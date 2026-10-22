"""
api/brain_api.py - the brain (brain/brain_store.py) for the app: status, link,
notes, learn, delete. Local-only, like the rest of the app's API.

  GET    /brain/status          {"linked", "vault", "folder", "notes", "obsidian_installed", "learning"}
  POST   /brain/link            {"vault": optional path}  (default: the vault Obsidian has open)
  POST   /brain/unlink
  GET    /brain/notes           {"notes": [{"title", "kind", "status", "strength", "uses", "source", "path"}]}
  POST   /brain/learn           {"topic": str, "kind": "topic" | "app"}  (the "Learn" button, S94)
  DELETE /brain/notes           {"path": str}  (to the Recycle Bin; only inside <vault>/MBUDDY, S95)
"""
from __future__ import annotations

import asyncio
import json

from aiohttp import web

from api.device_control import _require_local
from brain import brain_store
from brain.tools import ToolFailure


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


async def status_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json(await asyncio.to_thread(brain_store.status))


async def link_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await _body(request)
    try:
        return _json(await asyncio.to_thread(brain_store.link, str(data.get("vault") or "")))
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)


async def unlink_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json(await asyncio.to_thread(brain_store.unlink))


async def notes_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json({"notes": await asyncio.to_thread(brain_store.list_notes)})


async def learn_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await _body(request)
    topic = str(data.get("topic", "")).strip()
    kind = "app" if data.get("kind") == "app" else "topic"
    result = await asyncio.to_thread(brain_store.learn_about, topic, kind, "the user pressed Learn")
    ok = not isinstance(result, ToolFailure)
    return _json({"ok": ok, "message": str(result).removeprefix("FAILED: ")}, 200 if ok else 400)


async def delete_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await _body(request)
    ok = await asyncio.to_thread(brain_store.delete_note, str(data.get("path", "")))
    return _json({"ok": ok}, 200 if ok else 404)


def register_brain_routes(app: web.Application) -> None:
    app.router.add_get("/brain/status", status_handler)
    app.router.add_post("/brain/link", link_handler)
    app.router.add_post("/brain/unlink", unlink_handler)
    app.router.add_get("/brain/notes", notes_handler)
    app.router.add_post("/brain/learn", learn_handler)
    app.router.add_delete("/brain/notes", delete_handler)

    async def _start_brain(app: web.Application) -> None:
        # So a learning job can speak when it comes back; then link the vault
        # (Obsidian's open one) and let unused notes fade (the learning rate).
        brain_store.set_loop(asyncio.get_running_loop())

        async def startup() -> None:
            try:
                changed = await asyncio.to_thread(brain_store.daily_decay)
                state = await asyncio.to_thread(brain_store.status)
                print(f"DEBUG: brain {'linked to ' + str(state['vault']) if state['linked'] else 'not linked'}"
                      f" ({state['notes']} notes, {changed} faded)", flush=True)
            except Exception as exc:
                print(f"DEBUG: brain startup failed: {exc}", flush=True)
        app["brain_task"] = asyncio.create_task(startup())

    app.on_startup.append(_start_brain)
