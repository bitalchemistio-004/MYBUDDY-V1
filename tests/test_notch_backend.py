"""What the notch's views need from the backend: media controls, the real output level, and "speak it"."""
import asyncio
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import ui_api
from api.websocket_server import MBUDDYSession
from brain import pc_windows
from core import ui_settings


def client(monkeypatch):
    monkeypatch.setattr(ui_api, "_require_local", lambda request: None)
    app = web.Application()
    app.router.add_get("/ui/audio-level", ui_api.audio_level_handler)
    app.router.add_post("/ui/dictate", ui_api.dictate_handler)
    return TestClient(TestServer(app))


def test_the_output_level_is_the_real_reading_or_nothing(monkeypatch):
    monkeypatch.setattr(pc_windows, "output_level", lambda: 0.42)

    async def go():
        async with client(monkeypatch) as c:
            assert (await (await c.get("/ui/audio-level")).json())["level"] == 0.42
    asyncio.run(go())


def test_speak_it_returns_the_next_utterance_and_refuses_when_the_mic_is_off(monkeypatch, tmp_path):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    ui_settings.reset_cache()
    fake = SimpleNamespace(_capture=None)
    fake.capture_next_utterance = lambda seconds: MBUDDYSession.capture_next_utterance(fake, seconds)
    monkeypatch.setattr(ui_api, "active_session", lambda: fake)

    async def go():
        async with client(monkeypatch) as c:
            async def say_it():
                await asyncio.sleep(0.2)
                fake._capture.set_result("buy milk tomorrow")      # what finalize_utterance does with the recognised text
            task = asyncio.ensure_future(say_it())
            got = await (await c.post("/ui/dictate", json={"seconds": 5})).json()
            await task
            assert got == {"ok": True, "text": "buy milk tomorrow"}
            ui_settings.update({"microphone_on": False})
            off = await c.post("/ui/dictate", json={})
            assert off.status == 409
    asyncio.run(go())
    ui_settings.reset_cache()


def test_a_timer_picked_on_the_dial_starts_and_can_be_cancelled(monkeypatch, tmp_path):
    from brain import tools
    manager = tools.TimerManager(db_path=tmp_path / "timers.json")
    monkeypatch.setattr(tools, "_timer_manager", manager)
    monkeypatch.setattr(ui_api, "_require_local", lambda request: None)

    async def go():
        app = web.Application()
        app.router.add_post("/ui/timers", ui_api.timers_handler)
        app.router.add_delete("/ui/timers", ui_api.timers_handler)
        async with TestClient(TestServer(app)) as c:
            assert (await c.post("/ui/timers", json={"seconds": 2})).status == 400          # too short
            started = await (await c.post("/ui/timers", json={"seconds": 600})).json()
            assert started["ok"] and len(manager.list_timers()) == 1 and manager.list_timers()[0].duration_seconds == 600
            assert (await c.delete("/ui/timers", json={"key": started["key"]})).status == 200
            assert manager.list_timers() == []
            assert (await c.delete("/ui/timers", json={"key": "nope"})).status == 404
    asyncio.run(go())
