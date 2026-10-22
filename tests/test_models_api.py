import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import models_api
from core import cloud, ui_settings


def _app(tmp_path, monkeypatch, key="k-secret"):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MBUDDY_CLOUD_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    if key:
        monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", key)
    else:
        monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    monkeypatch.setattr(models_api, "_require_local", lambda request: None)
    app = web.Application()
    models_api.register(app)
    return app


def test_the_state_never_contains_the_key_and_says_what_is_on(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    async def go():
        async with TestClient(TestServer(app)) as c:
            raw = await (await c.get("/ui/models")).text()
            r = json.loads(raw)
            assert "k-secret" not in raw
            assert r["cloud"]["key_present"] and r["cloud"]["usable"] and r["cloud"]["model"] == "gpt-oss:120b"
            assert r["work_on"] == "pc" and r["usage_today"]["calls"] == 0
    import asyncio
    asyncio.run(go())


def test_switching_the_cloud_off_and_choosing_models_is_validated(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await (await c.post("/ui/models", json={"cloud": {"enabled": False}})).json()
            assert r["ok"] and r["cloud"]["usable"] is False and r["cloud"]["enabled"] is False
            bad = await c.post("/ui/models", json={"cloud": {"model": "no way!"}})
            assert bad.status == 400
            assert (await c.post("/ui/models", json={"work_on": "moon"})).status == 400
            ok = await (await c.post("/ui/models", json={"work_on": "phone"})).json()
            assert ok["work_on"] == "phone"
            assert (await c.post("/ui/models", json={"character": "void"})).status == 400   # only these two keys
    import asyncio
    asyncio.run(go())


def test_the_test_button_reports_failure_in_words(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, key=None)

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await (await c.post("/ui/models/test")).json()
            assert r["ok"] and r["test"]["ok"] is False and "key" in r["test"]["why"].lower()
    import asyncio
    asyncio.run(go())
