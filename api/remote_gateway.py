"""The phone gateway: a second, small server that lets a paired phone use MBUDDY from anywhere.

Why a separate server and not the main one: the main API (port 5000) trusts "the request comes from this PC".
A tunnel or a reverse proxy (Tailscale Serve, for one) makes every remote request arrive from this PC too, so
exposing port 5000 would hand the whole API to anyone on the tunnel. This server never trusts where a request
comes from. It listens on 127.0.0.1 only (a private tunnel such as `tailscale serve` points at it; the local
network is a separate opt-in), and:

- pairing: POST /pair with the code shown on the PC returns a token once (core/remote.py);
- every other call needs that token (Authorization: Bearer, or ?t= for the live WebSocket);
- the token only opens an ALLOWLIST of calls (what the phone screens need). Settings, the brain, the control
  endpoints and everything not listed answer 403, however the request is made;
- chat from the phone is never spoken on the PC, and asking is the only thing a phone can say to the buddy;
  a PC action still waits for a yes, which the phone may give from its own screen;
- "Pause access" (core/remote.py) cuts every phone off at once.

The pages themselves (the same app, in its narrow layout) are served here too, so the phone needs nothing
installed beyond a browser.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import aiohttp
from aiohttp import web

import utils.logger as log
from core import remote

GATEWAY_PORT = int(os.getenv("MBUDDY_REMOTE_PORT", "5051"))
UI_DIR = Path(__file__).resolve().parents[1] / "shell" / "ui"

# (method, path regex) the phone may call; the path is without the /remote/ prefix.
ALLOW = [
    ("GET", r"ui/(settings|overview|conversation|tasks|pending-action|focus|routines|models|learned|memory|music|abilities|system-volume|app-icon)"),
    ("GET", r"ui/activity/(today|settings)"),
    ("POST", r"control/chat"),
    ("GET", r"control/chat/result"),
    ("POST", r"ui/pending-action"),
    ("POST", r"ui/tasks"),
    ("POST", r"ui/tasks/\d+/(done|snooze)"),
    ("POST", r"ui/focus/(start|stop)"),
    ("POST", r"ui/routines/run"),
    ("POST", r"ui/models"),
    ("GET", r"ui/phone-actions"),
    ("POST", r"ui/phone-actions"),
    ("POST", r"ui/phone-agent/step"),
]
_ALLOW = [(m, re.compile(rf"^{p}$")) for m, p in ALLOW]
MAX_BODY = 16_000


def allowed(method: str, path: str) -> bool:
    return any(m == method and p.match(path) for m, p in _ALLOW)


def _filter_body(path: str, body: dict) -> dict | None:
    """The body that may be forwarded, or None when it must be refused."""
    if path == "control/chat":
        text = str(body.get("text", ""))[:1000]
        return {"text": text, "speak": False} if text.strip() else None      # never spoken on the PC from a phone
    if path == "ui/models":
        return {"work_on": body["work_on"]} if set(body) == {"work_on"} else None   # the phone may only switch where it works
    if path == "ui/phone-actions":
        return {"id": str(body.get("id", ""))[:20], "status": str(body.get("status", ""))[:12]}
    return body


def _describe(path: str, body: dict) -> str:
    """What a phone's request did, in words (no more than the start of what was typed)."""
    if path == "control/chat":
        return f"Asked: {str(body.get('text', ''))[:60]}"
    if path == "ui/pending-action":
        return f"Answered {body.get('decision', '')} to a question"
    if path == "ui/tasks":
        return f"Added a task: {str(body.get('title', ''))[:50]}"
    if path.endswith("/done"):
        return "Finished a task"
    if path.endswith("/snooze"):
        return "Snoozed a reminder"
    if path.startswith("ui/focus/"):
        return "Started a focus sprint" if path.endswith("start") else "Stopped the focus sprint"
    if path.startswith("ui/routines/"):
        return "Ran a routine"
    if path == "ui/models":
        return f"Switched to working on the {body.get('work_on', '')}"
    if path == "ui/phone-agent/step":
        return f"Phone task step: {str(body.get('goal', ''))[:50]}"
    if path == "ui/phone-actions":
        return f"Did or dismissed a phone action ({body.get('status', '')})"
    return path


def _token(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query.get("t", "")


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})


# ── pages ────────────────────────────────────────────────────────────────
async def page_handler(request: web.Request) -> web.Response:
    html = (UI_DIR / "app.html").read_text(encoding="utf-8")
    inject = ('<link rel="manifest" href="manifest.webmanifest"><meta name="theme-color" content="#000000">'
              '<link rel="icon" href="assets/pwa/icon-192.png"><link rel="apple-touch-icon" href="assets/pwa/icon-192.png">'
              '<script>window.__MBUDDY_REMOTE__=true;if("serviceWorker"in navigator)navigator.serviceWorker.register("sw.js").catch(function(){});</script>')
    return web.Response(text=html.replace("</head>", inject + "</head>", 1), content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store"})


async def phone_page_handler(request: web.Request) -> web.Response:
    """The phone tools page: alarms, timers, calendar, calls, messages, directions, and the on-device AI guide. It works
    from the phone's own copy when the PC is off (the service worker keeps it), so it is a page of its own."""
    html = (UI_DIR / "phone.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html", charset="utf-8", headers={"Cache-Control": "no-store"})


async def manifest_handler(request: web.Request) -> web.Response:
    return web.json_response({
        "shortcuts": [{"name": "Phone tools", "short_name": "Tools", "url": "phone"}],
        "name": "MBUDDY", "short_name": "MBUDDY", "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#000000", "theme_color": "#000000",
        "icons": [{"src": "assets/pwa/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "assets/pwa/icon-512.png", "sizes": "512x512", "type": "image/png"}]},
        content_type="application/manifest+json")


async def worker_handler(request: web.Request) -> web.Response:
    return web.Response(body=(UI_DIR / "sw.js").read_bytes(), content_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


async def health_handler(request: web.Request) -> web.Response:
    return _json({"ok": True, "paused": remote.paused()})


# ── pairing ──────────────────────────────────────────────────────────────
async def pair_handler(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "Send a JSON object."}, 400)
    if not isinstance(data, dict):
        return _json({"ok": False, "error": "Send a JSON object."}, 400)
    token, why = await asyncio.to_thread(remote.pair, str(data.get("code", "")), str(data.get("name", "")))
    if token is None:
        return _json({"ok": False, "error": why}, 403)
    return _json({"ok": True, "token": token})


# ── the proxy ────────────────────────────────────────────────────────────
def _backend(request: web.Request) -> str:
    return request.app["backend"].rstrip("/")


async def proxy_handler(request: web.Request) -> web.Response:
    path = request.match_info["tail"]
    if request.method == "OPTIONS":
        return web.Response(status=204)
    if remote.paused():
        return _json({"ok": False, "error": "Remote access is paused on the PC."}, 423)
    device = await asyncio.to_thread(remote.verify, _token(request))
    if device is None:
        return _json({"ok": False, "error": "Not paired."}, 401)
    if not allowed(request.method, path):
        log.warn(f"[Remote] refused {request.method} /{path} from {device['name']}")
        return _json({"ok": False, "error": "The phone can't do that."}, 403)
    body = None
    if request.method == "POST":
        if (request.content_length or 0) > MAX_BODY:
            return _json({"ok": False, "error": "Too big."}, 413)
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = _filter_body(path, data if isinstance(data, dict) else {})
        if data is None:
            return _json({"ok": False, "error": "The phone can't do that."}, 403)
        body = data
    url = f"{_backend(request)}/{path}"
    query = {k: v for k, v in request.query.items() if k != "t"}
    try:
        async with request.app["session"].request(request.method, url, params=query or None, json=body,
                                                   timeout=aiohttp.ClientTimeout(total=170)) as r:
            payload = await r.read()
            if request.method == "POST" and r.status == 200:
                await asyncio.to_thread(remote.log_action, device["name"], _describe(path, body or {}))
            return web.Response(body=payload, status=r.status, content_type=r.content_type or "application/json",
                                headers={"Cache-Control": "no-store"})
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return _json({"ok": False, "error": f"The PC didn't answer ({type(exc).__name__})."}, 502)


async def live_handler(request: web.Request) -> web.WebSocketResponse:
    """The live status, one way: PC to phone. Same token rule; closes when access is paused."""
    if remote.paused() or await asyncio.to_thread(remote.verify, _token(request)) is None:
        return web.Response(status=401)  # type: ignore[return-value]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    try:
        async with request.app["session"].ws_connect(_backend(request).replace("http", "ws") + "/ui/live") as upstream:
            async def pump() -> None:
                async for msg in upstream:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        if remote.paused():
                            break
                        await ws.send_str(msg.data)
            pumper = asyncio.ensure_future(pump())
            async for msg in ws:                      # the phone says nothing to the buddy over this socket
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
            pumper.cancel()
    except (aiohttp.ClientError, asyncio.CancelledError):
        pass
    finally:
        await ws.close()
    return ws


@web.middleware
async def headers_middleware(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


def build_app(backend: str = "http://127.0.0.1:5000") -> web.Application:
    app = web.Application(middlewares=[headers_middleware])
    app["backend"] = backend

    async def open_session(a: web.Application) -> None:
        a["session"] = aiohttp.ClientSession()

    async def close_session(a: web.Application) -> None:
        await a["session"].close()

    app.on_startup.append(open_session)
    app.on_cleanup.append(close_session)
    app.router.add_get("/", page_handler)
    app.router.add_get("/app.html", page_handler)
    app.router.add_get("/phone", phone_page_handler)
    app.router.add_get("/manifest.webmanifest", manifest_handler)
    app.router.add_get("/sw.js", worker_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_post("/pair", pair_handler)
    app.router.add_get("/remote/ui/live", live_handler)
    app.router.add_route("*", "/remote/{tail:.*}", proxy_handler)
    from api import remote_mcp
    app.router.add_route("*", "/mcp/{token}", remote_mcp.mcp_handler)     # a phone's own model uses MBUDDY's tools
    for folder in ("css", "js", "assets"):
        app.router.add_static(f"/{folder}", UI_DIR / folder, follow_symlinks=False)
    return app


# ── started and stopped with the backend ─────────────────────────────────
async def start(main_app: web.Application) -> None:
    port = int(os.environ.get("PORT", 5000))
    app = build_app(f"http://127.0.0.1:{port}")
    runner = web.AppRunner(app)
    await runner.setup()
    host = "0.0.0.0" if remote.lan_enabled() else "127.0.0.1"
    try:
        await web.TCPSite(runner, host, GATEWAY_PORT).start()
    except OSError as exc:
        log.warn(f"[Remote] the phone gateway could not listen on {host}:{GATEWAY_PORT}: {exc}")
        await runner.cleanup()
        return
    main_app["remote_runner"] = runner
    main_app["remote_listening"] = (host, GATEWAY_PORT)
    log.info(f"[Remote] phone gateway on {host}:{GATEWAY_PORT} ({'local network too' if host != '127.0.0.1' else 'this PC only: a private tunnel points at it'})")


async def stop(main_app: web.Application) -> None:
    runner = main_app.get("remote_runner")
    if runner is not None:
        await runner.cleanup()
