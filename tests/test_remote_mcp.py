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


def backend():
    seen = []

    async def overview(request):
        return web.json_response({"ok": True, "timers": [{"label": "10 minute timer", "remaining_s": 300}], "recent_actions": [{"result": "Timer started"}], "volume": 40})

    async def tasks_get(request):
        return web.json_response({"ok": True, "tasks": [{"title": "Call mum", "due_date": None}, {"title": "Send the invoice", "due_date": "2026-10-12"}]})

    async def tasks_post(request):
        body = await request.json()
        seen.append(("tasks", body))
        return web.json_response({"ok": True})

    async def chat(request):
        body = await request.json()
        seen.append(("chat", body))
        return web.json_response({"ok": True, "reply": "It is 2 PM.", "via": "LOCAL 4B"})

    app = web.Application()
    app.router.add_get("/ui/overview", overview)
    app.router.add_get("/ui/tasks", tasks_get)
    app.router.add_post("/ui/tasks", tasks_post)
    app.router.add_post("/control/chat", chat)
    return app, seen


def run(test):
    async def go():
        b, seen = backend()
        async with TestServer(b) as srv:
            gw = remote_gateway.build_app(f"http://{srv.host}:{srv.port}")
            async with TestClient(TestServer(gw)) as c:
                await test(c, seen)
    asyncio.run(go())


def token():
    code = remote.new_code()["code"]
    return remote.pair(code, "Pixel")[0]


def rpc(method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return msg


def test_the_handshake_and_the_tool_list():
    t = token()

    async def go(c, seen):
        r = await c.post(f"/mcp/{t}", json=rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "gallery"}}))
        data = await r.json()
        assert r.status == 200 and data["result"]["protocolVersion"] == "2025-06-18" and "tools" in data["result"]["capabilities"]
        assert data["result"]["serverInfo"]["name"] == "MBUDDY"
        old = await (await c.post(f"/mcp/{t}", json=rpc("initialize", {"protocolVersion": "1999-01-01"}))).json()
        assert old["result"]["protocolVersion"] == "2025-06-18"                      # an unknown version: ours is offered
        note = await c.post(f"/mcp/{t}", json=rpc("notifications/initialized", id_=None))
        assert note.status == 202 and await note.text() == ""
        tools = (await (await c.post(f"/mcp/{t}", json=rpc("tools/list"))).json())["result"]["tools"]
        assert {x["name"] for x in tools} == {"mbuddy_status", "mbuddy_tasks", "mbuddy_add_task", "mbuddy_ask"}
        assert all(x["inputSchema"]["type"] == "object" and len(x["description"]) < 120 for x in tools)   # short, for a small model
        assert (await (await c.post(f"/mcp/{t}", json=rpc("ping"))).json())["result"] == {}
    run(go)


def test_the_tools_read_and_act_on_the_pc_through_the_gateway():
    t = token()

    async def go(c, seen):
        def call(name, args=None):
            return c.post(f"/mcp/{t}", json=rpc("tools/call", {"name": name, "arguments": args or {}}, id_=7))
        status = (await (await call("mbuddy_status")).json())["result"]
        assert not status["isError"] and "10 minute timer" in status["content"][0]["text"]
        tasks = (await (await call("mbuddy_tasks")).json())["result"]["content"][0]["text"]
        assert "Call mum" in tasks and "Send the invoice (2026-10-12)" in tasks
        added = (await (await call("mbuddy_add_task", {"title": "Buy milk", "when": "tomorrow at 9"})).json())["result"]
        assert not added["isError"] and seen[-1] == ("tasks", {"title": "Buy milk", "when": "tomorrow at 9"})
        asked = (await (await call("mbuddy_ask", {"question": "what time is it", "speak": True})).json())["result"]
        assert asked["content"][0]["text"] == "It is 2 PM." and seen[-1] == ("chat", {"text": "what time is it", "speak": False})   # never spoken on the PC
        empty = (await (await call("mbuddy_add_task", {"title": " "})).json())["result"]
        assert empty["isError"] is True
        bad = await (await call("delete_everything")).json()
        assert bad["error"]["code"] == -32602
    run(go)
    assert [a["what"] for a in remote.recent_actions(5)][:2] == ["Asked (on-device model): what time is it", "Added a task (on-device model): Buy milk"]


def test_only_a_paired_unpaused_native_client_may_call_it():
    t = token()

    async def go(c, seen):
        assert (await c.post("/mcp/wrong-token", json=rpc("ping"))).status == 401
        assert (await c.get(f"/mcp/{t}")).status == 405                                   # no stream offered
        assert (await c.post(f"/mcp/{t}", json=rpc("ping"), headers={"Origin": "https://evil.example"})).status == 403
        assert (await c.post(f"/mcp/{t}", data="not json", headers={"Content-Type": "application/json"})).status == 400
        remote.set_paused(True)
        assert (await c.post(f"/mcp/{t}", json=rpc("ping"))).status == 423
        remote.set_paused(False)
        assert (await c.post(f"/mcp/{t}", json=rpc("ping"))).status == 200
        unknown = await (await c.post(f"/mcp/{t}", json=rpc("resources/list"))).json()
        assert unknown["error"]["code"] == -32601
    run(go)
