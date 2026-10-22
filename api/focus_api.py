"""api/focus_api.py - Focus sprints (core/focus.py)."""
from __future__ import annotations

import asyncio

from aiohttp import web

from api.device_control import _require_local
from core import focus


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Send a JSON object.")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Send a JSON object.")
    return data


async def state_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await asyncio.to_thread(lambda: {"current": focus.current(), "week": focus.week(),
                                            "settings": focus.settings(), "limits": [focus.MIN_MIN, focus.MAX_MIN]})
    return _json({"ok": True, **data})


async def start_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await _body(request)
    try:
        cur = await asyncio.to_thread(focus.start, data.get("minutes"))
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({"ok": True, "current": cur})


async def stop_handler(request: web.Request) -> web.Response:
    _require_local(request)
    rec = await asyncio.to_thread(focus.stop)
    return _json({"ok": True, "ended": rec})


async def settings_handler(request: web.Request) -> web.Response:
    _require_local(request)
    try:
        new = await asyncio.to_thread(focus.set_settings, await _body(request))
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({"ok": True, "settings": new})


def register(app: web.Application) -> None:
    app.router.add_get("/ui/focus", state_handler)
    app.router.add_post("/ui/focus/start", start_handler)
    app.router.add_post("/ui/focus/stop", stop_handler)
    app.router.add_post("/ui/focus/settings", settings_handler)
