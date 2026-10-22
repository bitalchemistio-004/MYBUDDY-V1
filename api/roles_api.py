"""api/roles_api.py - Apps I can use: which app does which job (core/roles.py).

Every option is listed with what it can really do (control, search, play a
song) and whether it is on this PC. Nothing is chosen until the user chooses.
"""
from __future__ import annotations

import asyncio

from aiohttp import web

from api.device_control import _require_local
from core import roles


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


async def overview_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json({"ok": True, **(await asyncio.to_thread(roles.overview))})


async def choose_handler(request: web.Request) -> web.Response:
    """{role, provider}: choose; provider null clears the choice."""
    _require_local(request)
    data = await _body(request)
    role, pid = data.get("role"), data.get("provider")
    try:
        if pid is not None:
            p = roles.find(str(pid))
            if p and not (await asyncio.to_thread(roles.available, p))[0]:
                return _json({"ok": False, "error": f"{p['name']} isn't on this PC."}, 400)
        await asyncio.to_thread(roles.choose, role, pid)
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({"ok": True, **(await asyncio.to_thread(roles.overview))})


async def custom_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await _body(request)
    if request.method == "DELETE":
        done = await asyncio.to_thread(roles.remove_custom, str(data.get("id") or ""))
        return _json({"ok": done, **(await asyncio.to_thread(roles.overview))} if done else {"ok": False, "error": "No such app."},
                     200 if done else 404)
    try:
        p = await asyncio.to_thread(roles.add_custom, data.get("role"), data.get("name"), data.get("kind"),
                                    str(data.get("search") or ""), str(data.get("exe") or ""), str(data.get("exe_args") or ""))
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({"ok": True, "added": p["id"], **(await asyncio.to_thread(roles.overview))})


def register(app: web.Application) -> None:
    app.router.add_get("/ui/roles", overview_handler)
    app.router.add_post("/ui/roles", choose_handler)
    app.router.add_post("/ui/roles/custom", custom_handler)
    app.router.add_delete("/ui/roles/custom", custom_handler)
