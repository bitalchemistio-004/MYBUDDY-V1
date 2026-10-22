import asyncio
import json
import os
import sys
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import models_api
from core import cloud, cloud_brain, secret_store, ui_settings


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MBUDDY_CLOUD_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", "env-key-0123456789abcdef")
    monkeypatch.setattr(cloud_brain, "active", False)
    yield


def fake_cloud():
    seen = []

    async def chat(request):
        body = await request.json()
        seen.append((request.headers.get("Authorization"), body))
        if body["model"] == "blocked:1b":
            return web.json_response({"error": "upgrade"}, status=402)
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
            await resp.prepare(request)
            await resp.write(json.dumps({"message": {"content": "He"}, "done": False}).encode() + b"\n")
            await resp.write(json.dumps({"message": {"content": "llo"}, "done": True, "prompt_eval_count": 40, "eval_count": 3}).encode() + b"\n")
            return resp
        return web.json_response({"message": {"role": "assistant", "content": "Hello"}, "done": True, "prompt_eval_count": 50, "eval_count": 7})

    app = web.Application()
    app.router.add_post("/api/chat", chat)
    return app, seen


def run(test):
    async def go():
        upstream, seen = fake_cloud()
        async with TestServer(upstream) as srv:
            os.environ["OLLAMA_CLOUD_BASE_URL"] = f"http://{srv.host}:{srv.port}"
            try:
                async with TestClient(TestServer(cloud_brain.build_app())) as c:
                    await test(c, seen)
            finally:
                os.environ.pop("OLLAMA_CLOUD_BASE_URL", None)
    asyncio.run(go())


def test_the_proxy_names_the_chosen_model_adds_the_key_and_counts_the_tokens():
    async def go(c, seen):
        r = await c.post("/api/chat", json={"model": "qwen3:4b-instruct", "stream": False, "messages": [{"role": "user", "content": "hi"}]})
        assert (await r.json())["message"]["content"] == "Hello"
        auth, body = seen[-1]
        assert body["model"] == "gemma4:31b" and auth == "Bearer env-key-0123456789abcdef"
        s = await c.post("/api/chat", json={"model": "qwen3:4b-instruct", "stream": True, "messages": []})
        assert "llo" in await s.text()
    run(go)
    u = cloud.usage_report(1)
    assert u["total"]["calls"] == 2 and u["total"]["tokens_in"] == 90 and u["total"]["tokens_out"] == 10
    assert u["by_purpose"][0]["name"] == "brain" and u["by_model"][0]["name"] == "gemma4:31b"


def test_housekeeping_calls_cost_nothing_and_never_reach_the_cloud():
    async def go(c, seen):
        assert (await (await c.get("/api/version")).json())["version"] == "cloud-brain"
        assert (await (await c.get("/api/ps")).json())["models"][0]["name"] == "gemma4:31b"
        load = await (await c.post("/api/generate", json={"model": "x", "keep_alive": "30m"})).json()
        assert load["done"] and load["load_duration"] == 0
        warm = await (await c.post("/api/chat", json={"model": "x", "options": {"num_predict": 1}, "messages": []})).json()
        assert warm["done"] and warm["message"]["content"] == ""
        assert seen == []
    run(go)
    assert cloud.usage_report(1)["total"]["calls"] == 0


def test_a_plan_refusal_passes_through_and_is_counted_as_failed():
    async def go(c, seen):
        ui_settings.update({"cloud": {"brain_model": "blocked:1b"}})
        r = await c.post("/api/chat", json={"stream": False, "messages": []})
        assert r.status == 402
    run(go)
    assert cloud.usage_report(1)["total"]["failed"] == 1


def test_only_this_pc_without_a_browser_origin_may_use_the_proxy():
    async def go(c, seen):
        r = await c.post("/api/chat", json={"messages": []}, headers={"Origin": "https://evil.example"})
        assert r.status == 403 and seen == []
    run(go)


def test_no_key_means_no_call():
    async def go(c, seen):
        os.environ.pop("OLLAMA_CLOUD_API_KEY", None)
        r = await c.post("/api/chat", json={"stream": False, "messages": []})
        assert r.status == 401 and seen == []
    run(go)


def test_the_choice_is_read_once_and_a_change_asks_for_a_restart():
    assert cloud_brain.mode_state() == {"saved": "local", "running": "local", "restart_needed": False, "why": ""}
    ui_settings.update({"brain_mode": "cloud"})
    st = cloud_brain.mode_state()
    assert st["saved"] == "cloud" and st["running"] == "local" and st["restart_needed"] is True
    with pytest.raises(ui_settings.SettingsError):
        ui_settings.update({"brain_mode": "moon"})


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_the_saved_key_is_encrypted_and_read_back_and_beats_the_env_key(tmp_path):
    cloud.save_key("saved-key-0123456789abcdef")
    raw = (tmp_path / "secrets" / "ollama_cloud_key.secret").read_bytes()
    assert b"saved-key" not in raw
    assert cloud._key() == "saved-key-0123456789abcdef" and cloud.key_source() == "app"
    cloud.remove_key()
    assert cloud.key_source() == "env" and not secret_store.exists(cloud.KEY_NAME)


def _app():
    app = web.Application()
    models_api.register(app)
    return app


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(models_api, "_require_local", lambda request: None)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_a_key_is_tried_before_it_is_kept_and_is_never_sent_back(local, monkeypatch):
    results = iter([{"valid": False, "model_ok": False, "why": "Ollama refused this key."},
                    {"valid": True, "model_ok": True, "why": ""}])
    monkeypatch.setattr(cloud, "validate_key", lambda key, model=None: next(results))

    async def go():
        async with TestClient(TestServer(_app())) as c:
            bad = await c.post("/ui/models/key", json={"key": "wrong-key-0123456789abcdef"})
            assert bad.status == 400 and cloud.key_source() == "env"
            raw = await (await c.post("/ui/models/key", json={"key": "good-key-0123456789abcdef"})).text()
            assert "good-key" not in raw and json.loads(raw)["cloud"]["key_source"] == "app"
            assert cloud._key() == "good-key-0123456789abcdef"
            out = await (await c.post("/ui/models/key", json={"action": "remove"})).json()
            assert out["cloud"]["key_source"] == "env"
    asyncio.run(go())


def test_the_cloud_brain_needs_a_key_and_the_usage_page_adds_up(local, monkeypatch):
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    for model, purpose, a, b in [("gemma4:31b", "brain", 1000, 200), ("gemma4:31b", "brain", 3000, 400), ("gpt-oss:120b", "bigger model", 500, 100)]:
        cloud.record_call(model, purpose, a, b, 1.0)

    async def go():
        async with TestClient(TestServer(_app())) as c:
            assert (await c.post("/ui/models", json={"brain_mode": "cloud"})).status == 400
            u = (await (await c.get("/ui/models/usage?days=7")).json())["usage"]
            assert u["total"]["calls"] == 3 and u["total"]["tokens_in"] == 4500 and len(u["by_day"]) == 7
            assert u["by_model"][0]["name"] == "gemma4:31b" and u["by_model"][0]["usd"] == round((4000 * 0.14 + 600 * 0.40) / 1e6, 4)
            assert u["unpriced"] == ["gpt-oss:120b"] and u["by_day"][-1]["calls"] == 3
    asyncio.run(go())


def test_the_plan_check_names_each_answer(monkeypatch):
    class R:
        def __init__(self, code):
            self.status_code, self.content = code, b"{}"

        def json(self):
            return {"prompt_eval_count": 5, "eval_count": 2}

    monkeypatch.setattr(cloud.requests, "post", lambda url, **k: R(402 if k["json"]["model"].startswith("big") else 200))
    assert cloud.check_plan(["gemma4:31b", "big:1t"]) == {"gemma4:31b": "ok", "big:1t": "not in plan"}


def test_a_named_model_can_be_tried_and_is_counted_as_a_preview(monkeypatch):
    seen = []

    class R:
        status_code, content = 200, b"{}"

        def json(self):
            return {"message": {"content": "I am good at tools."}, "prompt_eval_count": 20, "eval_count": 8}

    def post(url, **k):
        seen.append(k["json"]["model"])
        return R()

    monkeypatch.setattr(cloud.requests, "post", post)
    out = cloud.preview("gemma4:31b", "What are you good at?")
    assert out["ok"] and out["reply"] == "I am good at tools." and seen == ["gemma4:31b"]
    assert out["usd"] == round((20 * 0.14 + 8 * 0.40) / 1e6, 6)
    assert cloud.preview("", "x")["ok"] is False and cloud.preview("gemma4:31b", "  ")["ok"] is False
    assert cloud.usage_report(1)["by_purpose"][0]["name"] == "preview"


def test_the_notch_is_a_valid_look_and_the_default():
    assert "notch" in ui_settings.POPUP_STYLES and ui_settings.DEFAULTS["popup_style"] == "corner"  # the corner buddy is the default on purpose
    assert ui_settings.update({"notch_hover": False})["notch_hover"] is False


def test_switching_to_the_cloud_stops_the_local_model_first_and_back_starts_it(monkeypatch):
    from brain import ollama_service
    order = []
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(cloud_brain, "_ensure_proxy", lambda: cloud_brain._server.update(runner=object()) or True)
    monkeypatch.setattr(cloud_brain, "_original_base", [None, False])
    monkeypatch.setattr(ollama_service, "shutdown", lambda: order.append(("free local", os.environ.get("OLLAMA_BASE_URL"))))
    monkeypatch.setattr(ollama_service, "ensure_running", lambda *a, **k: order.append("start local") or (True, ""))
    monkeypatch.setattr(ollama_service, "warm", lambda *a, **k: order.append("warm"))
    monkeypatch.setattr(cloud_brain, "_server", {})
    ui_settings.update({"brain_mode": "cloud"})
    said = cloud_brain.apply_now()
    assert "stopped" in said and cloud_brain.active is True
    assert order == [("free local", None)]                       # the local model was freed while the address still pointed at it
    assert os.environ["OLLAMA_BASE_URL"].endswith("/v1") and "5052" in os.environ["OLLAMA_BASE_URL"]
    assert cloud_brain.mode_state()["restart_needed"] is False
    ui_settings.update({"brain_mode": "local"})
    assert "Back to the model on this PC" in cloud_brain.apply_now()
    assert "OLLAMA_BASE_URL" not in os.environ and cloud_brain.active is False
    import time
    for _ in range(40):
        if "warm" in order:
            break
        time.sleep(0.05)
    assert "start local" in order and "warm" in order
    assert cloud_brain.apply_now() == ""                           # nothing to change


def test_the_reply_adapter_follows_the_brains_address_after_a_switch(monkeypatch):
    from brain import brain_adapters as ba
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OLLAMA_CHAT_MODEL", "qwen3:4b-instruct")
    adapter = ba.GenericOpenAIAdapter("ollama")
    first = adapter.client
    adapter._follow_env()
    assert adapter.client is first                                       # nothing changed: the same client
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:5052/v1")    # the cloud brain took over
    adapter._follow_env()
    assert adapter.client is not first and "5052" in str(adapter.client.base_url)
    monkeypatch.delenv("OLLAMA_BASE_URL")                                 # and back to this PC
    adapter._follow_env()
    assert "11434" in str(adapter.client.base_url)


def test_the_cloud_brain_stops_at_the_daily_limit():
    ui_settings.update({"cloud": {"daily_token_cap": 100}})
    cloud.record_call("gemma4:31b", "brain", 90, 20, 1.0)                   # 110 tokens used today

    async def go(c, seen):
        r = await c.post("/api/chat", json={"stream": False, "messages": []})
        assert r.status == 429 and "limit" in (await r.json())["error"] and seen == []
    run(go)
