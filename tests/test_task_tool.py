"""Task tool intent: adding must not be mistaken for listing."""
import pytest

import brain.task_tracker as tt
from brain.tools import execute_task_tool


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    t = tt.TaskTracker(db_path=tmp_path / "buddy.db")
    monkeypatch.setattr(tt, "task_tracker", t)
    return t


@pytest.mark.parametrize("phrase, title", [
    ("Add buy milk to my task list.", "buy milk"),
    ("add milk and eggs to my to-do list", "milk and eggs"),
    ("Please add call the dentist to my list", "call the dentist"),
    ("Put water the plants on my todo list", "water the plants"),
])
def test_add_phrases_create_a_task(tracker, phrase, title):
    reply = execute_task_tool(phrase)
    tasks = tracker.get_pending_tasks()
    assert len(tasks) == 1, reply
    assert tasks[0].title.lower() == title
    assert reply.startswith("Added task")


@pytest.mark.parametrize("phrase", ["What's on my task list?", "show my tasks", "list my to-do"])
def test_list_phrases_list_without_adding(tracker, phrase):
    tracker.create_task("existing thing")
    execute_task_tool(phrase)
    assert [t.title for t in tracker.get_pending_tasks()] == ["existing thing"]
