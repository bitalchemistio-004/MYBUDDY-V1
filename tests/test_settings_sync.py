"""Every window follows settings changed elsewhere: the revision in /ui/live (S81)."""
import asyncio
import json
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import setup_checks, ui_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    path = tmp_path / "app_settings.json"
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(path))
    ui_settings.reset_cache()
    monkeypatch.setattr(setup_checks, "ollama_models", lambda: ["qwen3:4b-instruct"])
    yield path
    ui_settings.reset_cache()


def test_revision_is_stable_without_changes():
    first = ui_settings.revision()
    ui_settings.all_settings(); ui_settings.get("microphone_on")
    assert ui_settings.revision() == first == ui_settings.revision()


def test_revision_changes_after_a_write():
    before = ui_settings.revision()
    ui_settings.update({"microphone_on": False})
    after = ui_settings.revision()
    assert after != before
    assert ui_settings.revision() == after  # and is stable again


def test_a_refused_write_does_not_change_it():
    before = ui_settings.revision()
    with pytest.raises(ui_settings.SettingsError):
        ui_settings.update({"microphone_on": "off"})
    assert ui_settings.revision() == before


def test_revision_changes_when_another_process_writes_the_file(isolated):
    ui_settings.update({"microphone_on": True})
    before = ui_settings.revision()
    isolated.write_text(json.dumps({"microphone_on": False}), encoding="utf-8")
    st = isolated.stat()
    os.utime(isolated, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))  # a clearly newer mtime
    assert ui_settings.revision() != before
    assert ui_settings.get("microphone_on") is False


def test_live_snapshot_carries_the_revision(monkeypatch, tmp_path):
    from api import ui_api
    import core.companion_settings as cs_mod
    # a scratch companion store, never the real ~/.buddy one
    monkeypatch.setattr(cs_mod, "_companion_settings", cs_mod.CompanionSettings(path=tmp_path / "companion.json"))
    a = ui_api.live_snapshot()["settings_rev"]
    assert ui_api.live_snapshot()["settings_rev"] == a
    ui_settings.update({"character": "cinder"})
    b = ui_api.live_snapshot()["settings_rev"]
    assert b != a
    cs_mod._companion_settings._inner_enabled = True  # the inner-life switch counts too (no file write)
    assert ui_api.live_snapshot()["settings_rev"] != b


def test_settings_only_query_skips_the_options():
    async def go():
        from api.ui_api import origin_guard, register_ui_routes
        app = web.Application(middlewares=[origin_guard])
        register_ui_routes(app)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/ui/settings?only=settings")
            body = await r.json()
            assert body["ok"] and "options" not in body and body["settings"]["microphone_on"] is True
            r = await c.get("/ui/settings")
            opts = (await r.json())["options"]
            assert opts["companion_style_suggestion"] == ui_settings.COMPANION_STYLE_SUGGESTION
            assert opts["profile_max"] == ui_settings.PROFILE_MAX
    asyncio.run(go())
