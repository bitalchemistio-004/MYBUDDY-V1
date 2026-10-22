"""Reading the PC is free; acting waits for the user's yes (S48, S49)."""
import subprocess
import sys
import time

import pytest

from brain import pc_windows as pw
from brain.tools import ToolFailure


@pytest.fixture(autouse=True)
def clean():
    pw._pending = None
    yield
    pw._pending = None


def fake_window(monkeypatch, app="Spotify", title="Spotify Premium"):
    w = pw.Window(hwnd=4242, title=title, app=app, focused=False, minimized=False)
    monkeypatch.setattr(pw, "find_window", lambda target: w if target.lower() in (app.lower(), title.lower()) else None)
    done = []
    monkeypatch.setattr(pw, "_execute", lambda p: done.append(p.action) or f"Closed {p.app}.")
    monkeypatch.setattr("brain.action_log.record", lambda *a, **k: None)
    return done


def test_a_request_does_nothing_until_yes(monkeypatch):
    done = fake_window(monkeypatch)
    out = pw.request("close", "Spotify")
    assert "NOT DONE YET" in out and "close Spotify" in out
    assert done == [] and pw.pending() is not None
    name, args, result = pw.resolve("yes please")
    assert done == ["close"] and result == "Closed Spotify." and pw.pending() is None


def test_no_cancels_and_other_talk_keeps_waiting(monkeypatch):
    done = fake_window(monkeypatch)
    pw.request("close", "Spotify")
    assert "Cancelled" in pw.resolve("no, leave it")[2]
    pw.request("close", "Spotify")
    # Other talk (someone else in the room) is not an answer: nothing done, still waiting.
    assert pw.resolve("wait, what's the weather?") is None and pw.pending() is not None
    assert "Cancelled" in pw.resolve("no")[2]
    assert done == []


def test_it_expires(monkeypatch):
    done = fake_window(monkeypatch)
    pw.request("minimize", "Spotify")
    pw._pending.expires = time.time() - 1
    assert pw.pending() is None and pw.resolve("yes") is None and done == []


def test_unknown_window_or_action_is_an_honest_failure(monkeypatch):
    fake_window(monkeypatch)
    assert isinstance(pw.request("close", "Photoshop"), ToolFailure)
    assert isinstance(pw.request("delete", "Spotify"), ToolFailure)
    assert pw.pending() is None


def test_the_app_button_answers_too(monkeypatch):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from api.ui_api import origin_guard, register_ui_routes

    done = fake_window(monkeypatch)
    pw.request("close", "Spotify")

    async def go():
        app = web.Application(middlewares=[origin_guard])
        register_ui_routes(app)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/ui/pending-action")
            assert "close Spotify" in (await r.json())["pending"]["text"]
            r = await c.post("/ui/pending-action", json={"decision": "yes"}, headers={"Origin": "http://tauri.localhost"})
            assert (await r.json())["result"] == "Closed Spotify."
            r = await c.post("/ui/pending-action", json={"decision": "yes"}, headers={"Origin": "http://tauri.localhost"})
            assert r.status == 404  # nothing left to confirm
    asyncio.run(go())
    assert done == ["close"]


def test_the_agent_offers_the_tools_and_gates_them_by_the_setting(tmp_path, monkeypatch):
    from brain import agent
    from core import ui_settings
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    ui_settings.reset_cache()
    names = {t["function"]["name"] for t in agent.allowed_tools()}
    assert {"pc_status", "window_action"} <= names
    ui_settings.update({"tool_permissions": {"windows": "off"}})
    names = {t["function"]["name"] for t in agent.allowed_tools()}
    assert not ({"pc_status", "window_action"} & names)
    ui_settings.reset_cache()


def test_real_window_minimized_then_closed_only_after_yes():
    """A throwaway window of our own, acted on for real and checked at Windows."""
    title = f"MBUDDY test window {time.time():.0f}"
    proc = subprocess.Popen([sys.executable, "-c",
                             f"import tkinter as t; r=t.Tk(); r.title({title!r}); r.geometry('200x80'); r.mainloop()"])
    try:
        for _ in range(50):
            w = pw.find_window(title)
            if w:
                break
            time.sleep(0.1)
        assert w is not None, "test window did not appear"
        assert title in pw.pc_status_text()

        assert "NOT DONE YET" in pw.request("minimize", title)
        assert not pw.user32.IsIconic(w.hwnd)  # nothing happened yet
        assert pw.resolve("yes")[2] == f"Minimized {w.app}."
        assert pw.user32.IsIconic(w.hwnd)

        pw.request("close", title)
        assert "Cancelled" in pw.resolve("no")[2] and pw.user32.IsWindow(w.hwnd)
        pw.request("close", title)
        assert pw.resolve("yes")[2].startswith("Closed")
        assert proc.wait(timeout=5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_direct_commands_are_understood_without_the_model():
    dc = pw.direct_command
    assert dc("Close Spotify.") == ("close", "Spotify")
    assert dc("Please close the scratch notes window.") == ("close", "scratch notes")
    assert dc("Hey Buddy, minimize Chrome") == ("minimize", "Chrome")
    assert dc("hide the WhatsApp app please") == ("minimize", "WhatsApp")
    assert dc("Bring up my Word") == ("focus", "Word")
    assert dc("switch to Cursor") == ("focus", "Cursor")
    # Not commands: left to the model.
    assert dc("I closed the deal today!") is None
    assert dc("Chrome is distracting me.") is None
    assert dc("close enough") == ("close", "enough")  # parsed; the router ignores it because no such window is open


def test_questions_about_the_pc_now_are_recognised():
    q = pw.is_pc_question
    for t in ["Is anything playing sound?", "What apps are running right now, and is anything playing audio?",
              "Which apps are open?", "What's making that noise?", "How's my battery?"]:
        assert q(t), t
    for t in ["I am playing football later.", "The store is open until 9.", "I was running this morning.", "Tell me a joke."]:
        assert not q(t), t


def test_pause_asks_first_presses_once_and_checks_it_stopped(monkeypatch):
    player = {"chrome": True}  # chrome is playing
    presses = []

    class FakeUser32:
        def keybd_event(self, vk, scan, flags, extra):
            if flags == 0:
                presses.append(vk)
                player["chrome"] = not player["chrome"]  # the media key toggles

    monkeypatch.setattr(pw, "user32", FakeUser32())
    monkeypatch.setattr(pw, "playing_audio", lambda: [(a, False) for a, on in player.items() if on])
    monkeypatch.setattr(pw, "list_windows", lambda include_own=False: [pw.Window(1, "Just A Dream - YouTube Music", "chrome", False, False)])
    monkeypatch.setattr(pw.time, "sleep", lambda s: None)
    monkeypatch.setattr("brain.action_log.record", lambda *a, **k: None)
    monkeypatch.setattr(pw, "_last_paused", None)

    assert "NOT DONE YET" in pw.request("pause", "")
    assert "pause what's playing in chrome" in pw.pending().describe()
    assert presses == [] and player["chrome"]  # asked, nothing done
    assert pw.resolve("yes")[2] == "Paused what was playing in chrome."
    assert presses == [pw.VK_MEDIA_PLAY_PAUSE] and not player["chrome"]

    # Pausing again when nothing plays is refused (the key would START it).
    assert isinstance(pw.request("pause", ""), ToolFailure)
    # Resume what MBUDDY paused, again only after a yes.
    assert "NOT DONE YET" in pw.request("resume", "")
    assert pw.resolve("yes")[2] == "Resumed chrome; it's playing again."
    assert player["chrome"] and len(presses) == 2


def test_media_commands_are_understood():
    dc = pw.direct_command
    assert dc("Pause it.") == ("pause", "") and dc("Can you pause that?") == ("pause", "")
    assert dc("stop the music") == ("pause", "") and dc("resume it") == ("resume", "")
    assert dc("stop worrying about it") is None


def test_skip_asks_first_and_checks_the_song_really_changed(monkeypatch):
    songs = ["Golden", "Riptide"]
    state = {"i": 0}
    presses = []

    class FakeUser32:
        def keybd_event(self, vk, scan, flags, extra):
            if flags == 0:
                presses.append(vk)
                state["i"] += 1

    monkeypatch.setattr(pw, "user32", FakeUser32())
    monkeypatch.setattr(pw, "now_playing", lambda: [{"app": "Chrome", "title": songs[min(state["i"], 1)], "artist": "Kaya", "status": "playing"}])
    monkeypatch.setattr(pw.time, "sleep", lambda s: None)
    monkeypatch.setattr("brain.action_log.record", lambda *a, **k: None)

    assert "NOT DONE YET" in pw.request("next", "")
    assert pw.pending().describe() == 'skip to the next song in Chrome (now: "Golden")'
    assert presses == []  # asked only
    assert pw.resolve("yes")[2] == 'Skipped. Now playing "Riptide" by Kaya in Chrome.'
    assert presses == [pw.VK_MEDIA_NEXT_TRACK]


def test_skip_that_doesnt_change_the_song_is_reported(monkeypatch):
    class FakeUser32:
        def keybd_event(self, *a):
            pass

    monkeypatch.setattr(pw, "user32", FakeUser32())
    monkeypatch.setattr(pw, "now_playing", lambda: [{"app": "Chrome", "title": "Golden", "artist": "", "status": "playing"}])
    monkeypatch.setattr(pw.time, "sleep", lambda s: None)
    monkeypatch.setattr("brain.action_log.record", lambda *a, **k: None)
    pw.request("next", "")
    assert isinstance(pw.resolve("yes")[2], ToolFailure)


def test_no_player_no_skip(monkeypatch):
    monkeypatch.setattr(pw, "now_playing", lambda: [])
    assert isinstance(pw.request("next", ""), ToolFailure) and pw.pending() is None


def test_skip_commands_are_understood():
    dc = pw.direct_command
    for t in ["skip this song", "next song", "change the song im listening to", "can you change the song"]:
        assert dc(t) == ("next", ""), t
    assert dc("play the previous song") == ("previous", "")
    assert dc("I want to skip lunch") is None and dc("next week") is None


def test_resume_finds_what_was_paused_even_after_a_restart(monkeypatch, tmp_path):
    # live 2026-10-11: paused, the backend restarted, "play the music" -> "I haven't paused anything"
    import json
    saved = tmp_path / "last_paused.json"
    monkeypatch.setattr(pw, "_last_paused", None)
    monkeypatch.setattr(pw, "_paused_path", lambda: saved)
    monkeypatch.setattr(pw, "playing_audio", lambda: [])
    monkeypatch.setattr(pw, "list_windows", lambda include_own=False: [])
    monkeypatch.setattr(pw, "now_playing", lambda: [])
    # nothing remembered, nothing paused in any player: it says so plainly
    assert isinstance(pw.request("resume", ""), ToolFailure)
    # written down before the restart: found again
    saved.write_text(json.dumps({"app": "chrome", "title": "YouTube Music", "at": __import__("time").time()}), encoding="utf-8")
    assert "NOT DONE YET" in pw.request("resume", "") and pw.pending().app == "chrome"
    pw.cancel() if hasattr(pw, "cancel") else None


def test_resume_uses_a_player_that_shows_paused(monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "_last_paused", None)
    monkeypatch.setattr(pw, "_paused_path", lambda: tmp_path / "none.json")
    monkeypatch.setattr(pw, "playing_audio", lambda: [])
    monkeypatch.setattr(pw, "list_windows", lambda include_own=False: [])
    monkeypatch.setattr(pw, "now_playing", lambda: [{"app": "Chrome", "title": "Yellow", "artist": "Coldplay", "status": "paused"}])
    assert "NOT DONE YET" in pw.request("resume", "") and pw.pending().app == "Chrome"


def test_a_timed_pause_asks_once_and_schedules_the_resume(monkeypatch):
    started = []

    class FakeTimer:
        def __init__(self, interval, fn, args=()):
            started.append((interval, fn, args))
            self.daemon = False

        def start(self):
            pass

    player = {"chrome": True}

    class FakeUser32:
        def keybd_event(self, vk, scan, flags, extra):
            if flags == 0:
                player["chrome"] = not player["chrome"]

    monkeypatch.setattr(pw.threading, "Timer", FakeTimer)
    monkeypatch.setattr(pw, "user32", FakeUser32())
    monkeypatch.setattr(pw, "playing_audio", lambda: [(a, False) for a, on in player.items() if on])
    monkeypatch.setattr(pw, "list_windows", lambda include_own=False: [pw.Window(1, "Yellow - YouTube Music", "chrome", False, False)])
    monkeypatch.setattr(pw.time, "sleep", lambda s: None)
    monkeypatch.setattr("brain.action_log.record", lambda *a, **k: None)
    monkeypatch.setattr(pw, "_last_paused", None)
    assert "NOT DONE YET" in pw.request("pause", "", 1)
    assert "play it again in 1 minute" in pw.pending().describe()
    assert started == []                                   # nothing is scheduled before the yes
    assert pw.resolve("yes")[2] == "Paused what was playing in chrome. It will play again in 1 minute."
    assert len(started) == 1 and started[0][0] == 60 and started[0][1] is pw._auto_resume
    pw._auto_resume(*started[0][2])                        # the minute is over
    assert player["chrome"]
