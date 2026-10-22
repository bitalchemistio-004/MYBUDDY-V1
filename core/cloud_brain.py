"""The cloud brain: a cloud model standing in for the small model on this PC (for weak laptops).

Every part of MBUDDY that thinks (the decision step, the reply, the brain's notes, the lessons) already talks
to "Ollama" at one address. When the user chooses the cloud brain, this module starts a tiny local server and
points that address at it, so all of that code keeps working unchanged: same tools, same vault, same lessons.
The server only does four things:
  1. forwards each request to ollama.com with the user's key (core/cloud.py), naming the chosen cloud model;
  2. answers the housekeeping calls (version, what's loaded, warm-up) itself, with no cloud call;
  3. writes every real call to the usage log (purpose "brain"), so the Usage page counts them;
  4. refuses anyone but this PC, and never returns or logs the key.
The choice is read once when MBUDDY starts: changing it in Settings asks for a restart, so no half-switched state.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import aiohttp
from aiohttp import web

import utils.logger as log
from core import cloud, model_gate

PORT = int(os.getenv("MBUDDY_CLOUDBRAIN_PORT", "5052"))
active = False                       # decided at startup, never changed while running
_server: dict = {}


def wanted() -> bool:
    from core import ui_settings
    return ui_settings.get("brain_mode") == "cloud" and cloud.key_present()


def mode_state() -> dict:
    """What Settings shows: the saved choice, what is running now, and whether a restart is needed."""
    from core import ui_settings
    saved = ui_settings.get("brain_mode")
    why = "The cloud brain needs an Ollama key." if saved == "cloud" and not cloud.key_present() else ""
    return {"saved": saved, "running": "cloud" if active else "local", "restart_needed": wanted() != active, "why": why}


# ── the proxy ────────────────────────────────────────────────────────────
def _local_only(request: web.Request) -> None:
    if (request.remote or "") not in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or request.headers.get("Origin"):
        raise web.HTTPForbidden(text="local only")


def _model() -> str:
    return cloud.settings()["brain_model"]


_gate = model_gate.default_gate()


async def _forward(request: web.Request, body: dict, upstream_path: str, purpose: str = "brain") -> web.StreamResponse:
    """One request at a time reaches the cloud (that is all a plan allows), the user's turn first (core/model_gate.py)."""
    prio = model_gate.parse(request.query.get("priority") or request.headers.get(model_gate.HEADER))
    t0 = time.monotonic()
    async with _gate.slot(prio):
        waited = time.monotonic() - t0
        if waited > 1.5:
            log.info(f"[CloudBrain] a {prio} call waited {waited:.1f}s for its turn at the cloud")
        return await _forward_now(request, body, upstream_path, purpose)


async def _forward_now(request: web.Request, body: dict, upstream_path: str, purpose: str = "brain") -> web.StreamResponse:
    if not cloud.key_present():
        return web.json_response({"error": "no cloud key"}, status=401)
    model = _model()
    if cloud._over_cap():
        return web.json_response({"error": "Today's cloud limit is used up. It can be changed in Settings, Models."}, status=429)
    body["model"] = model
    streaming = bool(body.get("stream", True))
    if upstream_path == "/v1/chat/completions" and streaming:
        body.setdefault("stream_options", {"include_usage": True})
    url = cloud._base() + upstream_path
    started = time.time()
    tin = tout = 0
    ok, why = True, ""
    session: aiohttp.ClientSession = request.app["session"]
    resp: web.StreamResponse | None = None
    try:
        for attempt in range(3):
            async with session.post(url, json=body, headers=cloud.auth_header(), timeout=aiohttp.ClientTimeout(total=180)) as up:
                if up.status == 429 and attempt < 2:      # a free plan allows one request at a time: wait a moment and try again
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if up.status != 200:
                    ok, why = False, f"HTTP {up.status}"
                    text = await up.text()
                    log.warn(f"[CloudBrain] {model}: HTTP {up.status} {text[:80]}")
                    return web.Response(status=up.status, body=text.encode(), content_type="application/json")
                if streaming:
                    resp = web.StreamResponse(status=200, headers={"Content-Type": up.headers.get("Content-Type", "application/x-ndjson")})
                    await resp.prepare(request)
                    async for chunk in up.content.iter_any():
                        await resp.write(chunk)
                        for line in chunk.decode("utf-8", "ignore").splitlines():
                            line = line.strip()
                            if line.startswith("data:"):
                                line = line[5:].strip()
                            if line.startswith("{") and ('"eval_count"' in line or '"usage"' in line):
                                try:
                                    d = json.loads(line)
                                except ValueError:
                                    continue
                                u = d.get("usage") or {}
                                tin = int(d.get("prompt_eval_count", u.get("prompt_tokens", tin)) or 0)
                                tout = int(d.get("eval_count", u.get("completion_tokens", tout)) or 0)
                    await resp.write_eof()
                    return resp
                data = await up.read()
                try:
                    d = json.loads(data)
                    u = d.get("usage") or {}
                    tin = int(d.get("prompt_eval_count", u.get("prompt_tokens", 0)) or 0)
                    tout = int(d.get("eval_count", u.get("completion_tokens", 0)) or 0)
                except ValueError:
                    pass
                return web.Response(status=200, body=data, content_type="application/json")
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        ok, why = False, type(exc).__name__
        if resp is not None:
            return resp
        return web.json_response({"error": "I can't reach the cloud right now."}, status=502)
    finally:
        cloud.record_call(model, purpose, tin, tout, time.time() - started, ok, why)
    return web.json_response({"error": "the cloud is busy"}, status=429)


async def _body(request: web.Request) -> dict:
    _local_only(request)
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def chat(request: web.Request) -> web.StreamResponse:
    body = await _body(request)
    # The warm-up asks for one token only to read the prompt into memory: nothing to warm in the cloud, nothing to pay.
    if (body.get("options") or {}).get("num_predict") == 1:
        return web.json_response({"model": _model(), "message": {"role": "assistant", "content": ""}, "done": True})
    return await _forward(request, body, "/api/chat")


async def generate(request: web.Request) -> web.StreamResponse:
    body = await _body(request)
    if not body.get("prompt"):          # "load" or "unload the model": nothing to do in the cloud
        return web.json_response({"model": _model(), "response": "", "done": True, "load_duration": 0})
    return await _forward(request, body, "/api/generate")


async def completions(request: web.Request) -> web.StreamResponse:
    return await _forward(request, await _body(request), "/v1/chat/completions")


async def show(request: web.Request) -> web.StreamResponse:
    return await _forward(request, await _body(request), "/api/show", purpose="info")


async def version(request: web.Request) -> web.Response:
    _local_only(request)
    return web.json_response({"version": "cloud-brain"})


async def listing(request: web.Request) -> web.Response:
    _local_only(request)
    from core import ui_settings       # "loaded" always, under the name MBUDDY asks for as well: there is nothing to load
    names = [_model(), ui_settings.get("llm_model")]
    return web.json_response({"models": [{"name": n, "model": n} for n in names]})


def build_app() -> web.Application:
    app = web.Application(client_max_size=8 * 1024 * 1024)

    async def open_session(a):
        a["session"] = aiohttp.ClientSession()

    async def close_session(a):
        await a["session"].close()

    app.on_startup.append(open_session)
    app.on_cleanup.append(close_session)
    app.router.add_post("/api/chat", chat)
    app.router.add_post("/api/generate", generate)
    app.router.add_post("/api/show", show)
    app.router.add_post("/v1/chat/completions", completions)
    app.router.add_get("/api/version", version)
    app.router.add_get("/api/ps", listing)
    app.router.add_get("/api/tags", listing)
    return app


def _serve(ready: threading.Event) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(build_app())

    async def go():
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
        _server["runner"] = runner

    try:
        loop.run_until_complete(go())
    except OSError as exc:
        log.warn(f"[CloudBrain] could not listen on 127.0.0.1:{PORT}: {exc}")
        ready.set()
        return
    ready.set()
    loop.run_forever()


_original_base: list = [None, False]      # the address the brain used before the cloud brain took over (and whether it was set)


def _ensure_proxy() -> bool:
    if "runner" in _server:
        return True
    _server.clear()
    ready = threading.Event()
    threading.Thread(target=_serve, args=(ready,), name="mbuddy-cloud-brain", daemon=True).start()
    ready.wait(10)
    return "runner" in _server


def activate() -> bool:
    """Called once at startup, before anything thinks. Starts the proxy and points the brain's address at it."""
    global active
    if active or not wanted():
        return active
    if not _ensure_proxy():
        return False
    _original_base[:] = [os.environ.get("OLLAMA_BASE_URL"), "OLLAMA_BASE_URL" in os.environ]
    os.environ["OLLAMA_BASE_URL"] = f"http://127.0.0.1:{PORT}/v1"
    active = True
    log.info(f"[CloudBrain] the cloud model {_model()} is the brain; the small model on this PC is not started")
    return True


def apply_now() -> str:
    """Make the saved choice take effect now, without a restart. Everything that thinks reads the brain's address on
    every call, so changing it switches them all at once.
      to the cloud: the model on this PC is taken out of memory and the Ollama server MBUDDY started is ended
                    (the user's own Ollama is left running, only its model is unloaded), THEN the address changes;
      to this PC:   the address goes back and the model is started and warmed in the background.
    Returns what happened, in words."""
    global active
    want = wanted()
    if want and not active:
        from brain import ollama_service
        try:
            ollama_service.shutdown()            # while the address still points at the real Ollama
        except Exception as exc:
            log.warn(f"[CloudBrain] could not free the local model: {type(exc).__name__}")
        if not activate():
            return "The cloud brain could not start."
        return "The cloud brain is on: the model on this PC has been stopped."
    if not want and active:
        if _original_base[1]:
            os.environ["OLLAMA_BASE_URL"] = _original_base[0] or ""
        else:
            os.environ.pop("OLLAMA_BASE_URL", None)
        active = False
        from brain import ollama_service

        def start_local():
            up, _why = ollama_service.ensure_running()
            if up:
                ollama_service.warm()
        threading.Thread(target=start_local, name="mbuddy-local-brain", daemon=True).start()
        log.info("[CloudBrain] back to the model on this PC")
        return "Back to the model on this PC: starting it now."
    return ""
