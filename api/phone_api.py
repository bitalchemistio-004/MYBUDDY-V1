"""api/phone_api.py - the things MBUDDY asked the user's phone to do (core/phone_actions.py): listed for the phone page, answered by it."""
from __future__ import annotations

import asyncio

from aiohttp import web

from api.device_control import _require_local
from core import phone_actions


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


async def list_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json({"ok": True, "actions": await asyncio.to_thread(phone_actions.pending)})


async def answer_handler(request: web.Request) -> web.Response:
    """The phone says an action was done or dismissed (it is then no longer offered)."""
    _require_local(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    ok = await asyncio.to_thread(phone_actions.mark, str((data or {}).get("id", "")), str((data or {}).get("status", "")))
    return _json({"ok": ok} if ok else {"ok": False, "error": "No such action."}, 200 if ok else 404)


def register(app: web.Application) -> None:
    app.router.add_get("/ui/phone-actions", list_handler)
    app.router.add_post("/ui/phone-actions", answer_handler)
