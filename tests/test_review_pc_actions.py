"""Senior review regression tests for brain/pc_actions.py
(docs/audit/SENIOR_REVIEW.md, C2, M2, M3, M4).

These are EXPECTED TO FAIL until the findings are fixed. Everything that could
touch the PC is replaced by a recorder; no real file, window or process is
used outside tmp_path.
"""
import asyncio
import os
import threading
import webbrowser

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from brain import pc_actions as pa


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    pa._pending = None
    monkeypatch.setattr(pa, "BASE_DIR", tmp_path / "MBUDDY")
    monkeypatch.setattr(pa, "_windows", lambda: [])
    monkeypatch.setattr(pa, "_wait_for_window", lambda before, words, timeout=5.0: None)
    monkeypatch.setattr(pa, "_publish", lambda: None)

    def no_real_actions(*a, **k):
        raise AssertionError("a real PC action was attempted in a test")
    monkeypatch.setattr(os, "startfile", no_real_actions, raising=False)
    monkeypatch.setattr(pa.subprocess, "Popen", no_real_actions)
    monkeypatch.setattr(pa.subprocess, "run", no_real_actions)
    monkeypatch.setattr(webbrowser, "open", no_real_actions)
    monkeypatch.setattr("brain.action_log.record", lambda *a, **k: None)
    yield
    pa._pending = None


# C2 ----------------------------------------------------------------------
@pytest.mark.parametrize("name", ["setup.exe", "run.bat", "script.ps1", "go.cmd", "x.vbs", "app.lnk"])
def test_open_file_never_launches_a_program_without_a_yes(monkeypatch, tmp_path, name):
    """open_file_or_folder is risk "low" and calls os.startfile on whatever
    path the model names, so a downloaded .exe/.bat runs with no question."""
    target = tmp_path / name
    target.write_bytes(b"not really a program")
    started = []
    monkeypatch.setattr(os, "startfile", lambda p, *a: started.append(p), raising=False)
    result = pa.run("open_file_or_folder", {"path": str(target)})
    assert not started, f"{name} was launched with no confirmation: {result!r}"
    assert isinstance(result, pa.NeedsConfirmation) or str(result).startswith("FAILED:")


# M2 ----------------------------------------------------------------------
def test_delete_question_says_which_file(tmp_path):
    """Two report.txt files: "Delete report.txt?" doesn't tell the user which
    one. The question must show the folder (or full path)."""
    a = tmp_path / "work" / "report.txt"
    b = tmp_path / "old" / "report.txt"
    for p in (a, b):
        p.parent.mkdir()
        p.write_text("x", encoding="utf-8")
    out = pa.run("delete_file", {"path": str(b)})
    assert isinstance(out, pa.NeedsConfirmation)
    q = out.pending.question
    assert str(b.parent) in q or "old" in q, f"the question doesn't identify the file: {q!r}"


# M3 ----------------------------------------------------------------------
def _ask_delete(tmp_path, name="a.txt"):
    f = tmp_path / name
    f.write_text("x", encoding="utf-8")
    out = pa.run("delete_file", {"path": str(f)})
    assert isinstance(out, pa.NeedsConfirmation)
    return f


def test_two_simultaneous_yes_answers_run_the_action_once(monkeypatch, tmp_path):
    """The app's Yes button and a spoken "yes" at the same moment: resolve()
    reads the pending action, then clears it later under the lock, so both
    callers can execute it."""
    _ask_delete(tmp_path)
    runs = []
    monkeypatch.setattr(pa.REGISTRY["delete_file"], "execute", lambda plan: runs.append(plan) or "done")
    gate = threading.Barrier(2, timeout=5)

    def slow_decision(text):
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return "yes"
    monkeypatch.setattr("brain.pc_windows.decision", slow_decision)
    threads = [threading.Thread(target=pa.resolve, args=("yes please",)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(runs) == 1, f"the confirmed action ran {len(runs)} times"


def test_answering_an_old_question_does_not_drop_a_newer_one(monkeypatch, tmp_path):
    """A new critical question arrives while the yes to the previous one is
    being processed: resolve() sets _pending = None unconditionally and wipes
    the new question without it ever being answered."""
    _ask_delete(tmp_path, "old.txt")
    monkeypatch.setattr(pa.REGISTRY["delete_file"], "execute", lambda plan: "done")
    newer = tmp_path / "new.txt"
    newer.write_text("x", encoding="utf-8")

    def decision_while_new_question_arrives(text):
        pa.run("delete_file", {"path": str(newer)})  # a newer question is stored now
        return "yes"
    monkeypatch.setattr("brain.pc_windows.decision", decision_while_new_question_arrives)
    settled = pa.resolve("yes please")
    assert settled is not None
    p = pa.pending()
    assert p is not None and "new.txt" in p.text, "the newer question vanished"


# M4 ----------------------------------------------------------------------
def test_the_apps_yes_button_settles_a_pc_actions_question(monkeypatch, tmp_path):
    """pc_actions publishes its question into runtime_status.pending_action,
    so the quick panel shows "Yes, do it", but POST /ui/pending-action only
    calls pc_windows.resolve(): the button answers 404 and nothing happens."""
    ran = []
    monkeypatch.setattr(pa.REGISTRY["lock_screen"], "execute", lambda plan: ran.append(plan) or "locked (fake)")
    from brain import pc_windows
    pc_windows._pending = None
    out = pa.run("lock_screen", {})
    assert isinstance(out, pa.NeedsConfirmation)

    async def go():
        from api.ui_api import origin_guard, register_ui_routes
        app = web.Application(middlewares=[origin_guard])
        register_ui_routes(app)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/ui/pending-action")
            shown = (await r.json())["pending"]
            r = await c.post("/ui/pending-action", json={"decision": "yes"})
            return shown, r.status, await r.json()
    shown, status, body = asyncio.run(go())
    assert shown is not None, "GET /ui/pending-action doesn't show the pc_actions question"
    assert status == 200 and body["ok"] and ran, f"Yes button didn't settle it: {status} {body}"
