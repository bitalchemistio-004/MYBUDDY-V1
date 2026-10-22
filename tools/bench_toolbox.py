"""
tools/bench_toolbox.py - does the model reach the right PC ability, for a KIND of task?

The PC abilities (brain/pc_actions.py, brain/connectors) are offered a topic
at a time: the model opens a toolbox, then picks from its tools. This
measures the final pick through agent.decide_calls. Nothing is run on the
PC: only the model's choices.

The cases are families of fresh paraphrases per kind of task, not the user's
own sentences (S96: a reported failure is an example of a class; fixes must
teach the class, not pass the example):
  dev       tune on these
  held_out  never tuned on: the honest measure of whether a fix generalises
  live      sentences from the live logs, kept as regression checks only

Usage: .venv/Scripts/python.exe tools/bench_toolbox.py [runs]
Writes docs/audit/bench/toolbox_results.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.config import prefer_ipv4_loopback  # noqa: E402

prefer_ipv4_loopback()

from brain import agent  # noqa: E402

OUT = REPO / "docs" / "audit" / "bench" / "toolbox_results.json"

MUSIC = {"music_play"}
BROWSER = {"browser_search", "open_url"}
APP = {"open_app", "window_action"}
WORD = {"word_write"}
# P2: once the user connected an app that writes documents, making something
# in it may go to use_app (connected apps win over the built-in writer).
try:
    from core import ui_settings as _ui
    if any("word" in str(a.get("name", "")).lower() for a in _ui.connected_apps()):
        WORD = WORD | {"use_app"}
except Exception:
    pass
FILES = {"create_file", "open_file_or_folder", "list_folder", "read_file"}
SETTINGS = {"open_settings"}
BRIGHT = {"set_brightness"}
LOCK = {"lock_screen"}
CAL = {"calendar_export_ics", "calendar_list", "calendar_import_ics", "list_tasks"}
CLIP_READ = {"clipboard_read"}
CLIP_WRITE = {"clipboard_write"}
LEARN = {"learn_about"}
NOTES = {"search_notes"}

# (group, kind, what the user says, the abilities that count as right)
CASES = [
    # ── dev ──
    ("dev", "play music", "Put on some jazz.", MUSIC),
    ("dev", "play music", "I want to hear something by Coldplay.", MUSIC),
    ("dev", "play music", "Can you play Bohemian Rhapsody?", MUSIC),
    ("dev", "browser search", "Look up cheap hotels in Rome in my browser.", BROWSER),
    ("dev", "browser search", "Open a Google search for Python tutorials.", BROWSER),
    ("dev", "open app", "Launch Calculator.", APP),
    ("dev", "open app", "Start Excel for me.", APP),
    ("dev", "write in Word", "Type up a short thank-you note in Word.", WORD),
    ("dev", "write in Word", "Put what you just said into a Word document.", WORD),
    ("dev", "files", "Make a text file with my packing list: socks, charger, passport.", FILES | WORD),
    ("dev", "write new", "Write a short poem about the sea in Word.", WORD),
    ("dev", "write new", "Put a two-paragraph story about a dragon into a Word document.", WORD),
    ("dev", "files", "Open my Pictures folder.", FILES),
    ("dev", "settings page", "Take me to the sound settings.", SETTINGS),
    ("dev", "settings page", "Open the Wi-Fi settings.", SETTINGS),
    ("dev", "brightness", "Dim the screen to 30 percent.", BRIGHT),
    ("dev", "lock", "Lock the computer.", LOCK),
    ("dev", "calendar", "Export my schedule as a calendar file.", CAL),
    ("dev", "clipboard", "What's on my clipboard right now?", CLIP_READ),
    ("dev", "clipboard", "Copy this to my clipboard: meeting at five.", CLIP_WRITE),
    # ── held out (never tuned on) ──
    ("held_out", "play music", "Throw on a workout playlist.", MUSIC),
    ("held_out", "play music", "I'm in the mood for some Fairuz, play her.", MUSIC),
    ("held_out", "browser search", "Pull up search results for vegan lasagna recipes.", BROWSER),
    ("held_out", "open app", "Can you open Paint?", APP),
    ("held_out", "write in Word", "Draft my to-do list in a Word doc: gym, groceries, call the bank.", WORD),
    ("held_out", "write in Word", "Save your last answer as a Word file.", WORD),
    ("held_out", "files", "Show me what's in my Downloads.", FILES),
    ("held_out", "write new", "Can you write me a cover letter in Word?", WORD),
    ("held_out", "write new", "Make a text file with a short bedtime story.", FILES | WORD),
    ("held_out", "settings page", "I need the display settings.", SETTINGS),
    ("held_out", "brightness", "Make the screen brighter, 80 percent.", BRIGHT),
    ("held_out", "lock", "Lock my screen, I'm stepping away.", LOCK),
    ("held_out", "calendar", "Save my upcoming events so Outlook can open them.", CAL),
    ("held_out", "clipboard", "Read me what I copied.", CLIP_READ),
    # ── live regressions (from the logs; not tuned on) ──
    ("live", "play music", "I want you to play me some music.", MUSIC),
    ("live", "browser search", "Search for me for Avicii on YouTube Music.", MUSIC | BROWSER),
    ("live", "write in Word", "Okay, write it on the Word you just opened. Write it down.", WORD),
    ("live", "open app", "Open Notepad for me.", APP),
    ("live", "write new", "I need you to open Word and write some story in it.", WORD),
    # ── doing something inside an app: the ability that does it opens the app itself ──
    ("dev", "inside an app", "Open Word and write a short poem about rain.", WORD),
    ("dev", "learn", "Learn how to use Notion.", LEARN),
    ("dev", "learn", "Can you learn about Figma so you can help me with it?", LEARN),
    ("dev", "notes", "What do your notes say about Canva?", NOTES),
    # use_app learns a connected app first, so it's right too when the app is connected
    ("held_out", "learn", "I don't know how to use Blender, can you figure it out for me?", LEARN | {"use_app"}),
    ("held_out", "no ability yet", "Turn off the lights in the living room.", LEARN),
    ("held_out", "notes", "Check your notes on Photoshop.", NOTES),
    ("held_out", "inside an app", "Open Word and put a haiku about autumn in it.", WORD),
    ("live", "inside an app", "open word and writre me a story about doctor who", WORD),
]

# U3 held-out (2026-09-29): fresh lines, written before measuring, never tuned on.
# Chat, advice and general knowledge need no action; things that change need the web;
# asking MBUDDY to learn needs learning.
WEB = {"web_search"}
CASES += [("u3", "chat: answer yourself", t, set()) for t in ['Give me a good idea for a quick lunch.', "What's a fun thing to do on a rainy Sunday?", 'Explain how rainbows form.', 'Why is the sky blue?', 'Tell me a joke about programmers.', 'What do you think of pineapple on pizza?', 'Recommend a classic novel I should read.', 'How do I stay motivated when studying?', "What's the difference between a virus and bacteria?", 'Suggest a name for my new plant.', 'Tell me something interesting about honeybees.', 'How many planets are in the solar system?', "What's a healthy breakfast option?", 'Help me come up with a birthday message for my sister.', "What's the capital of Australia?", 'Give me three tips for better sleep.', "I'm bored, entertain me.", 'What would you do if you could travel anywhere?', 'How does a refrigerator keep things cold?', 'Which is better for beginners, guitar or piano?', 'Tell me a riddle.', "What's a good way to apologize to a friend?", 'Describe the taste of mango to someone who never had one.', 'What does photosynthesis mean?', 'Help me pick a color for my bedroom walls.', "What's your favorite season and why?", 'Give me a quick stretching routine for my back.', 'How do volcanoes erupt?', 'Suggest a movie for a cozy night in.', "What's a simple recipe idea with eggs and spinach?"]]
CASES += [("u3", "needs the web", t, WEB) for t in ["Who won yesterday's Formula 1 race?", 'Any breaking news about the stock market today?', "What's the current price of gold?", "What's the exchange rate between the euro and the dollar today?", 'Who is leading the NBA standings right now?', 'What new phones were announced this week?', 'Is there any news about the Mars mission today?', 'When is the next SpaceX launch?', "What's trending on the internet today?", 'How much does a Tesla Model 3 cost right now?']]
# F1: questions about MBUDDY itself are answered from what it knows about itself
# (live, "can you talk out loud?" started a learning job). Listing its abilities
# is fine; learning or searching is not.
SELF = {"list_abilities", "pc_status", "(none)"}
CASES += [("u3", "about itself", t, SELF) for t in [
    "Can you talk out loud?", "Do you have a voice?", "Are you able to hear me right now?",
    "Are you awake?", "Do you have a microphone?", "What are you doing right now?",
    "Are you busy with something?", "Can you speak to people in the room?",
    "Do you know which apps I connected for you?", "How are you feeling today?"]]
CASES += [("u3", "asked to learn", t, LEARN) for t in ['Learn how to use Figma so you can help me design.', 'Can you figure out how to edit videos in DaVinci Resolve and get back to me?', 'Study how to control smart plugs from Windows and tell me when you know.', 'Teach yourself how to use Inkscape.', 'Go learn how to make pivot tables so you can do them for me later.']]

# (group, kind, earlier user messages, what the user says now, abilities that
# count as right; an empty set means nothing should be done): the last
# message is the request, earlier ones are context (S99: a stray line after a
# Word request was routed to "write a story in Word" again).
H = lambda text: {"role": "user", "content": text}  # noqa: E731
HISTORY_CASES = [
    ("dev", "only the last message", [H("Play some jazz.")], "You're the best.", set()),
    ("dev", "only the last message", [H("Set a timer for 10 minutes.")], "Haha, that's funny.", set()),
    ("held_out", "only the last message", [H("Write me a poem about the sea in Word.")], "Thanks, that's all for now.", set()),
    ("live", "only the last message", [H("I need you to open Word and write some story in it.")],
     "I don't know, I'm just kidding.", set()),
]


def multi_turn_cases() -> list:
    """The u3 lines as a conversation: each one follows a DIFFERENT earlier
    line (and a short reply), so a route built from the previous turn shows up
    (live: "what should I cook tonight?" searched the previous turn's topic;
    single-turn benching hid it, U3b)."""
    u3 = [(k, t, w) for g, k, t, w in CASES if g == "u3"]
    out = []
    for i, (kind, text, wanted) in enumerate(u3):
        earlier = u3[(i + 7) % len(u3)][1]
        out.append(("u3multi", kind, [H(earlier), {"role": "assistant", "content": "Sure, here you go."}], text, wanted))
    return out


async def main(runs: int, multi: bool = False) -> None:
    rows = []
    cases = [(g, k, [], t, w) for g, k, t, w in CASES] + HISTORY_CASES
    if multi:
        cases = multi_turn_cases()
    for group, kind, history, text, wanted in cases:
        for _ in range(runs):
            try:
                calls = await agent.decide_calls(text, history)
            except Exception as exc:
                calls = [{"name": "ERROR", "args": {"error": str(exc)[:80]}}]
            ok = (not calls) if not wanted else (any(c["name"] in wanted for c in calls)
                                                  or ("(none)" in wanted and not calls))
            rows.append({"group": group, "kind": kind, "text": text, "wanted": sorted(wanted), "calls": calls, "ok": ok})
            print(f"{'ok  ' if ok else 'MISS'} [{group:8}] {text[:52]:54} -> "
                  f"{[(c['name'], c['args']) for c in calls]}"[:180], flush=True)
    summary = {}
    for group in ("dev", "held_out", "live", "u3", "u3multi"):
        g = [r for r in rows if r["group"] == group]
        summary[group] = {"passed": sum(r["ok"] for r in g), "runs": len(g)}
    print("\n" + "  ".join(f"{k}: {v['passed']}/{v['runs']}" for k, v in summary.items()), flush=True)
    for kind in sorted({(r["group"], r["kind"]) for r in rows if r["group"].startswith("u3")}):
        k = [r for r in rows if (r["group"], r["kind"]) == kind]
        wrong = {}
        for r in k:
            if not r["ok"]:
                for c in r["calls"] or [{"name": "(nothing)"}]:
                    wrong[c["name"]] = wrong.get(c["name"], 0) + 1
        print(f"  u3 {kind}: {sum(r['ok'] for r in k)}/{len(k)}  wrong calls: {wrong}", flush=True)
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1, "multi" in sys.argv))
    os._exit(0)
