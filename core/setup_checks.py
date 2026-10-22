"""
core/setup_checks.py - installation checks shown on first run (and on the
Status screen).

Each check looks at the real thing (a file on disk, a server that answers,
a device that is connected) and says plainly what is wrong and how to fix
it. `required` checks must pass for MBUDDY to work; `recommended` ones
limit a feature when they fail.

Model pulls go through Ollama's own API (/api/pull) in the background, with
progress the app polls.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

ROOT = Path(__file__).resolve().parent.parent
OLLAMA = os.getenv("OLLAMA_HOST_URL", "http://127.0.0.1:11434")
MIN_FREE_GB = 2.0


def _result(check_id: str, title: str, ok: bool, level: str, detail: str, fix: str = "", action: str = "") -> dict:
    return {"id": check_id, "title": title, "ok": bool(ok), "level": level, "detail": detail,
            "fix": "" if ok else fix, "action": "" if ok else action}


def ollama_models() -> list[str] | None:
    """Installed Ollama model names, or None when Ollama doesn't answer."""
    try:
        data = requests.get(f"{OLLAMA}/api/tags", timeout=3).json()
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return None


def _model_installed(wanted: str, installed: list[str]) -> bool:
    return any(name == wanted or name == f"{wanted}:latest" for name in installed)


def check_gpu() -> dict:
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
    except Exception:
        count = 0
    return _result("gpu", "Graphics card", count > 0, "recommended",
                   f"NVIDIA GPU found ({count})." if count else "No NVIDIA GPU found.",
                   "MBUDDY still works on the processor, just slower. A lighter brain helps.")


def check_stt_model() -> dict:
    name = os.getenv("STT_MODEL", "small")
    hub = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    found = Path(name).exists() or any(hub.glob(f"models--*faster-whisper-{name}"))
    return _result("stt_model", "Hearing (speech recognition)", found, "recommended",
                   f"Whisper {name} is on this PC." if found else f"Whisper {name} isn't downloaded yet.",
                   "It downloads by itself the first time MBUDDY listens (about 460 MB).")


def check_tts_model() -> dict:
    folder = ROOT / "models" / "kokoro"
    files = ["kokoro-v1.0.onnx", "voices-v1.0.bin"]
    missing = [f for f in files if not (folder / f).exists()]
    return _result("tts_model", "Voice (speech)", not missing, "required",
                   "Kokoro voice is on this PC." if not missing else f"Missing: {', '.join(missing)}.",
                   f"Put kokoro-v1.0.onnx and voices-v1.0.bin (the kokoro-onnx model-files-v1.0 release) in {folder}.")


def check_ollama(installed: list[str] | None) -> dict:
    return _result("ollama", "Thinking engine (Ollama)", installed is not None, "required",
                   "Ollama is running." if installed is not None else "Ollama doesn't answer.",
                   "Install Ollama from ollama.com, then start it (it runs in the tray).")


def check_cloud_brain() -> list[dict]:
    """With the cloud brain, the thinking engine is a cloud model: it needs the key, the internet and a model on the plan."""
    from core import cloud
    ok_key = cloud.key_present()
    st = cloud.settings()
    return [
        _result("ollama", "Thinking engine (Ollama cloud)", ok_key, "required",
                f"Using {st['brain_model']} in Ollama's cloud with your key." if ok_key else "No Ollama cloud key is saved.",
                "Open Settings, Models, paste your key from ollama.com/settings/keys."),
        _result("chat_model", "Brain model", ok_key and not cloud.check_usable("brain"), "required",
                f"{st['brain_model']} will answer." if ok_key else "Needs the key first.",
                cloud.check_usable("brain") or "Pick a model your plan allows in Settings, Models."),
    ]


def check_chat_model(installed: list[str] | None, wanted: str) -> dict:
    if installed is None:
        return _result("chat_model", "Brain model", False, "required",
                       f"Can't check {wanted}: Ollama isn't running.", "Start Ollama first.")
    ok = _model_installed(wanted, installed)
    return _result("chat_model", "Brain model", ok, "required",
                   f"{wanted} is installed." if ok else f"{wanted} isn't downloaded.",
                   f"Download {wanted} (about 2.5 GB, once).", action="pull")


def check_microphone(status: Any) -> dict:
    connected = getattr(status, "device_conn_state", "") in ("online", "degraded")
    has_mic = bool((getattr(status, "device_capabilities", {}) or {}).get("mic"))
    heard = float(getattr(status, "last_rms", 0.0) or 0.0) > 0
    ok = connected and has_mic
    detail = ("Microphone connected" + (" and hearing sound." if heard else "; say something to test it.")
              if ok else "The microphone isn't connected yet.")
    return _result("microphone", "Microphone", ok, "required", detail,
                   "Allow microphone access when Windows asks, and make sure a microphone is plugged in.")


def check_disk() -> dict:
    free_gb = shutil.disk_usage(Path.home()).free / 1e9
    ok = free_gb >= MIN_FREE_GB
    return _result("disk", "Free disk space", ok, "recommended", f"{free_gb:.1f} GB free.",
                   f"Free up at least {MIN_FREE_GB:.0f} GB for memories and updates.")


def check_internet() -> dict:
    try:
        requests.head("https://duckduckgo.com", timeout=3)
        ok = True
    except Exception:
        ok = False
    return _result("internet", "Internet", ok, "recommended",
                   "Online." if ok else "No internet connection.",
                   "Everything works offline except web search and weather.")


def check_expressions() -> dict:
    clips = list((ROOT / "models" / "expressions").glob("*_[0-9]*.wav"))
    return _result("expressions", "Laughs and sighs", bool(clips), "recommended",
                   f"{len(clips)} expression sounds." if clips else "No expression sounds found.",
                   "MBUDDY will skip laughs and sighs until they're installed.")


def run_all(status: Any, wanted_model: str) -> dict:
    from core import cloud_brain
    cloud_mode = cloud_brain.active or cloud_brain.wanted()
    installed = None if cloud_mode else ollama_models()
    checks = [
        _result("backend", "MBUDDY core", True, "required", "Running."),
        *(check_cloud_brain() if cloud_mode else [check_ollama(installed), check_chat_model(installed, wanted_model)]),
        check_tts_model(),
        check_stt_model(),
        check_microphone(status),
        check_gpu(),
        check_disk(),
        check_internet(),
        check_expressions(),
    ]
    ready = all(c["ok"] for c in checks if c["level"] == "required")
    return {"ready": ready, "checks": checks, "models": installed or []}


# ── model pull (background, progress polled by the app) ───────────────────
_pull: dict[str, Any] = {"model": "", "state": "idle", "completed": 0, "total": 0, "status": "", "error": ""}
_pull_lock = threading.Lock()


def pull_progress() -> dict:
    with _pull_lock:
        return dict(_pull)


def start_pull(model: str, post: Callable = requests.post) -> dict:
    """Start downloading `model` through Ollama; returns the progress record."""
    with _pull_lock:
        if _pull["state"] == "running":
            return dict(_pull)
        _pull.update(model=model, state="running", completed=0, total=0, status="starting", error="")

    def run() -> None:
        import json
        try:
            with post(f"{OLLAMA}/api/pull", json={"model": model, "stream": True}, stream=True, timeout=(5, 600)) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    with _pull_lock:
                        if msg.get("error"):
                            _pull.update(state="error", error=str(msg["error"]))
                            return
                        _pull["status"] = msg.get("status", "")
                        if msg.get("total"):
                            _pull.update(total=int(msg["total"]), completed=int(msg.get("completed", 0)))
            with _pull_lock:
                _pull.update(state="done", status="success")
        except Exception as exc:
            with _pull_lock:
                _pull.update(state="error", error=f"{type(exc).__name__}: {exc}"[:200])

    threading.Thread(target=run, daemon=True, name="ollama-pull").start()
    time.sleep(0)  # let the thread start
    return pull_progress()
