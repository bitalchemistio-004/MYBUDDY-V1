"""S98: new text is composed with room for it, never squeezed into a tool
call; model errors are never a silent "no action". No real model, Word or
file system outside tmp is used."""
import asyncio

import pytest

from brain import agent, composer
from brain import pc_actions as pa
from brain.connectors import office
from brain.tools import ToolFailure


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def test_compose_returns_the_written_text(monkeypatch):
    sent = {}

    def post(url, json, timeout):
        sent.update(json)
        return FakeResponse({"message": {"content": "  Once upon a time, a robot listened.  "}})
    monkeypatch.setattr(composer.requests, "post", post)
    assert composer.compose("a short story about a robot") == "Once upon a time, a robot listened."
    assert sent["options"]["num_predict"] > 300  # room to write, unlike the tool step


def test_compose_failures_are_honest(monkeypatch):
    monkeypatch.setattr(composer.requests, "post", lambda *a, **k: FakeResponse({"error": "model not found"}))
    assert isinstance(composer.compose("a poem"), ToolFailure)
    assert isinstance(composer.compose("   "), ToolFailure)


def test_word_write_new_text_is_composed(monkeypatch):
    monkeypatch.setattr(composer, "compose", lambda instruction, max_words=400: f"[written: {instruction}]")
    assert office._text_for({"source": "write_new", "write_about": "a story about a cat"}) == \
        "[written: a story about a cat]"


def test_create_file_composes_when_asked_to_write_something_new(monkeypatch, tmp_path):
    monkeypatch.setattr(pa, "BASE_DIR", tmp_path)
    monkeypatch.setattr(composer, "compose", lambda instruction, max_words=400: "A letter to Sam.")
    out = pa.run("create_file", {"name": "letter", "write_about": "a thank-you letter to Sam"})
    assert out.startswith("Created") and (tmp_path / "letter.txt").read_text(encoding="utf-8") == "A letter to Sam."


def test_a_cut_off_tool_call_is_retried_with_room(monkeypatch):
    budgets = []

    def post(url, json, timeout):
        budgets.append(json["options"]["num_predict"])
        if len(budgets) == 1:
            return FakeResponse({"error": 'invalid tool call arguments for "word_write": unexpected end of JSON input'})
        return FakeResponse({"message": {"tool_calls": [{"function": {"name": "word_write",
                                                                      "arguments": {"source": "write_new"}}}]}})
    monkeypatch.setattr(agent.requests, "post", post)
    calls = asyncio.run(agent._ask_model([{"role": "user", "content": "x"}], [], 240))
    assert budgets == [240, 700] and calls == [{"name": "word_write", "args": {"source": "write_new"}}]


def test_a_model_error_is_raised_not_silenced(monkeypatch):
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: FakeResponse({"error": "out of memory"}))
    with pytest.raises(RuntimeError):
        asyncio.run(agent._ask_model([{"role": "user", "content": "x"}], []))
