"""Shared test setup.

Temporary files go in the user's own temp folder, whatever TEMP says. After a
reboot TEMP was C:\\Windows\\Temp, so tmp_path landed under Windows, where
MBUDDY rightly refuses to delete anything, and six pc_actions tests failed.
The refusal stays; the tests just don't put their files there.
"""
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _nothing_is_opened_for_real(request, monkeypatch):
    """No test may open an app, file or page on the user's PC (U1: every
    user-facing launch goes through core.launch). test_launch_job checks the
    job behaviour with its own harmless processes."""
    if request.module.__name__.endswith("test_launch_job"):
        return
    from core import launch

    def refuse(*a, **k):
        raise AssertionError("a test tried to open something on the PC for real")
    monkeypatch.setattr(launch, "_spawn", refuse)


@pytest.fixture(autouse=True)
def _never_start_the_real_ollama(request, monkeypatch):
    """A reply now checks the local brain is up and may start Ollama (M2 S4).
    No test may probe, start or warm the real one; test_ollama_service tests
    that logic itself with its own fakes."""
    if request.module.__name__.endswith("test_ollama_service"):
        return
    from brain import ollama_service
    monkeypatch.setattr(ollama_service, "probe", lambda timeout=1.0: "up")
    monkeypatch.setattr(ollama_service, "warm", lambda should_stop=None: None)
    monkeypatch.setattr(ollama_service, "load_model", lambda: "model load 0.0s")
    monkeypatch.setattr(ollama_service, "warm_prompts", lambda should_stop=None: [])
    monkeypatch.setattr(ollama_service, "ensure_running", lambda wait_s=10.0: (True, ""))
    monkeypatch.setattr(ollama_service, "warm_if_needed", lambda: None)
    monkeypatch.setattr(ollama_service, "model_loaded", lambda: True)
    monkeypatch.setattr(ollama_service, "finish_start_in_background", lambda: None)


def pytest_configure(config):
    if config.option.basetemp:  # an explicit --basetemp wins
        return
    local = os.environ.get("LOCALAPPDATA")
    if local:
        config.option.basetemp = str(Path(local) / "Temp" / "mbuddy-pytest")


@pytest.fixture(autouse=True)
def _no_real_learning_in_tests(request, monkeypatch, tmp_path):
    """Outcomes write lessons into the user's real vault through the local model in
    the background (brain/outcomes.py). Tests never do that: their outcomes go to a
    temp file and the background steps do not run, unless a test passes its own."""
    monkeypatch.setenv("MBUDDY_OUTCOMES_PATH", str(tmp_path / "outcomes.jsonl"))
    from brain import outcomes
    monkeypatch.setattr(outcomes, "_bg", lambda fn, *a: None)
    monkeypatch.setattr(outcomes, "_last", None)


@pytest.fixture(autouse=True)
def _memory_lives_in_the_test_folder(monkeypatch, tmp_path, request):
    """The user's memory (memory/mbuddy.sqlite3) is never written by a test. A suite run on 2026-10-20 saved a
    test session's empty memory over the real snapshot (facts, emotional memories, the recent conversation)."""
    from memory import sqlite_store
    store = sqlite_store.SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    trace = os.environ.get("MBUDDY_TRACE_MEMORY_WRITES")
    if trace:
        save = store.save

        def traced(memory):
            with open(trace, "a", encoding="utf-8") as f:
                f.write(request.node.nodeid + "\n")
            return save(memory)
        store.save = traced
    monkeypatch.setattr(sqlite_store, "_store", store)


@pytest.fixture(autouse=True)
def _tasks_timers_and_ledger_live_in_the_test_folder(monkeypatch, tmp_path):
    """The user's tasks and reminders (~/.buddy/database/buddy.db), timers (memory/timers.json) and the action
    ledger (~/.buddy/ledger.json) are never written by a test ('Zzqxv call the plumber' rows piled up in the
    real task list)."""
    import brain.task_tracker as tt
    from brain import tools
    monkeypatch.setattr(tt, "task_tracker", tt.TaskTracker(db_path=tmp_path / "tasks.db"))
    monkeypatch.setattr(tools, "_timer_manager", tools.TimerManager(db_path=tmp_path / "timers.json"))
    monkeypatch.setenv("MBUDDY_LEDGER_PATH", str(tmp_path / "ledger.json"))


@pytest.fixture(autouse=True)
def _the_real_vault_is_read_only(monkeypatch, request):
    """Tests may read the user's Obsidian vault, never write it. Every suite run used to add uses, successes and
    failures to real lesson notes (2026-10-20: 'uses: 58' on a Blender note), skewing what MBUDDY learned."""
    from brain import obsidian_vault
    real = Path.home() / "Documents" / "Obsidian Vault"
    write = obsidian_vault.Vault._atomic_write

    def guarded(self, path, text, expect_mtime=None):
        if Path(path).resolve().is_relative_to(real.resolve()):
            raise AssertionError(f"a test tried to write the user's vault: {Path(path).name}")
        return write(self, path, text, expect_mtime)
    monkeypatch.setattr(obsidian_vault.Vault, "_atomic_write", guarded)


@pytest.fixture(autouse=True)
def _secrets_live_in_the_test_folder(monkeypatch, tmp_path):
    """The user's saved cloud key (core/secret_store.py) must never be read, or overwritten, by a test."""
    monkeypatch.setenv("MBUDDY_SECRETS_DIR", str(tmp_path / "secrets"))
