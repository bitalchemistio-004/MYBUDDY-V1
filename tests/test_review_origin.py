"""Senior review regression tests (docs/audit/SENIOR_REVIEW.md, C1 and M1).

These are EXPECTED TO FAIL until the findings are fixed:
- C1: a web page on any other site can open the /ui/live WebSocket (and
  /status/stream) and read what MBUDDY heard and said. WebSockets are not
  covered by CORS, and origin_guard lets every GET through.
- M1: with DNS rebinding, a foreign site reaches 127.0.0.1:5000 under its own
  host name and reads GET endpoints (settings incl. the new "About you"
  profile) because no Host header check exists.

Scratch settings only; no real database is read; nothing acts on the PC.
"""
import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import setup_checks, ui_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    ui_settings.reset_cache()
    monkeypatch.setattr(setup_checks, "ollama_models", lambda: ["qwen3:4b-instruct"])
    import core.companion_settings as cs_mod
    monkeypatch.setattr(cs_mod, "_companion_settings", cs_mod.CompanionSettings(path=tmp_path / "companion.json"))
    yield
    ui_settings.reset_cache()


def _run(coro_fn):
    async def go():
        from api.ui_api import origin_guard, register_ui_routes
        app = web.Application(middlewares=[origin_guard])
        register_ui_routes(app)
        async with TestClient(TestServer(app)) as client:
            return await coro_fn(client)
    return asyncio.run(go())


def test_foreign_site_cannot_open_the_live_websocket():
    """C1: ws://127.0.0.1:5000/ui/live from https://evil.example must be refused."""
    async def go(c):
        try:
            ws = await c.ws_connect("/ui/live", headers={"Origin": "https://evil.example"})
        except aiohttp.WSServerHandshakeError as exc:
            assert exc.status in (403, 400)
            return
        msg = await ws.receive(timeout=2)
        await ws.close()
        pytest.fail(f"a foreign origin opened /ui/live and received: {str(msg.data)[:120]!r}")
    _run(go)


def test_the_app_origin_can_still_open_the_live_websocket():
    """Control: the fix must not lock the app itself out."""
    async def go(c):
        ws = await c.ws_connect("/ui/live", headers={"Origin": "http://tauri.localhost"})
        msg = await ws.receive(timeout=2)
        await ws.close()
        assert msg.type == aiohttp.WSMsgType.TEXT and "settings_rev" in msg.data
    _run(go)


def test_dns_rebinding_host_is_refused_for_private_reads():
    """M1: a request whose Host is a foreign name (DNS rebinding) must not get
    the user's settings, which now include the free-text "About you" profile."""
    async def go(c):
        await c.post("/ui/settings", json={"changes": {"about_me": "I'm Sam, a nurse."}})
        r = await c.get("/ui/settings?only=settings", headers={"Host": "attacker.example:5000"})
        if r.status == 200:
            body = await r.json()
            pytest.fail(f"foreign Host got the profile: {body['settings'].get('about_me')!r}")
        assert r.status in (400, 403, 421)
    _run(go)
