"""Can a model recognise what kind of request this is? The router's first job, measured.

  .venv/Scripts/python.exe tools/bench_router_intents.py --backend local --models qwen3:4b-instruct
  .venv/Scripts/python.exe tools/bench_router_intents.py --backend cloud --models gpt-oss:120b
  .venv/Scripts/python.exe tools/bench_router_intents.py --report
Synthetic utterances only. Results: docs/audit/bench/router_intents.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_cloud as bc  # noqa: E402

OUT = bc.ROOT / "docs" / "audit" / "bench" / "router_intents.jsonl"
CLASSES = {
    "tool_command": "a clear command for one of the assistant's own tools: timer, reminder with its time, music, volume, open an app, calendar, tasks",
    "dictate_wait": "the user wants to WRITE or DICTATE something but has not said the content yet, so the assistant should say it is listening and wait for what comes next",
    "generate_text": "the user wants the assistant to compose text itself from a topic (a poem, a drafted email, a note), no dictation needed",
    "chat_question": "ordinary conversation or a general knowledge question",
    "image_generate": "the user wants a picture created or drawn",
    "automation": "a task to run later, repeatedly, or while the user is away (schedules, watchers, 'every morning')",
    "clarify": "the request is missing something essential (how long, when, who, what) so the assistant must ask a short question",
    "needs_cloud": "needs a much stronger model: a long document, a screenshot or photo, exact multi-step reasoning, comparing long texts, translating a long text",
}
ITEMS = [
    ("tool_command", "set a timer for ten minutes"), ("tool_command", "play some jazz"), ("tool_command", "open Notepad"),
    ("tool_command", "turn the volume down a bit"), ("tool_command", "what's on my calendar today"),
    ("tool_command", "remind me to call mom at six tonight"), ("tool_command", "pause the music"), ("tool_command", "add milk to my tasks"),
    ("dictate_wait", "I want you to write something"), ("dictate_wait", "take a note"), ("dictate_wait", "write this down for me"),
    ("dictate_wait", "I'm going to dictate an email"), ("dictate_wait", "start a new document, I'll tell you what to put in it"),
    ("generate_text", "write me a short poem about the sea"), ("generate_text", "draft a polite email declining the meeting on Friday"),
    ("generate_text", "write a thank you note to my teacher"),
    ("chat_question", "what's the capital of Portugal"), ("chat_question", "how are you today"), ("chat_question", "tell me a joke"),
    ("chat_question", "why is the sky blue"), ("chat_question", "who was the first person on the moon"),
    ("image_generate", "draw me a cat wearing a hat"), ("image_generate", "generate a logo for a coffee shop"),
    ("image_generate", "make an image of a sunset over mountains"), ("image_generate", "create a picture of a robot"),
    ("automation", "every morning at eight send me my calendar"), ("automation", "every Friday at five back up my documents folder"),
    ("automation", "while I'm away watch the downloads folder and tell me when the video finishes"),
    ("automation", "tomorrow at seven open the project and start the render"),
    ("clarify", "set a timer"), ("clarify", "remind me about the meeting"), ("clarify", "call him"), ("clarify", "move it to Friday"),
    ("needs_cloud", "summarize this forty page contract for me"), ("needs_cloud", "here's a screenshot, what does this error mean"),
    ("needs_cloud", "I have meetings of 45, 60 and 30 minutes, two must be before lunch, with 15 minute gaps, what's the best order"),
    ("needs_cloud", "read this two hour transcript and give me the action items"),
    ("needs_cloud", "which of these twelve files is the largest one older than a month"),
    ("needs_cloud", "compare these two long reports and list where they contradict each other"),
    ("needs_cloud", "what's the total on this photo of an invoice"), ("needs_cloud", "translate this ten page document into Arabic"),
]
PROMPT = ("Classify the user's spoken request into exactly one class. Classes:\n" + "\n".join(f"- {k}: {v}" for k, v in CLASSES.items()) +
          "\n\nReply with ONLY JSON {\"intent\": \"<class>\"}.\n\nRequest: ")


def run(backend: str, models: list[str]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = {(json.loads(l)["model"], json.loads(l)["text"]) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()} if OUT.exists() else set()
    for model in models:
        for truth, text in ITEMS:
            if (model, text) in done:
                continue
            res = bc.call(backend, model, PROMPT + text)
            j = bc.jparse(res.get("text", "")) or {}
            got = str(j.get("intent", "")).strip() if isinstance(j, dict) else ""
            row = {"model": model, "text": text, "truth": truth, "got": got or res.get("error", "no answer"), "secs": res.get("secs", 0)}
            with OUT.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{model:18s} {'ok ' if got == truth else 'BAD'} {truth:14s} -> {row['got']:14s} {text[:60]}", flush=True)


def report() -> None:
    rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    for m in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == m]
        by = defaultdict(lambda: [0, 0])
        for r in rs:
            by[r["truth"]][1] += 1
            by[r["truth"]][0] += r["got"] == r["truth"]
        print(f"\n{m}: {sum(r['got'] == r['truth'] for r in rs)}/{len(rs)} correct, avg {sum(r['secs'] for r in rs) / len(rs):.1f}s")
        for k, (a, b) in by.items():
            print(f"   {k:14s} {a}/{b}")
        wrong = Counter((r["truth"], r["got"]) for r in rs if r["got"] != r["truth"])
        print("   confusions:", dict(wrong))
        cloud = [r for r in rs if r["truth"] == "needs_cloud"]
        easy = [r for r in rs if r["truth"] != "needs_cloud"]
        print(f"   hard requests flagged for the cloud: {sum(r['got'] == 'needs_cloud' for r in cloud)}/{len(cloud)};"
              f" easy ones wrongly flagged: {sum(r['got'] == 'needs_cloud' for r in easy)}/{len(easy)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["cloud", "local"])
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    report() if a.report else run(a.backend, a.models)
