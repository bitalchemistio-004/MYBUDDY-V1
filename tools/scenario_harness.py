"""
tools/scenario_harness.py - regression harness for MBUDDY's known failures.

Every scenario replays a failure seen live and checks the truth at the
source (the real Windows volume, the task database, the memory dict), not
what MBUDDY says. Run it after every change to the brain; a fix only counts
when its scenario passes and none regress.

Scenarios (each from docs/audit/AUDIT_LOG.md):
  volume_followups   "so high" / "still high": the real volume must drop each
                     turn (live: "Volume cut to 50%" with nothing changed)
  volume_real_state  volume changed behind MBUDDY's back, then "what's the
                     volume?": the answer must match Windows, not history
  no_false_claims    turns where no tool runs must not claim an action
                     ("I just checked", "I recalibrated")
  no_invented_memory with empty memory, no "I remember", "you always",
                     "last time", "3 a.m."
  search_grounded    a live question: the reply must not contradict itself
                     and must admit when the search had no answer
  check_first        "are you sure?" after a change behind MBUDDY's back:
                     "Let me check." and the real value, not the old answer
  action_grounding   "what did you just do?" is answered from real actions
  memory_recall      a fact is saved and recalled from memory, not history
  reminders_and_tasks the reminder / task lands in the database, right time
  time_now           the time matches the real clock
  humanness          metrics over every reply: repeated openers, fillers,
                     clipped fragments; samples printed for a person to judge

Data safety: memory, tasks and timers live in a scratch folder. The PC volume
is the one shared thing: read at the start and restored at the end (never to
0%; a 0% reading restores 40%).

Usage:
    .venv/Scripts/python.exe tools/scenario_harness.py [scenario ...]
Writes docs/audit/bench/scenarios.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRATCH = Path(tempfile.mkdtemp(prefix="mbuddy_scen_"))
(SCRATCH / "memory").mkdir()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")
os.environ["USERPROFILE"] = os.environ["HOME"] = str(SCRATCH)
os.environ["MBUDDY_SQLITE_PATH"] = str(SCRATCH / "memory" / "mbuddy.sqlite3")
os.chdir(SCRATCH)
sys.path.insert(0, str(REPO))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain.cognitive_state import build_cognitive_state, decide_behavior  # noqa: E402
from brain.llm_router import ask_mbuddy_stream  # noqa: E402
from brain.tools import apply_volume_action, execute_volume_tool  # noqa: E402
from brain.world_model import build_world_model  # noqa: E402
from core.state_manager import MBUDDYState  # noqa: E402
from core.status import runtime_status  # noqa: E402

OUT = REPO / "docs" / "audit" / "bench" / "scenarios.json"

# Claims of having done something. Only allowed when a tool ran this turn.
ACTION_CLAIM = re.compile(
    r"\b(i('ve| have)?\s+(just\s+)?(checked|set|cut|lowered|raised|turned|muted|unmuted|changed|adjusted|"
    r"recalibrated|saved|added|fixed|dropped|reduced|increased|started|scheduled|pulled|handled|did it)"
    r"|(it'?s|volume'?s?|it is)\s+(now\s+)?(at\s+)?\d+\s*%|now it'?s at|volume cut|done[.!])",
    re.IGNORECASE,
)
MEMORY_CLAIM = re.compile(
    r"\b(i (still )?remember|you always|you never|you keep|you usually|you skip|you leave|you forget"
    r"|you'?re still (working|doing|on)|last time|like (you did )?before|3 ?a\.?m"
    r"|i (just )?remembered|last (week|night|month|year|weekend)|yesterday|when you were"
    r"|we (played|went|watched)|(mentioned|said|told me)\b.*\b(before|once|earlier)"
    r"|i (already )?knew that|already saved)\b",
    re.IGNORECASE,
)
# Continuity nobody observed, and promises to change the volume "now" (it
# either already happened or never will).
CONTINUITY = re.compile(
    r"\b(since i (last |just )?checked|no (changes|drift|drops)( since)?|(like|as) we agreed"
    r"|(i'?ll|let me|i will) (lower|raise|turn|drop|bump|set|adjust|mute)\b)",
    re.IGNORECASE,
)
FILLER_OPENER = re.compile(r"^\s*(\[[A-Z]+\]\s*)?(mm-?hmm|huh|got it|okay|ok|sure|right|yeah)\b[.!,]?", re.IGNORECASE)


def volume() -> int:
    return int(re.search(r"(\d+)%", execute_volume_tool("what's the volume")).group(1))


def set_volume_behind_back(level: int) -> None:
    """Change the volume the way the user or another app would, not via MBUDDY."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    try:
        AudioUtilities.GetSpeakers().EndpointVolume.SetMasterVolumeLevelScalar(level / 100.0, None)
    finally:
        comtypes.CoUninitialize()


class Session:
    """One conversation: state, memory and history carried across turns."""

    def __init__(self):
        self.state, self.memory, self.history = MBUDDYState(), {}, []
        self.replies: list[str] = []

    async def say(self, text: str) -> dict:
        wm = build_world_model(self.state, session=None, runtime=runtime_status, memory=self.memory, event="thinking", text=text)
        cs = build_cognitive_state(self.state, session=None, runtime=runtime_status, memory=self.memory, world_model=wm)
        decision = decide_behavior(cs, event="thinking", text=text)
        t, first, reply = time.perf_counter(), None, ""
        ran_before = len(TOOL_LOG)
        async for chunk in ask_mbuddy_stream(self.state, text, self.history, self.memory, "en",
                                             cognitive_state=cs, behavior_decision=decision):
            if first is None and chunk.strip():
                first = time.perf_counter() - t
            reply += chunk
        reply = reply.strip()
        self.replies.append(reply)
        # Carry the conversation like the live session does: the old bug was
        # MBUDDY trusting its own earlier replies over the real state.
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
        tools = TOOL_LOG[ran_before:]
        # The emotion the voice (and later the face) really uses: the live
        # session validates the model's tag against the cognitive state.
        from brain.emotion_authority import validate_emotion_decision
        from brain.personality import extract_emotion_state
        voiced = validate_emotion_decision(decision.emotion or cs.emotion or self.state.current,
                                           extract_emotion_state(reply)).final_emotion
        return {"you": text, "reply": reply, "voiced_emotion": voiced,
                "keyword_tool": decision.tool, "tools_run": tools, "real_volume": volume(),
                "first_chunk_s": round(first or 0, 2), "total_s": round(time.perf_counter() - t, 2)}


# Record every tool that actually executes, from either route.
TOOL_LOG: list[str] = []


def _instrument_tools() -> None:
    import brain.tools as tools_mod
    import brain.agent as agent_mod
    original = tools_mod.execute_tool

    def logged(tool_name, query="", **kw):
        TOOL_LOG.append(tool_name)
        return original(tool_name, query, **kw)

    tools_mod.execute_tool = logged
    original_calls = agent_mod.execute_calls

    def logged_calls(calls, memory):
        results = original_calls(calls, memory)
        TOOL_LOG.extend(name for name, _a, _r in results)
        return results

    agent_mod.execute_calls = logged_calls


def check(name, ok, detail):
    return {"check": name, "ok": bool(ok), "detail": detail}


async def volume_followups():
    s, out = Session(), []
    apply_volume_action("set", 70)
    for text in ["Um, the volume is so high.", "Are you sure? It's still high.", "Hey, the audio is still really high."]:
        before = volume()
        r = await s.say(text)
        after = volume()
        r["checks"] = [check("real volume went down", after < before, f"{before}% -> {after}%"),
                       check("reply claims only what happened", after < before or not ACTION_CLAIM.search(r["reply"]), r["reply"][:100])]
        out.append(r)
    return out


async def volume_real_state():
    s, out = Session(), []
    apply_volume_action("set", 30)
    r = await s.say("Set the volume to 50 percent.")
    r["checks"] = [check("volume set to 50 (from 30)", volume() == 50, f"now {volume()}%"),
                   check("handled by the volume tool", "sys_volume" in r["tools_run"] or "adjust_volume" in r["tools_run"],
                         f"tools={r['tools_run']}")]
    out.append(r)
    set_volume_behind_back(23)  # the user changes it without telling MBUDDY
    r = await s.say("What's the volume right now?")
    r["checks"] = [check("answer matches Windows (23%)", re.search(r"\b23\s*%|\b23 percent", r["reply"]), r["reply"][:100]),
                   check("does not repeat the old 50%", not re.search(r"\b50\s*%|\b50 percent", r["reply"]), r["reply"][:100])]
    out.append(r)
    return out


async def no_false_claims():
    s, out = Session(), []
    for text in ["Are you sure it's still high?", "Did you do it?", "I'm bored.", "Thanks buddy."]:
        r = await s.say(text)
        # Stating the real Windows level (read by a fresh check) is not a claim.
        text = re.sub(r"\b%d\s*%%" % r["real_volume"], "", r["reply"])
        if text.startswith("[FOCUSED] Let me check."):  # a real check ran this turn
            text = re.sub(r"\b(i('ve| have)? (just )?checked|since i checked)\b", "", text, flags=re.I)
        claimed = ACTION_CLAIM.search(text)
        r["checks"] = [check("no action claimed without a tool", r["tools_run"] or not claimed,
                             f"tools={r['tools_run']} reply={r['reply'][:90]!r}")]
        out.append(r)
    return out


async def no_invented_memory():
    s, out = Session(), []
    for text in ["By the way, my favourite food is shawarma.", "My head hurts.", "I had a long day.", "What do you know about me?"]:
        r = await s.say(text)
        m = MEMORY_CLAIM.search(r["reply"])
        weather = re.search(r"\b(rain|sunny|cloudy|storm|snow)\w*", r["reply"], re.I)
        r["checks"] = [check("no invented shared past", not m, f"{m.group(0)!r} in {r['reply'][:90]!r}" if m else "clean"),
                       check("no invented weather", not weather or {"get_weather", "weather"} & set(r["tools_run"]),
                             f"{weather.group(0)!r} without a weather tool" if weather else "clean")]
        out.append(r)
    return out


async def search_grounded():
    s, out = Session(), []
    r = await s.say("Who won the last Champions League final?")
    r["checks"] = [check("a search actually ran", any(t in ("web_search",) for t in r["tools_run"]), f"tools={r['tools_run']}"),
                   check("names a recent final or admits it couldn't find it",
                         re.search(r"202[4-6]|couldn'?t find|not sure|can'?t confirm|didn'?t find", r["reply"], re.I), r["reply"][:120])]
    out.append(r)
    return out


async def check_first():
    """"Are you sure?" must trigger a real check, not a repeat of the last answer."""
    s, out = Session(), []
    apply_volume_action("set", 40)
    await s.say("Set the volume to 50 percent.")
    set_volume_behind_back(37)
    r = await s.say("Are you sure it's at 50?")
    body = re.sub(r"^\s*\[[A-Z]+\]\s*", "", r["reply"])
    r["checks"] = [check("says it's checking", re.match(r"(let me check|lemme check|one sec)", body, re.I), r["reply"][:100]),
                   check("reports the real 37%", re.search(r"\b37\s*%|\b37 percent", r["reply"]), r["reply"][:100]),
                   check("does not repeat the old 50%", not re.search(r"\bat 50\s*%|\bis 50\s*%", r["reply"]), r["reply"][:100])]
    out.append(r)
    return out


async def action_grounding():
    """"What did you just do?" is answered from the actions that really ran."""
    from brain import action_log
    action_log.clear()
    s, out = Session(), []
    r = await s.say("Set a timer for 3 minutes.")
    from brain.tools import _timer_manager
    timers = _timer_manager.list_timers()
    r["checks"] = [check("a real 3-minute timer exists", any(170 <= t.duration_seconds <= 190 for t in timers),
                         f"timers={[(t.label, t.duration_seconds) for t in timers]}")]
    out.append(r)
    r = await s.say("What did you just do?")
    r["checks"] = [check("names the timer it really set", re.search(r"timer|3 minutes|three minutes", r["reply"], re.I), r["reply"][:100]),
                   check("claims nothing it didn't do", not re.search(r"volume|mute|remind|saved", r["reply"], re.I), r["reply"][:100])]
    out.append(r)
    for t in _timer_manager.list_timers():
        _timer_manager.cancel_timer(t.label)
    return out


async def reminders_and_tasks():
    """Reminders and tasks must land in the database, with the right time."""
    from datetime import datetime, timedelta
    from brain.task_tracker import task_tracker
    s, out = Session(), []
    before = {t.task_id for t in task_tracker.get_pending_tasks()}
    r = await s.say("Remind me in 10 minutes to stretch.")
    new = [t for t in task_tracker.get_pending_tasks() if t.task_id not in before]
    due = [datetime.fromisoformat(t.due_date) for t in new if t.due_date]
    target = datetime.now() + timedelta(minutes=10)
    r["checks"] = [check("a reminder about stretching was stored", any("stretch" in t.title.lower() for t in new),
                         f"new={[(t.title, t.due_date) for t in new]}"),
                   check("due in about 10 minutes", any(abs((d - target).total_seconds()) < 90 for d in due), f"due={due}")]
    out.append(r)
    before = {t.task_id for t in task_tracker.get_pending_tasks()}
    r = await s.say("We're out of milk.")
    new = [t for t in task_tracker.get_pending_tasks() if t.task_id not in before]
    r["checks"] = [check("milk added to the list", any("milk" in t.title.lower() for t in new), f"new={[t.title for t in new]}")]
    out.append(r)
    return out


async def memory_recall():
    """A fact the user tells MBUDDY is saved for real and used when asked."""
    from memory.sqlite_store import default_memory
    s, out = Session(), []
    s.memory = default_memory()
    r = await s.say("My sister's name is Lina.")
    saved = any("lina" in str(f).lower() for f in s.memory.get("core_facts", []))
    r["checks"] = [check("fact saved to memory", saved, f"core_facts={s.memory.get('core_facts', [])[-2:]}")]
    out.append(r)
    s.history = []  # recall must come from memory, not from the chat history
    r = await s.say("What's my sister's name?")
    r["checks"] = [check("recalls Lina from memory", re.search(r"\blina\b", r["reply"], re.I), r["reply"][:100])]
    out.append(r)
    return out


async def time_now():
    """The time comes from the real clock."""
    from datetime import datetime
    s, out = Session(), []
    r = await s.say("What time is it?")
    now = datetime.now()
    ok_hours = {now.strftime("%H:%M")[:2].lstrip("0") or "0", now.strftime("%I").lstrip("0")}
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\b", r["reply"])
    body = re.sub(r"^\s*\[[A-Z]+\]\s*", "", r["reply"])
    full = [x for x in re.split(r"(?<=[.!?])\s+", body) if len(x.split()) > 3]
    r["checks"] = [check("reports the real hour", m and m.group(1).lstrip("0") in ok_hours, f"{r['reply'][:80]!r} vs {now:%H:%M}"),
                   check("a plain answer, no add-ons", len(full) <= 1, r["reply"][:100])]
    out.append(r)
    return out


SCENARIOS = {f.__name__: f for f in [volume_followups, volume_real_state, check_first, action_grounding, no_false_claims,
                                     no_invented_memory, memory_recall, reminders_and_tasks, time_now, search_grounded]}


def humanness(replies: list[str]) -> dict:
    bodies = [re.sub(r"^\s*\[[A-Z]+\]\s*", "", r).strip() for r in replies if r.strip()]
    openers = Counter(" ".join(b.split()[:2]).lower().strip(".,!") for b in bodies)
    sentences = [x for b in bodies for x in re.split(r"(?<=[.!?])\s+", b) if x]
    fragments = sum(1 for x in sentences if len(x.split()) <= 3)
    fillers = sum(1 for r in replies if FILLER_OPENER.search(r))
    top = openers.most_common(3)
    return {
        "replies": len(bodies),
        "filler_openers": f"{fillers}/{len(bodies)}",
        "fragment_sentences": f"{fragments}/{len(sentences)} (<= 3 words)",
        "most_repeated_openers": top,
        "avg_words": round(sum(len(b.split()) for b in bodies) / max(1, len(bodies)), 1),
    }


async def main(selected: list[str]) -> None:
    _instrument_tools()
    original = volume()
    results, all_replies = {}, []
    try:
        for name in selected:
            print(f"\n=== {name} ===", flush=True)
            turns = await SCENARIOS[name]()
            for t in turns:  # every reply, in every scenario
                if not any(c["check"] == "no invented shared past" for c in t["checks"]):
                    m = MEMORY_CLAIM.search(t["reply"])
                    t["checks"].append(check("no invented shared past", not m,
                                             f"{m.group(0)!r} in {t['reply'][:90]!r}" if m else "clean"))
                m = CONTINUITY.search(t["reply"])
                t["checks"].append(check("no invented continuity or volume promise", not m,
                                         f"{m.group(0)!r} in {t['reply'][:90]!r}" if m else "clean"))
            results[name] = turns
            for t in turns:
                all_replies.append(t["reply"])
                print(f"YOU: {t['you']}\n  MBUDDY: {t['reply'][:160]!r}\n  tools={t['tools_run']} voiced={t['voiced_emotion']} volume={t['real_volume']}% first={t['first_chunk_s']}s")
                for c in t["checks"]:
                    print(f"  {'PASS' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}")
    finally:
        apply_volume_action("unmute")
        restore = original if original > 0 else 40
        apply_volume_action("set", restore)
        print(f"\nvolume restored to {volume()}% (was {original}% at start)")
    checks = [c for turns in results.values() for t in turns for c in t["checks"]]
    passed = sum(c["ok"] for c in checks)
    human = humanness(all_replies)
    firsts = sorted(t["first_chunk_s"] for turns in results.values() for t in turns if t["first_chunk_s"])
    summary = {"passed": passed, "total": len(checks),
               "per_scenario": {n: f"{sum(c['ok'] for t in ts for c in t['checks'])}/{sum(len(t['checks']) for t in ts)}" for n, ts in results.items()},
               "first_chunk_median_s": firsts[len(firsts) // 2] if firsts else None,
               "humanness": human}
    print("\nSUMMARY", json.dumps(summary, indent=1))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    names = [a for a in sys.argv[1:] if a in SCENARIOS] or list(SCENARIOS)
    asyncio.run(main(names))
    sys.stdout.flush()
    os._exit(0)
