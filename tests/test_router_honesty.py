"""The router's own words for failed tools (live 2026-09-27: a failed
reminder became "That event doesn't exist", then repeated refusals)."""
import asyncio

import brain.agent as agent
import brain.llm_router as router
from brain.tools import ToolFailure
from core.state_manager import MBUDDYState


def reply_to(text, monkeypatch, results):
    async def fake_agent(user_text, history, memory, question_only=False):
        return results

    async def no_model(*a, **k):
        raise AssertionError("the model must not phrase a failed tool")
        yield ""  # pragma: no cover

    monkeypatch.setattr(agent, "run_agent", fake_agent)
    monkeypatch.setattr(router, "providers_include_ollama", lambda: True)
    monkeypatch.setattr(router, "get_brain_adapter", lambda *a, **k: type("A", (), {"stream_chat": no_model, "is_ready": lambda s: True})())

    async def go():
        return "".join([c async for c in router.ask_mbuddy_stream(MBUDDYState(), text, [], {}, "en")])
    return asyncio.run(go())


def test_a_failed_tool_is_reported_in_code_not_by_the_model(monkeypatch):
    fail = ToolFailure("I couldn't understand the time 'someday', so no reminder was set.")
    out = reply_to("remind me about MATE90RELASE someday", monkeypatch, [("add_reminder", {}, fail)])
    assert out == "[NORMAL] I couldn't understand the time 'someday', so no reminder was set."


def test_an_unknown_tool_is_an_honest_cant(monkeypatch):
    out = reply_to("play Riptide", monkeypatch, [("play_music", {}, ToolFailure("There is no tool called play_music."))])
    assert out == "[NORMAL] I can't do that yet."
