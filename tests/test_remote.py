import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import remote_gateway
from core import remote


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_REMOTE_PATH", str(tmp_path / "remote.json"))
    remote._code.update(value="", expires=0.0)
    remote._bad.update(count=0, locked_until=0.0)
    yield


# ── pairing and tokens ───────────────────────────────────────────────────
def pair_one(name="Pixel"):
    code = remote.new_code()["code"]
    token, why = remote.pair(code, name)
    assert token and not why
    return token


def test_a_phone_pairs_with_the_code_once_and_the_token_is_never_stored(tmp_path):
    code = remote.new_code()["code"]
    token, _ = remote.pair(code, "Pixel 8")
    assert token and len(token) > 30
    assert token not in (tmp_path / "remote.json").read_text(encoding="utf-8")        # only a hash is kept
    assert remote.verify(token)["name"] == "Pixel 8"
    assert remote.pair(code, "Second")[0] is None                                    # the code worked once
    assert remote.verify("not-a-token") is None and remote.verify("") is None


def test_five_wrong_codes_lock_pairing(monkeypatch):
    remote.new_code()
    for _ in range(4):
        assert remote.pair("000000", "x")[1] == "That code isn't right."
    assert "locked" in remote.pair("000000", "x")[1]
    right = remote.new_code()["code"]
    assert remote.pair(right, "x")[0] is None                                          # locked, even with a right code


def test_an_expired_code_does_not_work():
    code = remote.new_code()["code"]
    remote._code["expires"] = 1.0
    assert remote.pair(code, "x")[0] is None and remote.current_code() is None


def test_pausing_cuts_every_phone_off_and_resuming_brings_them_back():
    token = pair_one()
    remote.set_paused(True)
    assert remote.verify(token) is None
    remote.set_paused(False)
    assert remote.verify(token) is not None


def test_revoking_removes_a_phone_for_good():
    token = pair_one()
    device = remote.verify(token)
    assert remote.revoke(device["id"]) and remote.verify(token) is None
    assert remote.revoke("nope") is False


def test_the_state_has_no_secrets():
    pair_one("Mine")
    text = json.dumps(remote.state())
    assert "hash" not in text and remote.state()["devices"][0]["name"] == "Mine"


# ── the gateway ──────────────────────────────────────────────────────────
def fake_backend():
    seen = []

    async def echo(request):
        body = None
        if request.method == "POST":
            body = await request.json()
        seen.append((request.method, request.path, body))
        return web.json_response({"ok": True, "path": request.path, "body": body})

    async def live(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(json.dumps({"awake": True}))
        await asyncio.sleep(0.3)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ui/live", live)
    app.router.add_route("*", "/{tail:.*}", echo)
    return app, seen


def run(coro):
    return asyncio.run(coro)


async def with_gateway(test):
    backend, seen = fake_backend()
    async with TestServer(backend) as b:
        gw = remote_gateway.build_app(f"http://{b.host}:{b.port}")
        async with TestClient(TestServer(gw)) as c:
            await test(c, seen)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_nothing_works_without_a_token_and_pairing_needs_the_pc_code():
    async def t(c, seen):
        assert (await c.get("/remote/ui/overview")).status == 401
        assert (await c.post("/remote/control/chat", json={"text": "hi"})).status == 401
        bad = await c.post("/pair", json={"code": "123456", "name": "x"})
        assert bad.status == 403
        code = remote.new_code()["code"]
        ok = await c.post("/pair", json={"code": code, "name": "Pixel"})
        token = (await ok.json())["token"]
        r = await c.get("/remote/ui/overview", headers=bearer(token))
        assert r.status == 200 and (await r.json())["path"] == "/ui/overview"
        assert seen[-1][:2] == ("GET", "/ui/overview")
    run(with_gateway(t))


def test_the_allowlist_refuses_everything_else_even_with_a_valid_token():
    token = pair_one()

    async def t(c, seen):
        h = bearer(token)
        refused = [("POST", "/remote/ui/settings"), ("GET", "/remote/control/volume"), ("DELETE", "/remote/ui/learned"),
                   ("POST", "/remote/control/tunnel/start"), ("GET", "/remote/auth/tokens"), ("GET", "/remote/ui/apps"),
                   ("POST", "/remote/ui/tasks/../settings"), ("GET", "/remote/ui/../control/status")]
        for method, path in refused:
            r = await c.request(method, path, headers=h, json={} if method == "POST" else None)
            assert r.status in (403, 404), (method, path, r.status)
        assert seen == []                                                       # nothing reached the PC
    run(with_gateway(t))


def test_chat_from_a_phone_is_never_spoken_on_the_pc_and_only_work_on_can_be_switched():
    token = pair_one()

    async def t(c, seen):
        h = bearer(token)
        r = await c.post("/remote/control/chat", headers=h, json={"text": "what's on my calendar", "speak": True})
        assert (await r.json())["body"] == {"text": "what's on my calendar", "speak": False}
        assert (await c.post("/remote/control/chat", headers=h, json={"text": "  "})).status == 403
        ok = await c.post("/remote/ui/models", headers=h, json={"work_on": "phone"})
        assert ok.status == 200 and (await ok.json())["body"] == {"work_on": "phone"}
        assert (await c.post("/remote/ui/models", headers=h, json={"cloud": {"enabled": False}})).status == 403
        assert (await c.post("/remote/ui/models", headers=h, json={"work_on": "phone", "cloud": {}})).status == 403
    run(with_gateway(t))


def test_a_phone_can_answer_a_pending_action_and_add_a_task():
    token = pair_one()

    async def t(c, seen):
        h = bearer(token)
        assert (await c.post("/remote/ui/pending-action", headers=h, json={"decision": "yes"})).status == 200
        assert (await c.post("/remote/ui/tasks", headers=h, json={"title": "Call mum"})).status == 200
        assert (await c.post("/remote/ui/tasks/12/done", headers=h, json={})).status == 200
    run(with_gateway(t))


def test_pausing_locks_the_gateway_at_once():
    token = pair_one()

    async def t(c, seen):
        assert (await c.get("/remote/ui/overview", headers=bearer(token))).status == 200
        remote.set_paused(True)
        assert (await c.get("/remote/ui/overview", headers=bearer(token))).status == 423
        remote.set_paused(False)
        assert (await c.get("/remote/ui/overview", headers=bearer(token))).status == 200
    run(with_gateway(t))


def test_the_live_status_reaches_a_paired_phone_only():
    token = pair_one()

    async def t(c, seen):
        assert (await c.get("/remote/ui/live")).status == 401
        ws = await c.ws_connect(f"/remote/ui/live?t={token}")
        msg = await ws.receive(timeout=3)
        assert json.loads(msg.data) == {"awake": True}
        await ws.close()
    run(with_gateway(t))


def test_the_pages_are_served_with_the_remote_flag_and_a_manifest():
    async def t(c, seen):
        page = await (await c.get("/")).text()
        assert "__MBUDDY_REMOTE__" in page and "manifest.webmanifest" in page
        m = await (await c.get("/manifest.webmanifest")).json()
        assert m["display"] == "standalone" and m["short_name"] == "MBUDDY"
        assert (await c.get("/js/spaces.js")).status == 200
        assert (await c.get("/health")).status == 200
    run(with_gateway(t))


def test_what_a_phone_did_is_kept_on_the_pc_for_the_away_screen():
    token = pair_one("Pixel")

    async def t(c, seen):
        h = bearer(token)
        await c.post("/remote/control/chat", headers=h, json={"text": "remind me to call mum tomorrow at nine"})
        await c.post("/remote/ui/pending-action", headers=h, json={"decision": "no"})
        await c.get("/remote/ui/overview", headers=h)                    # reading is not an action
    run(with_gateway(t))
    acts = remote.state()["actions"]
    assert [a["what"] for a in acts] == ["Answered no to a question", "Asked: remind me to call mum tomorrow at nine"]
    assert all(a["device"] == "Pixel" for a in acts)
