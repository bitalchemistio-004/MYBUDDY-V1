"""api/routines_api.py - Routines: saved steps run with one word (core/routines.py)."""
from __future__ import annotations

import asyncio

from aiohttp import web

from api.device_control import _require_local
from core import routines


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


def _state() -> dict:
    return {"ok": True, "step_types": {k: {"label": v["label"], "field": v["field"]} for k, v in routines.STEP_TYPES.items()},
            "routines": [{**r, "running": routines.is_running(r["id"])} for r in routines.routines()]}


async def state_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json(await asyncio.to_thread(_state))


async def save_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await _body(request)
    try:
        r = await asyncio.to_thread(routines.save, data.get("name"), data.get("steps"), data.get("id"))
    except KeyError:
        return _json({"ok": False, "error": "No such routine."}, 404)
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({**(await asyncio.to_thread(_state)), "saved": r["id"]})


async def delete_handler(request: web.Request) -> web.Response:
    _require_local(request)
    done = await asyncio.to_thread(routines.delete, str((await _body(request)).get("id") or ""))
    return _json(await asyncio.to_thread(_state) if done else {"ok": False, "error": "No such routine."}, 200 if done else 404)


async def run_handler(request: web.Request) -> web.Response:
    """The user's own click: starts the routine in the background; progress shows as a job."""
    _require_local(request)
    rid = str((await _body(request)).get("id") or "")
    if routines.get(rid) is None:
        return _json({"ok": False, "error": "No such routine."}, 404)
    started = await asyncio.to_thread(routines.run_async, rid)
    return _json({"ok": started, **({} if started else {"error": "That routine is already running."})}, 200 if started else 409)


def register(app: web.Application) -> None:
    app.router.add_get("/ui/routines", state_handler)
    app.router.add_post("/ui/routines", save_handler)
    app.router.add_delete("/ui/routines", delete_handler)
    app.router.add_post("/ui/routines/run", run_handler)
