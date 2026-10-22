"""api/activity_api.py - Today: where the time went (core/activity.py).

Local-only. Tracking is opt-in; nothing is sampled until the user turns it on
here. GET /ui/activity/today is the whole screen's data; the rest are the
controls on it.
"""
from __future__ import annotations

import asyncio
from datetime import date

from aiohttp import web

from api.device_control import _require_local
from core import activity


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


async def today_handler(request: web.Request) -> web.Response:
    _require_local(request)
    raw = request.query.get("date")
    try:
        day = date.fromisoformat(raw) if raw else date.today()
    except ValueError:
        return _json({"ok": False, "error": "date must be YYYY-MM-DD"}, 400)
    data = await asyncio.to_thread(activity.summary, day)
    return _json({"ok": True, **data})


async def settings_handler(request: web.Request) -> web.Response:
    _require_local(request)
    if request.method == "POST":
        try:
            new = await asyncio.to_thread(activity.set_settings, await _body(request))
        except ValueError as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        return _json({"ok": True, "settings": new})
    return _json({"ok": True, "settings": activity.settings()})


async def answer_handler(request: web.Request) -> web.Response:
    """The user answers a check-in: work | off | break."""
    _require_local(request)
    data = await _body(request)
    cid = data.get("id")
    if isinstance(cid, bool) or not isinstance(cid, int):
        return _json({"ok": False, "error": "id must be a whole number"}, 400)
    try:
        result = await asyncio.to_thread(activity.TRACKER.answer, cid, data.get("answer"))
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({"ok": True, **result})


async def label_handler(request: web.Request) -> web.Response:
    """The user sets a program's status by hand: work | off | mixed | unknown."""
    _require_local(request)
    data = await _body(request)
    key = str(data.get("key") or "")
    if not key or len(key) > 80 or "/" in key or "\\" in key:
        return _json({"ok": False, "error": "key must be a program name"}, 400)
    try:
        status = await asyncio.to_thread(activity.set_label, key, data.get("status"))
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({"ok": True, "key": key, "status": status})


async def icon_handler(request: web.Request) -> web.Response:
    """The icon of a program Today has seen (from the path stored with it),
    never of an arbitrary path."""
    _require_local(request)
    key = request.query.get("key", "")
    exe = ""
    for offset in range(0, 7):
        from datetime import timedelta
        day = activity.load_day(date.today() - timedelta(days=offset))
        exe = (day["apps"].get(key) or {}).get("exe", "")
        if exe:
            break
    if not exe:
        raise web.HTTPNotFound()
    from core import app_icon
    png = await asyncio.to_thread(app_icon.for_path, exe)
    if not png:
        raise web.HTTPNotFound()
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "max-age=3600"})


def register(app: web.Application) -> None:
    app.router.add_get("/ui/activity/today", today_handler)
    app.router.add_get("/ui/activity/settings", settings_handler)
    app.router.add_post("/ui/activity/settings", settings_handler)
    app.router.add_post("/ui/activity/answer", answer_handler)
    app.router.add_post("/ui/activity/label", label_handler)
    app.router.add_get("/ui/activity/icon", icon_handler)

    async def start(_app):
        activity.start()

    async def stop(_app):
        activity.stop()
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
