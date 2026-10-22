"""The local brain's server (Ollama): is it up, and start it if it isn't.

After a reboot the Ollama tray app didn't start its server, and a turn sat
~30 s in silence retrying before it spoke (M2, R4). The user approved
starting it on demand (2026-09-28): only `ollama serve`, windowless, only
when nothing listens and no Ollama process exists. Under the desktop shell it
runs inside MBUDDY's Windows job, so it ends when MBUDDY ends (U1). A running Ollama (the tray's,
or one that is slow to answer) is never touched, killed or doubled, and no
Windows setting, service, startup entry or environment variable is changed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import requests

import utils.logger as log

READY_WAIT_S = 30.0       # measured: the tray took 9.1 s to answer, a bare serve longer
_lock = threading.Lock()
needs_warm = False


def base_url() -> str:
    from brain.agent import _ollama_chat_url
    return _ollama_chat_url().split("/api/")[0]


def _port() -> int:
    from urllib.parse import urlparse
    return urlparse(base_url()).port or 11434


def listening(port: int) -> bool:
    """Is anything listening on this TCP port on this PC? Decided from the
    port table, not from how a connect fails: on Windows a connect to a closed
    loopback port is retried and only refused after ~2 s, so a 1 s probe saw
    a timeout and took "nothing there" for "busy" (S4 verification, B1)."""
    import psutil
    try:
        return any(c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port
                   for c in psutil.net_connections(kind="tcp"))
    except Exception:
        return True  # can't tell: assume something is there and never start a second one


def probe(timeout: float = 1.0, port: int | None = None) -> str:
    """"up", "refused" (nothing listening: safe to start it), or "busy"
    (something listens but doesn't answer yet: leave it alone)."""
    port = port or _port()
    if not listening(port):
        return "refused"
    try:
        url = base_url() if port == _port() else f"http://127.0.0.1:{port}"
        requests.get(url + "/api/version", timeout=timeout).raise_for_status()
        return "up"
    except Exception:
        return "busy"


def alive(timeout: float = 1.0) -> bool:
    return probe(timeout) == "up"


def find_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    candidate = Path(local) / "Programs" / "Ollama" / "ollama.exe" if local else None
    return str(candidate) if candidate and candidate.is_file() else None


PROCESS_NAMES = {"ollama.exe", "ollama app.exe", "ollama"}


def ollama_processes() -> list[int]:
    """Any Ollama process on this PC (server or tray). While one exists,
    nothing is started: it is waited for instead. A timer didn't prevent
    doubles: live, repeat starts left four tray processes and no server (B3)."""
    import psutil
    try:
        return [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in PROCESS_NAMES]
    except Exception:
        return [-1]  # can't tell: assume one exists, never start a second


PID_FILE = Path("memory/ollama_started.json")   # the server MBUDDY started, for the orphan guard
RUNNER_NAMES = {"llama-server.exe", "ollama_llama_server.exe"}


def start_serve(exe: str) -> subprocess.Popen:
    """Start `ollama serve` as MBUDDY's own child. Under the desktop shell the
    backend runs in a Windows job that ends everything in it when MBUDDY closes
    or crashes, so the server and its model runner go with it: no orphans (U1).
    (Out of the tree, as before, a force-close left a runner holding ~1 GB.)"""
    proc = subprocess.Popen([exe, "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True, env=_SERVE_ENV)
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(json.dumps({"pid": proc.pid, "exe": exe, "started": time.time()}), encoding="utf-8")
    except OSError:
        pass
    return proc


def started_by_mbuddy() -> int | None:
    """The pid of the Ollama server MBUDDY started (from the pid file) if that process is still the same Ollama."""
    import psutil
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
        proc = psutil.Process(int(data["pid"]))
        if proc.name().lower().startswith("ollama") and proc.create_time() >= float(data.get("started", 0)) - 5:
            return proc.pid
    except Exception:
        pass
    return None


def shutdown() -> None:
    """MBUDDY is closing. The model is taken out of memory in every case. The Ollama server is ended only when MBUDDY
    started it (the pid file says so): one the user runs themselves, from the tray, is left running."""
    try:
        unload()
    except Exception:
        pass
    pid = started_by_mbuddy()
    if pid is None:
        return
    import psutil
    try:
        server = psutil.Process(pid)
        for child in server.children(recursive=True):       # the model runner
            child.terminate()
        server.terminate()
        server.wait(5)
        log.info(f"[Brain] ended the Ollama server MBUDDY had started (pid {pid})")
    except Exception as exc:
        log.warn(f"[Brain] couldn't end the Ollama server MBUDDY started: {type(exc).__name__}")
    try:
        PID_FILE.unlink()
    except OSError:
        pass


def orphans() -> list[dict]:
    """Model runner processes whose parent is gone (the leftover seen after a
    force-kill). "ours" only when its image is under Ollama's install folder and
    its parent was the server MBUDDY started (the pid file)."""
    import psutil
    exe = find_exe()
    install = Path(exe).parent.resolve() if exe else None
    try:
        ours = int(json.loads(PID_FILE.read_text(encoding="utf-8")).get("pid", -1))
    except (OSError, ValueError):
        ours = -1
    found = []
    for proc in psutil.process_iter(["name", "ppid", "exe", "memory_info"]):
        if (proc.info["name"] or "").lower() not in RUNNER_NAMES:
            continue
        ppid = proc.info["ppid"] or 0
        if ppid and psutil.pid_exists(ppid):
            continue
        image = Path(proc.info["exe"] or "")
        under = install is not None and install in image.resolve().parents if proc.info["exe"] else False
        found.append({"pid": proc.pid, "exe": str(image), "parent": ppid, "ours": under and ppid == ours,
                      "mb": round((proc.info["memory_info"].rss if proc.info["memory_info"] else 0) / 2**20)})
    return found


def clean_orphans() -> list[dict]:
    """Report every leftover runner; stop only the ones that are clearly MBUDDY's."""
    import psutil
    found = orphans()
    for o in found:
        if o["ours"]:
            try:
                psutil.Process(o["pid"]).kill()
                log.warn(f"[Brain] stopped a model runner left behind by MBUDDY (pid {o['pid']}, {o['mb']} MB)")
            except psutil.Error:
                pass
        else:
            log.warn(f"[Brain] a model runner with no parent is running (pid {o['pid']}, {o['mb']} MB, "
                     f"{o['exe']}); it isn't clearly MBUDDY's, so it was left alone")
    return found


def model_loaded() -> bool:
    """Is the chat model in memory right now (Ollama's /api/ps)? Asked before a
    turn, so a model unloaded while asleep is warmed first (U1b). If Ollama
    can't be asked, True: the turn goes ahead as before."""
    from brain import agent
    try:
        models = requests.get(base_url() + "/api/ps", timeout=1).json().get("models") or []
    except Exception:
        return True
    name = agent._chat_model()
    return any(m.get("name") == name or m.get("model") == name for m in models)


def unload() -> None:
    """Free the model from the GPU (keep_alive 0). Used when asleep, only if the
    user hasn't turned it off (the setting unload_model_after_min, 10 by default)."""
    from brain import agent
    try:
        requests.post(base_url() + "/api/generate", json={"model": agent._chat_model(), "keep_alive": 0}, timeout=10)
        log.info("[Brain] model unloaded")
    except Exception as exc:
        log.warn(f"[Brain] couldn't unload the model: {exc}")


def ensure_running(wait_s: float = READY_WAIT_S) -> tuple[bool, str]:
    """(up, why not). Starts `ollama serve` only when nothing listens on its
    port and no Ollama process exists; otherwise waits for the port. One lock
    covers the boot, turn and background paths, so only one ever starts it."""
    global needs_warm
    with _lock:
        state = probe()
        if state == "up":
            return True, ""
        if state == "busy":
            return False, "Ollama is there but not answering yet"
        running = ollama_processes()
        if running:
            log.info(f"[Brain] Ollama is starting (process {running[0]}); waiting for it")
        else:
            exe = find_exe()
            if not exe:
                log.warn("[Brain] Ollama isn't running and ollama.exe wasn't found")
                return False, "Ollama isn't running, and I couldn't find it installed on this PC"
            log.info(f"[Brain] Ollama wasn't running; starting it ({exe} serve)")
            try:
                start_serve(exe)
            except OSError as exc:
                log.warn(f"[Brain] couldn't start Ollama: {exc}")
                return False, "Ollama isn't running, and starting it failed"
            needs_warm = True  # whenever it comes up, even late, it's warmed once
        waited = time.monotonic()
        deadline = waited + wait_s
        while time.monotonic() < deadline:
            if alive(1.0):
                log.info(f"[Brain] Ollama is up (answered after {time.monotonic() - waited:.1f}s)")
                return True, ""
            time.sleep(0.5)
        return False, f"I started Ollama, but it didn't answer within {int(wait_s)} seconds"


_SERVE_ENV: dict | None = None  # the served process's environment (None: MBUDDY's own)


def finish_start_in_background() -> None:
    """Keep waiting for a started Ollama after the turn gave up on it, and warm
    it the moment it answers, so the next turn finds it ready."""
    def run():
        if ensure_running()[0]:
            warm_if_needed()
    threading.Thread(target=run, name="mbuddy-ollama-start", daemon=True).start()


def warm_if_needed() -> None:
    """Warm once after a start, whenever Ollama is found up (B2: it came up
    after the wait, and the next turn timed out on a cold model)."""
    global needs_warm
    if needs_warm and alive(1.0):
        needs_warm = False
        warm()


def load_model() -> str:
    """Put the model in memory (no prompt is read). Returns a short timing note."""
    from brain import agent
    t = time.monotonic()
    loaded = requests.post(base_url() + "/api/generate", json={"model": agent._chat_model(), "keep_alive": "30m"},
                           timeout=180).json()
    return f"model load {time.monotonic() - t:.1f}s (Ollama load {loaded.get('load_duration', 0) / 1e9:.1f}s)"


def warm_prompts(should_stop=None) -> list[str]:
    """Read the decision step's and the reply's stable prompts into Ollama's
    cache. `should_stop()` is asked before each one: a turn that is waiting
    goes first, and its own prompts fill the cache (U1c)."""
    from brain import agent
    from brain.memory_context import build_minimal_system
    from core.state_manager import MBUDDYState
    from core.ui_settings import personalize
    model, parts = agent._chat_model(), []
    # Exactly what the decision step sends (same tools, same messages), so the
    # cached prefix is the one a real turn reuses; then the reply's fixed
    # system prompt, which every reply starts with.
    requests_to_send = (
        ("decision prompt", {"messages": agent.build_messages("hello", [], step=1), "tools": agent.area_tools_step1()}),
        ("reply prompt", {"messages": [{"role": "system", "content": personalize(build_minimal_system(MBUDDYState(), {}, "en"))},
                                       {"role": "user", "content": "hello"}]}),
    )
    for label, body in requests_to_send:
        if should_stop is not None and should_stop():
            parts.append(f"{label} skipped (a turn is waiting)")
            continue
        t = time.monotonic()
        requests.post(base_url() + "/api/chat", timeout=120, json={
            "model": model, **body, "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 1}})
        parts.append(f"{label} {time.monotonic() - t:.1f}s")
    return parts


def warm(should_stop=None) -> None:
    """Load the model and read its stable prompts, so the first spoken turn
    isn't the one paying for them. Each part is logged (U1b)."""
    parts = []
    try:
        parts.append(load_model())
        parts += warm_prompts(should_stop)
        log.info("[Brain] warmed: " + ", ".join(parts))
    except Exception as exc:
        log.warn(f"[Brain] warm-up failed after {', '.join(parts) or 'nothing'}: {type(exc).__name__}: {exc}"[:240])
