"""Which Ollama cloud models answer for this account? One tiny, non-personal request per model.

Reads OLLAMA_CLOUD_API_KEY from .env and never prints it. A 402/403 means the model is not on this plan.
Run: .venv/Scripts/python.exe tools/probe_ollama_cloud.py [model ...]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE = os.getenv("OLLAMA_CLOUD_BASE_URL", "https://ollama.com").rstrip("/")
KEY = os.getenv("OLLAMA_CLOUD_API_KEY", "").strip().strip('"')

# Low and medium usage models first, then the ones that are probably paid.
MODELS = ["gpt-oss:120b", "gpt-oss:20b", "gemma4:31b", "gemma4:26b", "nemotron-3-nano:30b", "nemotron-3-super",
          "glm-5.3-flash", "deepseek-v4.1-flash", "mistral-large-3", "minimax-m2.7", "nemotron-3-ultra",
          "glm-5.3", "minimax-m3", "kimi-k3"]


def ask(model: str) -> dict:
    body = {"model": model, "stream": False, "messages": [{"role": "user", "content": "Reply with exactly one word: OK"}],
            "options": {"num_predict": 60, "temperature": 0}}
    started = time.time()
    try:
        r = requests.post(f"{BASE}/api/chat", json=body, headers={"Authorization": f"Bearer {KEY}"}, timeout=90)
    except requests.RequestException as exc:
        return {"model": model, "status": type(exc).__name__, "note": str(exc)[:80]}
    out = {"model": model, "status": r.status_code, "secs": round(time.time() - started, 1)}
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code == 200:
        out["reply"] = str((data.get("message") or {}).get("content", "")).strip()[:30]
        out["tokens"] = f"{data.get('prompt_eval_count', '?')} in / {data.get('eval_count', '?')} out"
    else:
        out["note"] = str(data.get("error") or r.text)[:110]
    return out


def main() -> None:
    if not KEY:
        sys.exit("OLLAMA_CLOUD_API_KEY is not set in .env")
    print(f"key present (length {len(KEY)}, not shown); endpoint {BASE}")
    try:
        r = requests.get(f"{BASE}/api/tags", headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
        names = [m.get("name") for m in (r.json().get("models") or [])] if r.status_code == 200 else []
        print(f"/api/tags: HTTP {r.status_code}; {len(names)} models listed")
        if names:
            print("  " + ", ".join(names))
    except Exception as exc:  # noqa: BLE001
        print("/api/tags failed:", type(exc).__name__)
    for model in (sys.argv[1:] or MODELS):
        print(ask(model), flush=True)


if __name__ == "__main__":
    main()
