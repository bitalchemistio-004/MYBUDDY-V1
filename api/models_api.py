"""api/models_api.py - Settings, Models: the local model, the cloud model and what was spent (core/cloud.py)."""
from __future__ import annotations

import asyncio

from aiohttp import web

from api.device_control import _require_local
from core import cloud, cloud_brain, ui_settings


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _state() -> dict:
    return {"ok": True,
            "local": {"model": ui_settings.get("llm_model"), "where": "this PC"},
            "cloud": cloud.status(),
            "usage_today": cloud.usage(1),
            "usage_week": cloud.usage(7),
            "work_on": ui_settings.get("work_on"),
            "brain": cloud_brain.mode_state(), "speech_device": ui_settings.get("speech_device"),
            "key_storage": "Windows account-encrypted" if cloud.key_source() == "app" else ("the .env file" if cloud.key_source() == "env" else "")}


async def state_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json(await asyncio.to_thread(_state))


async def update_handler(request: web.Request) -> web.Response:
    _require_local(request)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "Send a JSON object."}, 400)
    changes = {k: v for k, v in (data or {}).items() if k in ("cloud", "work_on", "brain_mode")} if isinstance(data, dict) else {}
    if not changes:
        return _json({"ok": False, "error": "Nothing to change."}, 400)
    if changes.get("brain_mode") == "cloud" and not cloud.key_present():
        return _json({"ok": False, "error": "Save your Ollama key first: the cloud brain can't think without it."}, 400)
    try:
        await asyncio.to_thread(ui_settings.update, changes)
    except ui_settings.SettingsError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    note = ""
    if "brain_mode" in changes:
        note = await asyncio.to_thread(cloud_brain.apply_now)      # at once: no restart, and the other model is stopped
    state = await asyncio.to_thread(_state)
    return _json({**state, "note": note} if note else state)


async def test_handler(request: web.Request) -> web.Response:
    """One tiny, non-personal request: does the cloud answer with this key and model?"""
    _require_local(request)
    return _json({"ok": True, "test": await asyncio.to_thread(cloud.probe)})


async def key_handler(request: web.Request) -> web.Response:
    """Save or remove the user's own Ollama key. A key is tried with one tiny request before it is kept, is
    stored encrypted for this Windows account, and is never sent back to the app."""
    _require_local(request)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "Send a JSON object."}, 400)
    if not isinstance(data, dict):
        return _json({"ok": False, "error": "Send a JSON object."}, 400)
    if data.get("action") == "remove":
        await asyncio.to_thread(cloud.remove_key)
        if ui_settings.get("brain_mode") == "cloud" and not cloud.key_present():
            await asyncio.to_thread(ui_settings.update, {"brain_mode": "local"})
            await asyncio.to_thread(cloud_brain.apply_now)
        return _json(await asyncio.to_thread(_state))
    key = str(data.get("key", ""))
    if not cloud.secret_store_available():
        return _json({"ok": False, "error": "Safe key storage needs Windows."}, 400)
    result = await asyncio.to_thread(cloud.validate_key, key)
    if not result["valid"]:
        return _json({"ok": False, "error": result["why"]}, 400)
    await asyncio.to_thread(cloud.save_key, key)
    state = await asyncio.to_thread(_state)
    return _json({**state, "note": result["why"]})


async def catalog_handler(request: web.Request) -> web.Response:
    """Cloud model names from ollama.com, and (POST) which of the chosen ones this plan allows."""
    _require_local(request)
    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:
            data = {}
        names = [str(m) for m in (data.get("models") or [])][:8] if isinstance(data, dict) else []
        if not cloud.key_present():
            return _json({"ok": False, "error": "Save your Ollama key first."}, 400)
        return _json({"ok": True, "plan": await asyncio.to_thread(cloud.check_plan, names)})
    return _json({"ok": True, "models": await asyncio.to_thread(cloud.catalog, request.query.get("refresh") == "1")})


async def usage_handler(request: web.Request) -> web.Response:
    _require_local(request)
    try:
        days = int(request.query.get("days", "30"))
    except ValueError:
        days = 30
    return _json({"ok": True, "usage": await asyncio.to_thread(cloud.usage_report, days)})


async def preview_handler(request: web.Request) -> web.Response:
    """Ask one chosen model a short question and show its answer, its speed and its token use."""
    _require_local(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    result = await asyncio.to_thread(cloud.preview, str(data.get("model", "")), str(data.get("prompt", "")))
    return _json({"ok": result["ok"], "preview": result}, 200 if result["ok"] else 400)


def register(app: web.Application) -> None:
    app.router.add_get("/ui/models", state_handler)
    app.router.add_post("/ui/models", update_handler)
    app.router.add_post("/ui/models/test", test_handler)
    app.router.add_post("/ui/models/key", key_handler)
    app.router.add_get("/ui/models/catalog", catalog_handler)
    app.router.add_post("/ui/models/catalog", catalog_handler)
    app.router.add_get("/ui/models/usage", usage_handler)
    app.router.add_post("/ui/models/preview", preview_handler)
