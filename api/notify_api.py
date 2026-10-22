"""api/notify_api.py - Settings, Messages: what MBUDDY watches, what is waiting, and what the user pressed on a reminder."""
from __future__ import annotations

import asyncio

from aiohttp import web

from api.device_control import _require_local
from core import notif_watch, ui_settings


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _state() -> dict:
    cfg = notif_watch.settings()
    seen = notif_watch.apps_seen()
    names = list(dict.fromkeys([*notif_watch.APPS, *cfg["apps"], *seen]))
    return {"ok": True, "settings": cfg,
            "apps": [{"name": n, "seen": seen.get(n, 0), "watched": n in cfg["apps"]} for n in names],
            "waiting": notif_watch.missed(), "recent": notif_watch.recent(15), "alert": notif_watch.current_alert()}


async def state_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json(await asyncio.to_thread(_state))


async def act_handler(request: web.Request) -> web.Response:
    """seen, snooze, mute, shown (the reminder was put in front of the user), open (the app), clear (forget all)."""
    _require_local(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    action, rec_id = str((data or {}).get("action", "")), str((data or {}).get("id", ""))
    if action == "clear":
        await asyncio.to_thread(notif_watch.forget_all)
        return _json(await asyncio.to_thread(_state))
    if action == "open":
        rec = next((r for r in notif_watch.recent(200) if r["id"] == rec_id), None)
        if not rec:
            return _json({"ok": False, "error": "That message is gone."}, 404)
        opened = await asyncio.to_thread(notif_watch.bring_to_front, rec["app"])
        await asyncio.to_thread(notif_watch.act, rec_id, "seen")
        return _json({"ok": opened, **({} if opened else {"error": f"I couldn't find {rec['app']} open."})})
    ok = await asyncio.to_thread(notif_watch.act, rec_id, action)
    return _json({"ok": ok} if ok else {"ok": False, "error": "No such message or action."}, 200 if ok else 404)


def register(app: web.Application) -> None:
    app.router.add_get("/ui/notifications", state_handler)
    app.router.add_post("/ui/notifications", act_handler)
