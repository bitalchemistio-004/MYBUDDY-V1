"""Hybrid router, agent step: the model chooses tools, MBUDDY runs them for real."""
import asyncio

import pytest

import brain.agent as agent
import brain.task_tracker as tt
from brain.tools import ToolFailure


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    t = tt.TaskTracker(db_path=tmp_path / "buddy.db")
    monkeypatch.setattr(tt, "task_tracker", t)
    return t


def test_identical_calls_run_once(monkeypatch):
    runs = []
    monkeypatch.setitem(agent.EXECUTORS, "adjust_volume", lambda args, memory: runs.append(args) or "System volume set to 20%.")
    calls = [{"name": "adjust_volume", "args": {"action": "down", "amount": 40}}] * 2
    results = agent.execute_calls(calls, memory={})
    assert len(runs) == 1 and len(results) == 1


def test_unknown_tool_is_an_honest_failure():
    [(name, args, result)] = agent.execute_calls([{"name": "play_music", "args": {}}], memory={})
    assert isinstance(result, ToolFailure)


def test_reminder_with_unreadable_time_fails_honestly(tracker):
    [(_, _, result)] = agent.execute_calls(
        [{"name": "add_reminder", "args": {"text": "drink water", "when": "every 30 minutes"}}], memory={})
    assert isinstance(result, ToolFailure)
    assert tracker.get_pending_tasks() == []


def test_reminder_is_really_created(tracker):
    [(_, _, result)] = agent.execute_calls(
        [{"name": "add_reminder", "args": {"text": "Call the bank", "when": "in 20 minutes"}}], memory={})
    assert not isinstance(result, ToolFailure)
    [task] = tracker.get_pending_tasks()
    assert task.title == "Call the bank" and task.due_date


def test_remember_saves_a_real_fact():
    memory = {}
    [(_, _, result)] = agent.execute_calls([{"name": "remember", "args": {"fact": "Sam is allergic to peanuts"}}], memory=memory)
    assert "Sam is allergic to peanuts" in memory["core_facts"]
    assert not isinstance(result, ToolFailure)


def test_tool_block_marks_failures():
    block = agent.format_tool_output([
        ("adjust_volume", {}, "System volume set to 20% (was 60%)."),
        ("add_reminder", {}, ToolFailure("I couldn't understand the time 'every 30 minutes'.")),
    ])
    assert "TOOL OUTPUT (adjust_volume)" in block and "20%" in block
    assert "TOOL FAILED (add_reminder)" in block


def test_run_agent_executes_what_the_model_chose(monkeypatch):
    # Two steps (S96): the area first, then the tool from that area.
    async def fake_model(messages, tools=None, num_predict=120):
        if "time" in {t["function"]["name"] for t in tools or []}:
            return [{"name": "time", "args": {}}]
        return [{"name": "get_time", "args": {}}]

    monkeypatch.setattr(agent, "_ask_model", fake_model)
    monkeypatch.setitem(agent.EXECUTORS, "get_time", lambda args, memory: "It's 14:10.")
    results = asyncio.run(agent.run_agent("Am I late for my 3 o'clock?", history=[], memory={}))
    assert results == [("get_time", {}, "It's 14:10.")]


def test_run_agent_with_no_tool_does_nothing(monkeypatch):
    async def fake_model(messages, tools=None, num_predict=120):
        return []

    monkeypatch.setattr(agent, "_ask_model", fake_model)
    assert asyncio.run(agent.run_agent("I'm bored.", history=[], memory={})) == []
