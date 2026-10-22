"""
tools/bench_llm.py - local LLM benchmark for MBUDDY's voice-companion use.

For each Ollama model, measured on THIS machine (nothing estimated):
- cold load time and GPU memory once loaded
- time to first token (streaming), which decides whether a spoken reply feels
  instant, and generation speed in tokens per second
- tool calling: does it pick the right tool with the right arguments, and
  does it stay out of tools for plain chat?
- whether any reasoning ("thinking") leaks into the visible reply
- a few conversational replies, printed for a person to judge tone

Thinking is switched off (`think: false`): hybrid models such as qwen3:1.7b
honour it, instruct-only models ignore it.

Usage:
    .venv/Scripts/python.exe tools/bench_llm.py qwen3:4b-instruct qwen3:1.7b
Writes docs/audit/bench/llm_results.json.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "audit" / "bench" / "llm_results.json"
OLLAMA = "http://127.0.0.1:11434/api/chat"
SYSTEM = (
    "You are MBUDDY, a warm, playful desktop companion who speaks out loud. "
    "Reply in 1-2 short spoken sentences. Use a tool only when the user asks "
    "you to do something a tool can do."
)


def tool(tool_name: str, desc: str, /, **params: str) -> dict:
    props = {k: {"type": "string" if v != "number" else "number", "description": k} for k, v in params.items()}
    return {"type": "function", "function": {
        "name": tool_name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(params)},
    }}


TOOLS = [
    tool("set_timer", "Start a countdown timer.", minutes="number"),
    tool("cancel_timer", "Cancel the running timer."),
    tool("get_time", "Get the current local time and date."),
    tool("add_reminder", "Remind the user about something at a time.", text="string", time="string"),
    tool("add_task", "Add an item to the user's task list.", title="string"),
    tool("set_volume", "Set the PC's output volume in percent.", level="number"),
    tool("get_weather", "Get the weather forecast for a place.", location="string"),
    tool("open_app", "Open an application on this PC.", name="string"),
]

# (prompt, expected tool or None, {arg: regex the value must match})
TOOL_CASES = [
    ("Set a timer for 12 minutes.", "set_timer", {"minutes": r"^12(\.0)?$"}),
    ("What time is it?", "get_time", {}),
    ("Remind me to call my mom at 5:30 pm.", "add_reminder", {"text": r"(?i)mom", "time": r"(?i)5[:.]?30|17[:.]?30"}),
    ("Turn the volume down to 30 percent.", "set_volume", {"level": r"^30(\.0)?$"}),
    ("What's the weather in Beirut tomorrow?", "get_weather", {"location": r"(?i)beirut"}),
    ("Add milk and eggs to my task list.", "add_task", {"title": r"(?i)milk"}),
    ("Open Visual Studio Code.", "open_app", {"name": r"(?i)code|visual studio"}),
    ("Cancel my timer.", "cancel_timer", {}),
    ("How are you doing today?", None, {}),
    ("Tell me a quick joke.", None, {}),
    ("I just finished a long workout and I'm exhausted.", None, {}),
]

CHAT_PROMPTS = [
    "Hey buddy! I finally fixed that bug I was stuck on all day.",
    "I'm kind of nervous about my presentation tomorrow.",
    "What should I name my new plant?",
]


def vram_mb() -> float | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return float(out.splitlines()[0])
    except Exception:
        return None


def chat(model: str, prompt: str, tools: list | None = None, timeout: float = 180) -> dict:
    body = {"model": model, "stream": False, "think": False,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0.3, "num_predict": 200}}
    if tools:
        body["tools"] = tools
    t = time.perf_counter()
    r = requests.post(OLLAMA, json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    data["_s"] = time.perf_counter() - t
    return data


def stream_ttft(model: str, prompt: str) -> tuple[float, float | None, str]:
    """Seconds to first visible token, tokens/s, and the full reply."""
    body = {"model": model, "stream": True, "think": False,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0.7, "num_predict": 200}}
    t = time.perf_counter()
    first, text, tps = None, "", None
    with requests.post(OLLAMA, json=body, stream=True, timeout=180) as r:
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            piece = d.get("message", {}).get("content", "")
            if piece and first is None and piece.strip():
                first = time.perf_counter() - t
            text += piece
            if d.get("done") and d.get("eval_count") and d.get("eval_duration"):
                tps = d["eval_count"] / (d["eval_duration"] / 1e9)
    return (first if first is not None else float("nan")), tps, text.strip()


def leaks_reasoning(text: str) -> bool:
    return "<think>" in text or "</think>" in text


def score_tool_case(data: dict, expected: str | None, arg_rules: dict) -> tuple[bool, str]:
    calls = data.get("message", {}).get("tool_calls") or []
    if expected is None:
        return (not calls, "no tool" if not calls else f"called {calls[0]['function']['name']} unasked")
    if not calls:
        return False, "no tool call"
    fn = calls[0]["function"]
    if fn["name"] != expected:
        return False, f"wrong tool {fn['name']}"
    args = fn.get("arguments") or {}
    for key, pattern in arg_rules.items():
        if not re.search(pattern, str(args.get(key, ""))):
            return False, f"bad {key}={args.get(key)!r}"
    return True, f"{fn['name']}({json.dumps(args)})"


def bench(model: str) -> dict:
    res: dict = {"model": model}
    requests.post(OLLAMA, json={"model": model, "messages": [], "keep_alive": 0}, timeout=30)
    time.sleep(1)
    base = vram_mb()
    t = time.perf_counter()
    chat(model, "Say OK.")
    res["cold_load_s"] = round(time.perf_counter() - t, 2)
    loaded = vram_mb()
    res["vram_mb"] = round(loaded - base) if loaded is not None and base is not None else None

    ttfts, tpss, replies = [], [], []
    for p in CHAT_PROMPTS:
        ttft, tps, text = stream_ttft(model, p)
        ttfts.append(ttft)
        if tps:
            tpss.append(tps)
        replies.append({"prompt": p, "reply": text, "ttft_s": round(ttft, 3), "leak": leaks_reasoning(text)})
    res["ttft_s_mean"] = round(sum(ttfts) / len(ttfts), 3)
    res["tokens_per_s"] = round(sum(tpss) / len(tpss), 1) if tpss else None
    res["chat"] = replies

    rows, correct = [], 0
    for prompt, expected, rules in TOOL_CASES:
        try:
            data = chat(model, prompt, tools=TOOLS)
            ok, note = score_tool_case(data, expected, rules)
            leak = leaks_reasoning(data.get("message", {}).get("content", ""))
            rows.append({"prompt": prompt, "expected": expected, "ok": ok, "note": note, "s": round(data["_s"], 2), "leak": leak})
        except Exception as exc:
            ok = False
            rows.append({"prompt": prompt, "expected": expected, "ok": False, "note": f"error {exc}"})
        correct += ok
    res["tool_accuracy"] = f"{correct}/{len(TOOL_CASES)}"
    res["tool_cases"] = rows
    res["reasoning_leaks"] = sum(r.get("leak", False) for r in rows + replies)
    return res


def main(models: list[str]) -> None:
    results = []
    for m in models:
        print(f"\n=== {m} ===", flush=True)
        r = bench(m)
        results.append(r)
        print(f"  cold load {r['cold_load_s']}s  vram +{r['vram_mb']} MB  first token {r['ttft_s_mean']}s  "
              f"{r['tokens_per_s']} tok/s  tools {r['tool_accuracy']}  reasoning leaks {r['reasoning_leaks']}")
        for row in r["tool_cases"]:
            print(f"    {'ok ' if row['ok'] else 'BAD'} {row['prompt']!r:55s} {row['note']}")
        for c in r["chat"]:
            print(f"    chat: {c['prompt']!r}\n          -> {c['reply']!r}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1:] or ["qwen3:4b-instruct", "qwen3:1.7b"])
