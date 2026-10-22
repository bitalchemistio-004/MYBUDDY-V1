import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import activity_api
from core import activity as act


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_ACTIVITY_DIR", str(tmp_path / "a"))
    monkeypatch.setattr(act, "TRACKER", act.Tracker())
    monkeypatch.setattr(activity_api, "_require_local", lambda request: None)
    monkeypatch.setattr(act, "start", lambda: None)
    application = web.Application()
    activity_api.register(application)
    return application


async def _body(c):
    r = await (await c.get("/ui/activity/today")).json()
    assert r["ok"] and r["enabled"] is False and r["score"] is None and r["apps"] == []
    r = await (await c.post("/ui/activity/settings", json={"tracking": True})).json()
    assert r["settings"]["tracking"] is True
    bad = await c.post("/ui/activity/settings", json={"checkin_minutes": 1})
    assert bad.status == 400
    r = await (await c.post("/ui/activity/label", json={"key": "editor", "status": "work"})).json()
    assert r["status"] == "work"
    assert (await c.post("/ui/activity/label", json={"key": "../x", "status": "work"})).status == 400
    assert (await c.post("/ui/activity/label", json={"key": "editor", "status": "great"})).status == 400
    assert (await c.post("/ui/activity/answer", json={"id": 7, "answer": "work"})).status == 400
    assert (await c.get("/ui/activity/today?date=nope")).status == 400
    assert (await c.get("/ui/activity/icon?key=missing")).status == 404


def test_today_settings_label_and_answer(app):
    async def go():
        async with TestClient(TestServer(app)) as c:
            await _body(c)
    asyncio.run(go())
