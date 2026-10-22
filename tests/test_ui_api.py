"""The app's HTTP API and origin guard (docs/PHASE3_BUILD_PLAN.md 3.1)."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import setup_checks, ui_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    ui_settings.reset_cache()
    monkeypatch.setattr(setup_checks, "ollama_models", lambda: ["qwen3:4b-instruct"])
    yield
    ui_settings.reset_cache()


def run(coro_fn):
    async def go():
        from api.ui_api import origin_guard, register_ui_routes
        app = web.Application(middlewares=[origin_guard])
        register_ui_routes(app)
        async with TestClient(TestServer(app)) as client:
            return await coro_fn(client)
    return asyncio.run(go())


def test_settings_round_trip_and_validation():
    async def go(c):
        r = await c.get("/ui/settings")
        body = await r.json()
        assert body["settings"]["theme"] == "character" and "blink" in body["options"]["characters"]
        r = await c.post("/ui/settings", json={"changes": {"character": "cinder", "theme": "character"}})
        assert (await r.json())["settings"]["character"] == "cinder"
        r = await c.post("/ui/settings", json={"changes": {"theme": "neon"}})
        assert r.status == 400 and "theme" in (await r.json())["error"]
    run(go)


def test_other_websites_cannot_change_anything():
    async def go(c):
        r = await c.post("/ui/settings", json={"changes": {"speak_replies": False}}, headers={"Origin": "https://evil.example"})
        assert r.status == 403
        assert ui_settings.get("speak_replies") is True
        r = await c.get("/ui/settings", headers={"Origin": "https://evil.example"})
        assert "Access-Control-Allow-Origin" not in r.headers  # the page can't read it
    run(go)


def test_the_app_origin_gets_cors_and_preflight():
    async def go(c):
        r = await c.options("/ui/settings", headers={"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "POST"})
        assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "http://tauri.localhost"
        r = await c.post("/ui/settings", json={"changes": {"reduced_motion": True}}, headers={"Origin": "http://tauri.localhost"})
        assert r.status == 200 and r.headers["Access-Control-Allow-Origin"] == "http://tauri.localhost"
    run(go)


def test_setup_checks_report_real_state_and_readiness(monkeypatch):
    async def go(c):
        r = await c.get("/setup/checks")
        body = await r.json()
        ids = {x["id"]: x for x in body["checks"]}
        assert {"ollama", "chat_model", "tts_model", "microphone", "gpu", "disk"} <= set(ids)
        assert ids["ollama"]["ok"] and ids["chat_model"]["ok"]
        assert body["ready"] == all(x["ok"] for x in body["checks"] if x["level"] == "required")
    run(go)


def test_missing_model_offers_a_pull():
    installed = ["llama3.2:3b"]
    check = setup_checks.check_chat_model(installed, "qwen3:4b-instruct")
    assert not check["ok"] and check["action"] == "pull"
    assert setup_checks.check_chat_model(None, "x")["ok"] is False


def test_status_is_local_only(monkeypatch):
    # /status carries what MBUDDY last heard and said; the LAN must not read it.
    import asyncio as _asyncio

    import pytest as _pytest
    from aiohttp import web as _web

    from api.status import status_handler

    monkeypatch.delenv("MBUDDY_DASHBOARD_ALLOW_LAN", raising=False)

    class LanRequest:
        query = {}
        remote = "192.168.1.50"

    with _pytest.raises(_web.HTTPForbidden):
        _asyncio.run(status_handler(LanRequest()))


def test_conversation_comes_from_the_backend_record(monkeypatch):
    # The app window can be closed while the buddy talks; on reopening, the
    # conversation must come from the backend, voice and typed turns alike.
    import api.ui_api as ui_api

    log = [{"time": f"2026-09-27 12:{i:02d}", "user": f"q{i}", "mbuddy": f"a{i}", "lang": "en"} for i in range(5)]
    monkeypatch.setattr(ui_api, "_live_memory", lambda: {"conversation_log": log})

    async def go(c):
        r = await c.get("/ui/conversation?limit=2")
        body = await r.json()
        assert body["turns"] == [{"time": "2026-09-27 12:03", "you": "q3", "me": "a3"},
                                 {"time": "2026-09-27 12:04", "you": "q4", "me": "a4"}]
        r = await c.get("/ui/conversation?limit=oops")
        assert len((await r.json())["turns"]) == 5
    run(go)


def test_conversation_comes_from_the_backend_record(monkeypatch):
    # The app window can be closed while the buddy talks; on reopening, the
    # conversation must come from the backend, voice and typed turns alike.
    import api.ui_api as ui_api

    log = [{"time": f"2026-09-27 12:{i:02d}", "user": f"q{i}", "mbuddy": f"a{i}", "lang": "en"} for i in range(5)]
    monkeypatch.setattr(ui_api, "_live_memory", lambda: {"conversation_log": log})

    async def go(c):
        r = await c.get("/ui/conversation?limit=2")
        body = await r.json()
        assert body["turns"] == [{"time": "2026-09-27 12:03", "you": "q3", "me": "a3"},
                                 {"time": "2026-09-27 12:04", "you": "q4", "me": "a4"}]
        r = await c.get("/ui/conversation?limit=oops")
        assert len((await r.json())["turns"]) == 5
    run(go)


def test_abilities_come_from_the_real_tools_and_settings():
    async def go(c):
        r = await c.get("/ui/abilities")
        items = (await r.json())["abilities"]
        states = {i["tool"]: i["state"] for i in items}
        assert states["set_timer"] == "on" and states["media_control"] == "asks first"
        assert items[-1]["state"] == "not yet"
        await c.post("/ui/settings", json={"changes": {"tool_permissions": {"timers": "off"}}}, headers={"Origin": "http://tauri.localhost"})
        items = (await (await c.get("/ui/abilities")).json())["abilities"]
        assert {i["tool"]: i["state"] for i in items}["set_timer"] == "off"
    run(go)


def test_wardrobe_offers_locked_items_and_refuses_them(tmp_path, monkeypatch):
    from core import rewards, wardrobe
    monkeypatch.setenv("MBUDDY_REWARDS_PATH", str(tmp_path / "r.json"))
    monkeypatch.setenv("MBUDDY_WARDROBE_PATH", str(tmp_path / "w.json"))
    rewards.reset()
    wardrobe.reset()

    async def go(c):
        body = await (await c.get("/ui/wardrobe")).json()
        assert body["ok"] and body["character"] == "blink" and body["progress"]["level"] == 1
        halo = next(i for i in body["slots"]["crown"] if i["id"] == "halo")
        assert halo["unlocked"] is False and "level 6" in halo["need"]
        r = await c.post("/ui/wardrobe", json={"slot": "crown", "item": "halo"})
        assert r.status == 400 and "Not yet" in (await r.json())["error"]
        r = await c.post("/ui/wardrobe", json={"finish": "asis"})
        assert r.status == 200 and (await r.json())["finish"] == "asis"
        r = await c.post("/ui/wardrobe", json={"ink": "plaid"})
        assert r.status == 400
        r = await c.post("/ui/wardrobe", json={"reset": True}, headers={"Origin": "https://evil.example"})
        assert r.status == 403                                  # another website cannot dress the character
    run(go)
