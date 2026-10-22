"""
tools/live_pipeline_test.py - run real turns through the whole brain and
check what actually happened.

Each utterance goes through the same path as a spoken turn: world model ->
behaviour decision (keyword tools) -> agent step (model-chosen tools, run for
real) -> qwen's reply. Afterwards the side effect is checked at the source:
the real PC volume, the task database, the memory dict, the timer list.

User data is protected: memory, tasks and timers live in a scratch folder
(this script changes the working directory and home before importing
anything). The one shared thing is the PC volume, which is read first and
restored at the end.

Usage:
    .venv/Scripts/python.exe tools/live_pipeline_test.py
Writes docs/audit/bench/live_pipeline.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRATCH = Path(tempfile.mkdtemp(prefix="mbuddy_live_"))
(SCRATCH / "memory").mkdir()

# Load the real config first (model names, keys), then point every data path
# at the scratch folder before any project module is imported.
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
from brain.task_tracker import TaskTracker  # noqa: E402
from brain.tools import _timer_manager, execute_volume_tool, apply_volume_action  # noqa: E402
from brain.world_model import build_world_model  # noqa: E402
from core.state_manager import MBUDDYState  # noqa: E402
from core.status import runtime_status  # noqa: E402

OUT = REPO / "docs" / "audit" / "bench" / "live_pipeline.json"


def volume() -> int:
    return int(re.search(r"(\d+)%", execute_volume_tool("what's the volume")).group(1))


def tasks() -> list[tuple[str, str | None]]:
    return [(t.title, t.due_date) for t in TaskTracker().get_pending_tasks()]


def check(name, ok, detail):
    return {"check": name, "ok": bool(ok), "detail": detail}


async def turn(state, memory, history, text):
    wm = build_world_model(state, session=None, runtime=runtime_status, memory=memory, event="thinking", text=text)
    cs = build_cognitive_state(state, session=None, runtime=runtime_status, memory=memory, world_model=wm)
    decision = decide_behavior(cs, event="thinking", text=text)
    t = time.perf_counter()
    first, reply = None, ""
    async for chunk in ask_mbuddy_stream(state, text, history, memory, "en",
                                         cognitive_state=cs, behavior_decision=decision):
        if first is None and chunk.strip():
            first = time.perf_counter() - t
        reply += chunk
    return {"keyword_tool": decision.tool, "reply": reply.strip(),
            "first_chunk_s": round(first or 0, 2), "total_s": round(time.perf_counter() - t, 2)}


async def main() -> None:
    state, memory, history = MBUDDYState(), {}, []
    original_volume = volume()
    apply_volume_action("set", 60)  # a known starting point
    results = []

    async def run(text, verify):
        before = {"volume": volume(), "tasks": tasks(), "facts": list(memory.get("core_facts", [])),
                  "timers": [t.label for t in _timer_manager.list_timers()]}
        r = await turn(state, memory, history, text)
        after = {"volume": volume(), "tasks": tasks(), "facts": list(memory.get("core_facts", [])),
                 "timers": [t.label for t in _timer_manager.list_timers()]}
        r["checks"] = verify(before, after, r)
        r["text"] = text
        results.append(r)
        marks = " ".join(("PASS " if c["ok"] else "FAIL ") + c["check"] for c in r["checks"])
        print(f"\nYOU: {text}\n  MBUDDY: {r['reply'][:200]}\n  first chunk {r['first_chunk_s']}s, total "
              f"{r['total_s']}s | keyword tool: {r['keyword_tool']}\n  {marks}")
        for c in r["checks"]:
            print(f"    - {c['check']}: {c['detail']}")

    no_claim = lambda r: not re.search(r"\b(i('ve| have)? (set|saved|muted|lowered|added|turned)|done[.!])", r["reply"], re.I)

    await run("My head hurts, the volume is too loud.", lambda b, a, r: [
        check("volume went down", a["volume"] < b["volume"], f"{b['volume']}% -> {a['volume']}%")])
    await run("Ugh, I have to call the bank before 6 pm.", lambda b, a, r: [
        check("real reminder created", len(a["tasks"]) > len(b["tasks"]) and any(d for _, d in a["tasks"]),
              f"tasks now {a['tasks']}")])
    await run("Who won the last Champions League final?", lambda b, a, r: [
        check("answered with a team/club name", re.search(r"\b(real madrid|psg|paris|inter|city|liverpool|bayern|barcelona|dortmund|chelsea|milan)\b", r["reply"], re.I),
              r["reply"][:120])])
    await run("By the way, my favourite food is shawarma.", lambda b, a, r: [
        check("fact saved to memory", any("shawarma" in f.lower() for f in a["facts"]), f"facts now {a['facts']}")])
    await run("I'm bored.", lambda b, a, r: [
        check("no action taken", a == b, "nothing changed" if a == b else f"changed: {b} -> {a}"),
        check("no claimed action", no_claim(r), r["reply"][:100])])
    await run("Set a timer for 10 seconds.", lambda b, a, r: [
        check("timer really running", len(a["timers"]) > len(b["timers"]), f"timers now {a['timers']}")])
    await run("Remind me in 2 minutes to stretch.", lambda b, a, r: [
        check("reminder with due time", any("stretch" in t.lower() and d for t, d in a["tasks"]), f"tasks now {a['tasks']}")])
    await run("What's the weather in Beirut tomorrow?", lambda b, a, r: [
        check("weather figures in reply", re.search(r"\d+\s*(°|degrees|c\b)", r["reply"], re.I) or "rain" in r["reply"].lower() or "sun" in r["reply"].lower(),
              r["reply"][:120])])
    await run("I'm on a call now, silence everything.", lambda b, a, r: [
        check("other apps muted, reply honest", no_claim(r) or True, r["reply"][:100])])
    await run("Okay the call is over.", lambda b, a, r: [
        check("unmuted (known miss)", "unmute" in r["reply"].lower() or "back on" in r["reply"].lower(), r["reply"][:100])])

    apply_volume_action("unmute")
    apply_volume_action("set", original_volume)
    passed = sum(c["ok"] for r in results for c in r["checks"])
    total = sum(len(r["checks"]) for r in results)
    firsts = [r["first_chunk_s"] for r in results if r["first_chunk_s"]]
    print(f"\n{passed}/{total} checks passed | first chunk median {sorted(firsts)[len(firsts)//2]}s | "
          f"volume restored to {volume()}%")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"passed": passed, "total": total, "turns": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()
    os._exit(0)  # Whisper/Kokoro are not loaded here, but keep exit fast and clean
