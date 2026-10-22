import asyncio
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import phone_api
from brain import pc_actions
from core import phone_actions as pa


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_PHONE_ACTIONS_PATH", str(tmp_path / "phone.json"))
    yield


def test_only_real_actions_with_their_fields_are_queued():
    a = pa.add("alarm", {"hour": 7, "minute": 30, "label": "Gym", "evil": "x"})
    assert a["params"] == {"hour": 7, "minute": 30, "label": "Gym"} and pa.describe(a) == "alarm at 07:30 (Gym)"
    assert pa.describe(pa.add("timer", {"seconds": 600})) == "timer for 10 min"
    assert pa.add("call", {"number": "+212 600-111 222"})["params"]["number"] == "+212600111222"
    for kind, bad in (("alarm", {"hour": 25, "minute": 0}), ("timer", {"seconds": 0}), ("call", {"number": "x"}), ("calendar", {}), ("navigate", {}), ("format", {})):
        with pytest.raises(ValueError):
            pa.add(kind, bad)


def test_an_action_is_offered_until_the_phone_answers_it_and_old_ones_go():
    a = pa.add("search", {"query": "pasta recipe"})
    assert [r["id"] for r in pa.pending()] == [a["id"]]
    assert pa.mark(a["id"], "done") and pa.pending() == [] and not pa.mark("nope", "done") and not pa.mark(a["id"], "explode")
    old = pa.add("search", {"query": "old"}, now=time.time() - 4 * 86400)
    assert old["id"] not in [r["id"] for r in pa.pending()]


def test_the_ability_queues_for_the_phone_and_refuses_nonsense():
    out = pc_actions.REGISTRY["phone_do"].execute({"kind": "timer", "seconds": "300", "label": "tea"})
    assert "timer for 5 min" in out and len(pa.pending()) == 1
    assert "FAILED" in str(pc_actions.REGISTRY["phone_do"].execute({"kind": "alarm", "hour": 99}))
    assert pc_actions.REGISTRY["phone_do"].risk == "low" and pc_actions.REGISTRY["phone_do"].topic == "phone"


def test_the_api_lists_and_the_phone_answers(monkeypatch):
    monkeypatch.setattr(phone_api, "_require_local", lambda request: None)
    a = pa.add("navigate", {"place": "Central Station"})

    async def go():
        app = web.Application()
        phone_api.register(app)
        async with TestClient(TestServer(app)) as c:
            assert [x["id"] for x in (await (await c.get("/ui/phone-actions")).json())["actions"]] == [a["id"]]
            assert (await c.post("/ui/phone-actions", json={"id": a["id"], "status": "done"})).status == 200
            assert (await (await c.get("/ui/phone-actions")).json())["actions"] == []
            assert (await c.post("/ui/phone-actions", json={"id": "zzz", "status": "done"})).status == 404
    asyncio.run(go())


def test_the_gateway_lets_a_phone_read_and_answer_but_nothing_else_new():
    from api import remote_gateway as g
    assert g.allowed("GET", "ui/phone-actions") and g.allowed("POST", "ui/phone-actions")
    assert g._filter_body("ui/phone-actions", {"id": "a", "status": "done", "x": 1}) == {"id": "a", "status": "done"}
    assert not g.allowed("POST", "ui/notifications") and not g.allowed("POST", "ui/dictate")


def test_the_gateway_serves_the_phone_tools_page_and_a_shortcut_to_it():
    from api import remote_gateway as g

    async def go():
        async with TestClient(TestServer(g.build_app("http://127.0.0.1:1"))) as c:
            page = await c.get("/phone")
            html = await page.text()
            assert page.status == 200 and "Phone tools" in html and "js/phone-page.js" in html
            manifest = await (await c.get("/manifest.webmanifest")).json()
            assert manifest["shortcuts"][0]["url"] == "phone"
            worker = await (await c.get("/sw.js")).text()
            assert '"phone"' in worker and "js/phone-actions.js" in worker                 # kept at install, so it opens with no PC
            assert (await c.get("/js/phone-actions.js")).status == 200 and (await c.get("/css/phone.css")).status == 200
    asyncio.run(go())


def test_a_request_for_the_phone_is_not_sent_to_the_pcs_timer():
    from brain import agent
    for text in ("put a 5 minute timer on my phone", "remind my phone to call mum", "set an alarm on the phone"):
        assert agent._PHONE_TARGET.search(text) and agent._PHONE_DOES.search(text)
    for text in ("set a timer for 5 minutes", "what is on my phone", "my phone is dead"):
        assert not (agent._PHONE_TARGET.search(text) and agent._PHONE_DOES.search(text))


def test_the_android_app_knows_every_kind_of_action_the_pc_can_queue():
    from pathlib import Path
    kotlin = (Path(__file__).resolve().parents[1] / "android" / "app" / "src" / "main" / "kotlin" / "app" / "mbuddy" / "mobile" / "Actions.kt").read_text(encoding="utf-8")
    for kind in pa.KINDS:
        assert f'"{kind}"' in kotlin, f"Actions.kt has no '{kind}'"          # the PC, the page and the app must agree on the kinds
    page = (Path(__file__).resolve().parents[1] / "shell" / "ui" / "js" / "phone-actions.js").read_text(encoding="utf-8")
    for kind in pa.KINDS:
        assert f'"{kind}"' in page, f"phone-actions.js has no '{kind}'"
