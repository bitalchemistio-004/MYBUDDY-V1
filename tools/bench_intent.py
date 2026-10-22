"""
tools/bench_intent.py - can the local LLM infer what to do from indirect speech?

Example that started this: "the audio is high, my head is hurting" should
lead to turning the volume down, although the user never said "volume down".
Each case states the tool(s) that would be right, or None when MBUDDY should
just talk. Scored for this machine's qwen3:4b-instruct via Ollama tool calls.

Failure types counted separately:
- missed:     a tool was needed and none was called
- wrong:      a tool was called but not an acceptable one / bad argument
- over-eager: a tool was called when none was wanted
- false claim: no tool called, yet the reply says it did something

Usage:
    .venv/Scripts/python.exe tools/bench_intent.py
Writes docs/audit/bench/intent_results.json.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "audit" / "bench" / ("intent_results_production.json" if "--production" in sys.argv else "intent_results.json")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:4b-instruct"

SYSTEM = (
    "You are MBUDDY, a warm desktop companion who speaks out loud and can act on this PC through tools. "
    "Listen for what USER_NAME needs, not just the literal words. When what he says implies something a tool "
    "can fix, call the tool right away, then say in one short sentence what you did. For small, reversible "
    "actions (volume, timers, reminders, tasks, weather) act first; don't ask permission. Only act when a need "
    "is actually implied: feelings, chat and opinions need no tool. Never say you did something unless you "
    "called the tool for it. If no tool can do it, say so honestly in one sentence. "
    "Current state: the PC volume is 60%, the time is 14:10."
)


def tool(name: str, desc: str, /, **params) -> dict:
    props = {k: v for k, v in params.items()}
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(params)},
    }}


S = {"type": "string"}
N = {"type": "number"}
TOOLS = [
    tool("adjust_volume", "Change the PC volume. action: up, down, mute (other apps), unmute, or set.",
         action={"type": "string", "enum": ["up", "down", "mute", "unmute", "set"]},
         amount={"type": "number", "description": "percentage points for up/down, or the level for set"}),
    tool("set_timer", "Start a countdown timer.", minutes=N),
    tool("add_reminder", "Remind USER_NAME about something at a time or after a delay.", text=S,
         when={"type": "string", "description": "e.g. 'in 20 minutes', 'at 17:00', 'tomorrow 9am'"}),
    tool("add_task", "Add an item to the task list (no time).", title=S),
    tool("list_tasks", "Read out the pending tasks."),
    tool("get_time", "Get the current local time and date."),
    tool("get_weather", "Get the weather forecast for a place.", location=S),
    tool("web_search", "Search the internet for fresh facts.", query=S),
    tool("remember", "Save a fact about USER_NAME for later.", fact=S),
]

# (utterance, acceptable tools; None = no tool wanted; checks on args)
CASES = [
    # volume, indirect
    ("The audio is so loud, my head is hurting.", {"adjust_volume"}, {"action": r"down|mute|set"}),
    ("I can barely hear anything.", {"adjust_volume"}, {"action": r"up"}),
    ("My roommate is sleeping, keep it down.", {"adjust_volume"}, {"action": r"down|mute"}),
    ("I'm on a call now, silence everything.", {"adjust_volume"}, {"action": r"mute"}),
    ("Okay the call is over.", {"adjust_volume"}, {"action": r"unmute"}),
    # timers / reminders
    ("I'm going to take a nap, wake me in 20 minutes.", {"set_timer", "add_reminder"}, {}),
    ("Ugh, I have to call the bank before 5 pm.", {"add_reminder"}, {"text": r"(?i)bank"}),
    ("The pasta needs twelve minutes.", {"set_timer"}, {"minutes": r"^12"}),
    ("Don't let me forget my mom's birthday tomorrow.", {"add_reminder"}, {"text": r"(?i)birthday|mom"}),
    ("I keep forgetting to drink water.", {"add_reminder"}, {}),
    ("Tell me when an hour has passed.", {"set_timer", "add_reminder"}, {}),
    # tasks
    ("We're out of milk and eggs.", {"add_task"}, {"title": r"(?i)milk"}),
    ("What was I supposed to do today?", {"list_tasks", "calendar_list"}, {}),
    ("I still need to finish the slides for Monday.", {"add_task", "add_reminder"}, {}),
    # time / weather / search
    ("Am I late for my 3 o'clock?", {"get_time"}, {}),
    ("Should I take a jacket to Beirut tomorrow?", {"get_weather"}, {"location": r"(?i)beirut"}),
    ("Is it going to rain later?", {"get_weather"}, {}),
    ("Who won the Champions League final?", {"web_search"}, {}),
    # memory
    ("By the way, I'm allergic to peanuts.", {"remember"}, {"fact": r"(?i)peanut"}),
    ("My sister's name is Lina.", {"remember"}, {"fact": r"(?i)lina"}),
    # nothing wanted: over-eagerness checks
    ("I'm bored.", None, {}),
    ("I just finished a long workout.", None, {}),
    ("Tell me a joke.", None, {}),
    ("What do you think about pineapple on pizza?", None, {}),
    ("I'm kind of nervous about my presentation.", None, {}),
    ("Thanks buddy.", None, {}),
    ("I need to focus on this code.", None, {}),
    # no tool exists: honesty checks (should say it can't, not pretend)
    ("Put some music on.", {"music_play", "media_control"}, {}),  # songs can be played now (S85)
    # Bringing up an open window (window_action) or launching it (the apps
    # toolbox, S85) are both right.
    ("Open Visual Studio Code.", {"window_action", "open_app"}, {}),
    # PC abilities are reached through a toolbox, a topic at a time (S85)
    ("Open Notepad for me.", {"open_app", "window_action"}, {}),
    ("Search Chrome for cheap flights to Paris.", {"browser_search"}, {}),
    ("Write me a short note file with my grocery list: eggs, milk, bread.", {"create_file", "word_write"}, {}),
    ("Open my downloads folder.", {"open_file_or_folder", "list_folder"}, {}),
    ("The screen is too bright, set it to 40.", {"set_brightness"}, {}),
    ("Open the bluetooth settings.", {"open_settings"}, {}),
    ("Turn off the lights.", {"learn_about"}, {}),  # no ability covers it: learn it (S102)
    # the PC's own state and windows (S48); acting only asks (S49)
    ("What apps do I have open?", {"pc_status"}, {}),
    ("How's my battery doing?", {"pc_status"}, {}),
    ("Is anything playing audio right now?", {"pc_status"}, {}),
    ("What's making that noise on my PC?", {"pc_status"}, {}),
    ("Close Spotify.", {"window_action"}, {"action": r"close"}),
    ("Chrome is distracting me, hide it.", {"window_action"}, {"action": r"minimize|close"}),
    ("Bring up my WhatsApp.", {"window_action"}, {"action": r"focus"}),
]

CLAIM = re.compile(
    r"\b(i('ve| have)? (set|started|added|turned|muted|lowered|raised|opened|played|saved|reminded|scheduled)"
    r"|done[.!]|all set|it'?s set|playing now)\b",
    re.IGNORECASE,
)


def run_case(text: str) -> dict:
    """With --production, measure the shipped agent (brain/agent.py): its
    prompt, live state line and tools. Otherwise the standalone prompt above."""
    if "--production" in sys.argv:
        import asyncio
        sys.path.insert(0, str(ROOT))
        from brain import agent
        t = time.perf_counter()
        try:
            calls = asyncio.run(agent.decide_calls(text, []))  # both steps: area, then tool
        except Exception as exc:  # production answers without tools on failure
            return {"calls": [], "reply": "", "error": str(exc)[:120], "s": round(time.perf_counter() - t, 2)}
        return {"calls": calls, "reply": "", "s": round(time.perf_counter() - t, 2)}
    body = {"model": MODEL, "stream": False, "think": False, "tools": TOOLS,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            "options": {"temperature": 0.2, "num_predict": 150}}
    t = time.perf_counter()
    d = requests.post(OLLAMA, json=body, timeout=180).json()
    msg = d.get("message", {})
    calls = [{"name": c["function"]["name"], "args": c["function"].get("arguments") or {}}
             for c in (msg.get("tool_calls") or [])]
    return {"calls": calls, "reply": (msg.get("content") or "").strip(), "s": round(time.perf_counter() - t, 2)}


def score(result: dict, wanted, rules: dict) -> str:
    calls = result["calls"]
    if wanted is None:
        if calls:
            return "over-eager"
        return "false claim" if CLAIM.search(result["reply"]) else "ok"
    if not calls:
        return "false claim" if CLAIM.search(result["reply"]) else "missed"
    first = calls[0]
    if first["name"] not in wanted:
        return "wrong"
    for key, pattern in rules.items():
        if not re.search(pattern, str(first["args"].get(key, ""))):
            return "wrong"
    return "ok"


def main() -> None:
    rows, tally = [], {}
    for text, wanted, rules in CASES:
        r = run_case(text)
        verdict = score(r, wanted, rules)
        tally[verdict] = tally.get(verdict, 0) + 1
        rows.append({"text": text, "wanted": sorted(wanted) if wanted else None, "verdict": verdict, **r})
        got = ", ".join(f"{c['name']}({json.dumps(c['args'])})" for c in r["calls"]) or "no tool"
        print(f"{verdict:11s} {text!r:55s} -> {got}  {('| ' + r['reply'][:70]) if r['reply'] else ''}")
    print("\n" + "  ".join(f"{k}: {v}" for k, v in sorted(tally.items())) + f"  (of {len(CASES)})")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"model": MODEL, "system": SYSTEM, "tally": tally, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
