import json

import pytest

from brain.connectors import cloud as cloud_ability
from core import cloud, ui_settings


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data if data is not None else {}

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MBUDDY_CLOUD_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", "test-key-never-shown")
    cloud._unavailable.clear()
    yield


def answer(text="Hello", tin=12, tout=5):
    return FakeResponse(200, {"message": {"content": text}, "prompt_eval_count": tin, "eval_count": tout})


def test_a_call_returns_the_text_and_is_logged(monkeypatch):
    seen = {}
    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        return answer("Forty-two.")
    monkeypatch.setattr(cloud.requests, "post", post)
    r = cloud.chat([{"role": "user", "content": "meaning?"}], purpose="ask")
    assert r.text == "Forty-two." and r.model == "gpt-oss:120b" and (r.tokens_in, r.tokens_out) == (12, 5)
    assert seen["url"].endswith("/api/chat") and seen["auth"] == "Bearer test-key-never-shown"
    u = cloud.usage(1)
    assert u["calls"] == 1 and u["tokens_in"] == 12 and u["by_model"]["gpt-oss:120b"]["calls"] == 1
    assert "test-key" not in json.dumps(cloud.status()) and "test-key" not in json.dumps(u)


def test_no_key_or_switched_off_never_calls(monkeypatch):
    monkeypatch.setattr(cloud.requests, "post", lambda *a, **k: pytest.fail("must not call"))
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY")
    with pytest.raises(cloud.CloudError) as e:
        cloud.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "no_key"
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", "k")
    ui_settings.update({"cloud": {"enabled": False}})
    with pytest.raises(cloud.CloudError) as e:
        cloud.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "off" and "switched off" in str(e.value)


def test_a_model_not_on_the_plan_is_left_alone_for_a_while(monkeypatch):
    calls = []
    def post(*a, **k):
        calls.append(1)
        return FakeResponse(402, {"error": "This model is not in the Free plan."})
    monkeypatch.setattr(cloud.requests, "post", post)
    with pytest.raises(cloud.CloudError) as e:
        cloud.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "plan" and "isn't on this cloud plan" in str(e.value)
    with pytest.raises(cloud.CloudError):
        cloud.chat([{"role": "user", "content": "x"}])
    assert len(calls) == 1                                           # the second did not go out
    assert cloud.usage(1)["failed"] == 1


def test_a_native_tool_call_answer_is_turned_into_text(monkeypatch):
    monkeypatch.setattr(cloud.requests, "post", lambda *a, **k: FakeResponse(200, {"message": {"content": "", "tool_calls": [
        {"function": {"name": "set_timer", "arguments": {"minutes": 2}}}]}, "prompt_eval_count": 3, "eval_count": 4}))
    assert json.loads(cloud.chat([{"role": "user", "content": "x"}]).text) == {"tool": "set_timer", "args": {"minutes": 2}}


def test_the_daily_cap_stops_calls(monkeypatch):
    ui_settings.update({"cloud": {"daily_token_cap": 20}})
    monkeypatch.setattr(cloud.requests, "post", lambda *a, **k: answer("ok", 15, 10))
    cloud.chat([{"role": "user", "content": "x"}])
    with pytest.raises(cloud.CloudError) as e:
        cloud.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "cap"


def test_offline_is_said_plainly(monkeypatch):
    def boom(*a, **k):
        raise cloud.requests.ConnectionError("down")
    monkeypatch.setattr(cloud.requests, "post", boom)
    with pytest.raises(cloud.CloudError) as e:
        cloud.chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "offline"


def test_the_ability_answers_in_full_and_fails_in_words(monkeypatch):
    monkeypatch.setattr(cloud.requests, "post", lambda *a, **k: answer("Order B, A, C, D."))
    assert cloud_ability.ask_bigger_model({"task": "schedule four meetings"}) == "Order B, A, C, D."
    monkeypatch.setattr(cloud.requests, "post", lambda *a, **k: FakeResponse(403, {}))
    out = cloud_ability.ask_bigger_model({"task": "again"})
    assert str(out).startswith("FAILED") and "my own model" in str(out)


def test_what_would_be_sent_is_named_before_it_is_sent():
    ab = cloud_ability.ABILITIES[1]
    assert ab.risk == "critical"
    assert "clipboard" in ab.preview({"source": "clipboard"}) and "gpt-oss:120b" in ab.preview({"source": "clipboard"})
    assert "screenshot" in ab.preview({"source": "screen"}) and "gemma4:31b" in ab.preview({"source": "screen"})
    assert "contract.docx" in ab.preview({"source": "C:/Users/x/Documents/contract.docx"})


def test_a_file_is_read_only_inside_the_users_folder_and_only_text_kinds(tmp_path, monkeypatch):
    out = cloud_ability.ask_bigger_model_about({"task": "summarise", "source": "C:/Windows/system.ini"})
    assert str(out).startswith("FAILED") and "user folder" in str(out)
    f = cloud_ability.Path.home() / "mbuddy_test_note.exe"
    out = cloud_ability._read_file(str(f))
    assert str(out).startswith("FAILED")
