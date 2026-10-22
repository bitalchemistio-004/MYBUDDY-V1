"""Senior review, round 2 (docs/audit/SENIOR_REVIEW.md, C3, M5, M6, M7).

EXPECTED TO FAIL until the findings are fixed. Nothing touches the PC: every
launcher, window call and clipboard call is a recorder; files live in tmp_path.
"""
import asyncio
import os
import time
import webbrowser

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from brain import pc_actions as pa
from brain import pc_windows as pw
from core import setup_checks, ui_settings


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    ui_settings.reset_cache()
    monkeypatch.setattr(setup_checks, "ollama_models", lambda: ["qwen3:4b-instruct"])
    pa._pending = None
    pw._pending = None
    monkeypatch.setattr(pa, "BASE_DIR", tmp_path / "MBUDDY")
    monkeypatch.setattr(pa, "_windows", lambda: [])
    monkeypatch.setattr(pa, "_wait_for_window", lambda before, words, timeout=5.0: None)
    monkeypatch.setattr(pa, "_publish", lambda: None)
    monkeypatch.setattr(pw, "_publish", lambda: None)

    def no_real_actions(*a, **k):
        raise AssertionError("a real PC action was attempted in a test")
    monkeypatch.setattr(os, "startfile", no_real_actions, raising=False)
    monkeypatch.setattr(pa, "_open", no_real_actions)
    monkeypatch.setattr(pa.subprocess, "Popen", no_real_actions)
    monkeypatch.setattr(pa.subprocess, "run", no_real_actions)
    monkeypatch.setattr(webbrowser, "open", no_real_actions)
    monkeypatch.setattr(pw, "_execute", lambda p: no_real_actions())
    monkeypatch.setattr("brain.action_log.record", lambda *a, **k: None)
    yield
    pa._pending = None
    pw._pending = None
    ui_settings.reset_cache()


# C3 ----------------------------------------------------------------------
def test_a_yes_settles_the_question_that_was_just_asked(monkeypatch):
    """An older window question (pc_windows) is still waiting when a newer
    pc_actions question is asked. The router voices the NEWER one
    (llm_router.py: `waiting = pc_actions.pending() or pc_windows.pending()`),
    but both the voice path and the app's Yes button settle pc_windows FIRST,
    so "yes" to "Lock the PC?" closes Spotify instead."""
    closed = []
    monkeypatch.setattr(pw, "_execute", lambda p: closed.append(p.app) or "Closed Spotify (fake)")
    locked = []
    monkeypatch.setattr(pa.REGISTRY["lock_screen"], "execute", lambda plan: locked.append(plan) or "locked (fake)")
    pw._pending = pw.Pending("close", 1234, "Spotify", "Spotify Premium", time.time() + 60)
    time.sleep(0.01)
    assert isinstance(pa.run("lock_screen", {}), pa.NeedsConfirmation)

    async def go():
        from api.ui_api import origin_guard, register_ui_routes
        app = web.Application(middlewares=[origin_guard])
        register_ui_routes(app)
        async with TestClient(TestServer(app)) as c:
            shown = (await (await c.get("/ui/pending-action")).json())["pending"]
            r = await c.post("/ui/pending-action", json={"decision": "yes"})
            return shown, await r.json()
    shown, body = asyncio.run(go())
    assert shown and "lock" in shown["text"].lower(), f"the app shows the older question: {shown}"
    assert locked and not closed, f"yes ran {'close Spotify' if closed else 'nothing'} instead of the lock: {body}"


# M5 ----------------------------------------------------------------------
def test_two_critical_questions_in_one_turn_do_not_silently_replace_each_other(tmp_path):
    """The model calls delete_file twice in one turn. Both results say
    "NOT DONE YET ... Ask exactly ...", but only the second question is
    stored: the first can never be answered, and a "yes" meant for the first
    deletes the second file."""
    from brain.agent import execute_calls
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    for f in (a, b):
        f.write_text("x", encoding="utf-8")
    results = execute_calls([{"name": "delete_file", "args": {"path": str(a)}},
                             {"name": "delete_file", "args": {"path": str(b)}}], None)
    asking = [r for _n, _args, r in results if str(r).startswith("NOT DONE YET")]
    p = pa.pending()
    assert len(asking) <= 1, (f"{len(asking)} questions were handed to the reply but only one is stored "
                              f"({p.text if p else None})")


# M6 ----------------------------------------------------------------------
@pytest.mark.parametrize("name", ["help.chm", "setup.application", "app.appinstaller", "pkg.msix",
                                  "x.settingcontent-ms", "tool.msc", "evil.xll", "disk.iso"])
def test_open_file_refuses_other_runnable_types(monkeypatch, tmp_path, name):
    """_RUNNABLE is a deny list. These types also run code, install software
    or mount images when opened, and still go straight to os.startfile."""
    target = tmp_path / name
    target.write_bytes(b"not really")
    started = []
    monkeypatch.setattr(os, "startfile", lambda p, *a: started.append(p), raising=False)
    result = pa.run("open_file_or_folder", {"path": str(target)})
    assert not started, f"{name} was opened with no confirmation: {result!r}"


# M7 ----------------------------------------------------------------------
def test_connector_actions_ask_before_changing_anything(monkeypatch):
    """Pinned rule: MBUDDY asks before taking any action on the PC. The
    connectors were written with a preview "so the user can be asked first",
    but merged into pc_actions their risk "low" runs at once: the clipboard
    is overwritten (and calendar_import_ics would add up to 200 reminders)
    with no question. The preview functions are never called."""
    from brain.connectors import clipboard

    class FakeClip:
        CF_UNICODETEXT, CF_HDROP, CF_DIB = 13, 15, 8
        data = "the user's copied password"

        def OpenClipboard(self): pass
        def CloseClipboard(self): pass
        def EmptyClipboard(self): FakeClip.data = None
        def SetClipboardText(self, text, fmt): FakeClip.data = text
        def IsClipboardFormatAvailable(self, fmt): return fmt == 13 and FakeClip.data is not None
        def GetClipboardData(self, fmt): return FakeClip.data
    monkeypatch.setattr(clipboard, "_win32", FakeClip())
    from brain.agent import execute_calls
    results = execute_calls([{"name": "clipboard_write", "args": {"text": "hello"}}], None)
    assert FakeClip.data == "the user's copied password", f"clipboard replaced with no yes: {results[0][2]!r}"


# C4 ----------------------------------------------------------------------
def test_the_esp32_can_still_reach_the_backend_by_lan_ip():
    """The M1 fix (host_allowed in origin_guard, applied to the whole app in
    main.py) answers 421 to every Host that isn't 127.0.0.1/localhost. The
    ESP32 connects to ws://<PC LAN IP>:5000/ws (firmware/mbuddy/src/MBUDDY.ino
    :132, docs/api/API.md), so its Host is e.g. 192.168.1.50:5000 and the
    device is locked out. A literal IP can't be a DNS-rebinding name, so
    IP-literal hosts should pass (they are still subject to _require_local)."""
    async def go():
        from api.ui_api import origin_guard

        async def ws_stub(request):
            return web.Response(text="reached")
        app = web.Application(middlewares=[origin_guard])
        app.router.add_get("/ws", ws_stub)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/ws", headers={"Host": "192.168.1.50:5000"})
            return r.status, await r.text()
    status, text = asyncio.run(go())
    assert status == 200 and text == "reached", f"the device's request was refused: {status} {text!r}"
