import pytest

from brain.tools import ToolFailure
from core import ledger, reflex


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MBUDDY_LEDGER_PATH", str(tmp_path / "ledger.json"))
    ledger.reset()


def test_a_finished_one_line_result_is_the_reply_and_nothing_else_is():
    assert reflex.reply_for("clock", "It's 10:56 AM on Tuesday.") == "It's 10:56 AM on Tuesday."
    assert reflex.reply_for("calculator", "12*3 = 36") == "12*3 = 36"
    assert reflex.reply_for("clock", "TOOL OUTPUT (clock):\nIt's 10:56 AM.") == "It's 10:56 AM."
    assert reflex.reply_for("clock", ToolFailure("FAILED: no clock")) is None
    assert reflex.reply_for("clock", "line one\nline two") is None          # a judgment call for the model
    assert reflex.reply_for("clock", "x" * 400) is None
    assert reflex.reply_for("web_search", "Live search results") is None     # not a reflex tool
    assert reflex.reply_for("task", "FAILED: couldn't understand the time") is None
    assert reflex.reply_for("timer", "I couldn't understand the timer duration. Try something like 'set a timer for 5 minutes'.") is None
    assert reflex.reply_for("timer", "Timer started: 60s timer for 1 minute") == "Timer started: 60s timer for 1 minute"


def test_instant_replies_can_be_switched_off(monkeypatch):
    from core import ui_settings
    monkeypatch.setattr(ui_settings, "get", lambda key, *a: False if key == "instant_replies" else True)
    assert reflex.reply_for("clock", "It's 10:56 AM.") is None


def test_the_model_is_told_what_was_handled_without_it_until_it_has_run():
    assert ledger.block() == ""
    ledger.record("what time is it", "clock: It's 10:56 AM.", now=1000.0)
    ledger.record("set a timer for 10 minutes", "timer: Timer set for 10 minutes.", now=1060.0)
    text = ledger.block(now=1120.0)
    assert "HANDLED WITHOUT YOU" in text and "what time is it" in text and "Timer set for 10 minutes" in text
    assert "1 min ago" in text and "2 min ago" in text
    ledger.mark_model_turn(now=1130.0)
    assert ledger.block(now=1140.0) == ""                                    # told once
    ledger.record("lower the volume", "sys_volume: Volume set to 30%.", now=1150.0)
    assert "Volume set to 30%" in ledger.block(now=1160.0) and "timer" not in ledger.block(now=1160.0).lower()


def test_the_ledger_is_short_old_entries_fade_and_a_restart_keeps_it(tmp_path):
    for i in range(10):
        ledger.record(f"q{i}", f"done {i}", now=2000.0 + i)
    assert len(ledger.since_model(now=2020.0)) == ledger.MAX_SHOWN
    assert ledger.since_model(now=2000.0 + ledger.WINDOW_S + 100) == []      # too old to mention
    ledger._loaded = False
    ledger._entries = []                                                      # as after a restart
    assert ledger.since_model(now=2020.0)[-1]["said"] == "q9"


# ── through the router: no model call for a reflex, and the model hears about it later ──
import asyncio

import brain.agent as agent
import brain.llm_router as router
from core.state_manager import MBUDDYState


def _turn(text, monkeypatch, model_calls, tool_result="It's 10:56 AM on Tuesday."):  # noqa: D103
    async def fake_tool(name, query, **kw):
        return tool_result

    async def fake_agent(user_text, history, memory, question_only=False):
        return []

    async def model(*a, **k):
        model_calls.append(k.get("messages") or a)
        yield "[NORMAL] Sure."

    monkeypatch.setattr(router, "execute_tool_async", fake_tool)
    monkeypatch.setattr(agent, "run_agent", fake_agent)
    monkeypatch.setattr(router, "providers_include_ollama", lambda: True)
    monkeypatch.setattr(router, "get_brain_adapter", lambda *a, **k: type("A", (), {"stream_chat": model, "is_ready": lambda s: True})())

    from brain import cognitive_state
    state = MBUDDYState()
    decision = cognitive_state.decide_behavior(cognitive_state.build_cognitive_state(state, memory={}), event="thinking", text=text)

    async def go():
        return "".join([c async for c in router.ask_mbuddy_stream(state, text, [], {}, "en", behavior_decision=decision)])
    return asyncio.run(go())


def test_a_simple_command_is_answered_without_the_model_and_the_model_is_told_next_time(monkeypatch):
    calls = []
    out = _turn("what time is it?", monkeypatch, calls)
    assert out == "[NORMAL] It's 10:56 AM on Tuesday." and calls == []
    assert "what time is it" in ledger.block()

    seen = []
    from brain import memory_context
    real = memory_context.build_turn_block
    monkeypatch.setattr(memory_context, "build_turn_block", lambda state: real(state))
    out2 = _turn("tell me a joke", monkeypatch, seen)
    assert seen, "an ordinary question still goes to the model"
    flat = repr(seen)
    assert "HANDLED WITHOUT YOU" in flat and "10:56" in flat
    assert ledger.block() == ""                                              # told once


def test_switched_off_the_model_words_the_reply_as_before(monkeypatch):
    from core import ui_settings
    real = ui_settings.get
    monkeypatch.setattr(ui_settings, "get", lambda key, *a: False if key == "instant_replies" else real(key, *a))
    calls = []
    _turn("what time is it?", monkeypatch, calls)
    assert calls and ledger.block() == ""
