"""Load everything a turn needs at boot (M2 S4, G1; efficiency, 2026-10-06).

Right after a restart the first turn hit "Brain timeout after 30.0s": the
voice (Kokoro), the memory search and the brain were all loading lazily at
once on the shared 6 GB GPU, and the decision step spent ~9.8 s of its
10.7 s stalled. Each part is loaded here in turn, timed, with the brain last
so its prompt cache is the freshest.

Two changes keep that order but cut the wait (a cold boot took 60-100 s):
- The model server (Ollama) is a separate CPU process: it starts at once in the
  background while the GPU parts load in turn. The brain step waits for it.
- The brain step loads the model, then is READY. Reading the two stable prompts
  into the cache (30 s on this GPU) happens afterwards in the background, and
  yields to a turn that is waiting (U1c), so the app is usable sooner.
"""
from __future__ import annotations

import threading
import time

import numpy as np

import utils.logger as log


def _voice() -> None:
    from tts import local_tts
    local_tts.warm()  # loads Kokoro and runs one tiny synthesis


def _memory() -> None:
    from memory.vector_store import get_vector_store
    store = get_vector_store()
    store.warmup()
    store.search("hello there", 1, True)  # the query path a turn uses


def _hearing() -> None:
    from stt import local_whisper
    model = local_whisper.get_model()  # measured: 8.0 s to load on first use
    # One short decode so the first real clip doesn't pay the first GPU run;
    # without VAD, which would skip silence and decode nothing.
    list(model.transcribe(np.zeros(8000, dtype=np.float32), language="en", beam_size=1, vad_filter=False)[0])


_server: dict = {"thread": None, "note": ""}


def _start_server() -> None:
    """Model runners left behind are cleaned (only MBUDDY's own), then the model server is started."""
    from brain import brain_adapters, ollama_service
    t = time.perf_counter()
    found = ollama_service.clean_orphans()
    if "ollama" not in brain_adapters.get_available_providers():
        _server["note"] = "not local"
        return
    up, why = ollama_service.ensure_running()
    _server["note"] = (f"{len(found)} left behind; " if found else "") + ("" if up else f"not reachable: {why}")
    log.info(f"[Boot] model server {'up' if up else 'not up'} in {time.perf_counter() - t:.1f}s" + (f" ({_server['note']})" if _server["note"] else ""))


def _brain() -> str:
    thread = _server.get("thread")
    if thread is not None:
        thread.join()                         # started at the top of warm_everything, in parallel
    if _server["note"] == "not local":
        return "not local"
    if "not reachable" in _server["note"]:
        return _server["note"]
    from brain import ollama_service
    note = ollama_service.load_model()
    log.info(f"[Brain] {note}")
    ollama_service.needs_warm = False         # boot loaded it and warms the prompts in the background: the first turn must not wait for a second warm-up
    threading.Thread(target=_warm_prompts_later, name="mbuddy-prompt-warm", daemon=True).start()
    return ""


def _warm_prompts_later() -> None:
    """The prompt reads, after the app is ready; a turn that arrives goes first."""
    from brain import ollama_service
    from core.status import runtime_status
    try:
        parts = ollama_service.warm_prompts(should_stop=lambda: bool(getattr(runtime_status, "thinking", False)))
        log.info("[Brain] warmed (background): " + ", ".join(parts))
    except Exception as exc:
        log.warn(f"[Brain] background warm-up failed: {type(exc).__name__}: {exc}"[:200])


def _unverified() -> str:
    """Once: notes never checked by code are marked unverified (U2; reversible)."""
    from brain import verify_migration
    changed = verify_migration.apply()
    return f"{changed} marked unverified" if changed else ""


STEPS = (("voice", _voice), ("memory search", _memory), ("hearing", _hearing),
         ("notes check", _unverified), ("brain", _brain))
EARLY = (("model server", _start_server),)   # CPU-only work that starts at once, in the background


def warm_everything() -> float:
    """Blocking; run it in a thread. Returns the total seconds."""
    started = time.perf_counter()
    _server["thread"], _server["note"] = None, ""
    if EARLY:
        _server["thread"] = threading.Thread(target=EARLY[0][1], name="mbuddy-model-server", daemon=True)
        _server["thread"].start()
    def run_step(name, step) -> None:
        t = time.perf_counter()
        try:
            note = step() or ""
            log.info(f"[Boot] {name} ready in {time.perf_counter() - t:.1f}s" + (f" ({note})" if note else ""))
        except Exception as exc:
            log.warn(f"[Boot] {name} failed to load: {type(exc).__name__}: {exc}"[:200])

    pending = list(STEPS)
    for name, step in pending:
        run_step(name, step)
    total = time.perf_counter() - started
    log.info(f"[Boot] ready in {total:.1f}s")
    return total
