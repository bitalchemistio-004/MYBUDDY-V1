"""The moment block: what is in front, the day, the schedule. Pure rendering, so no Windows is needed."""
from datetime import datetime

from brain import awareness


NOW = datetime(2026, 10, 13, 13, 0)


def snap(**kw):
    base = {"front": {"app": "chrome", "title": "YouTube - Lofi", "private": False},
            "activity": {"active_s": 9600, "apps": [("Google Chrome", 4800), ("Visual Studio Code", 3240), ("Word", 20)]},
            "schedule": {"next": {"title": "Design review", "at": "2026-10-13T14:00"}, "tasks": 2, "timers": [{"name": "1 minute", "left_s": 40}], "focus": None},
            "media": {"title": "Yellow", "artist": "Coldplay", "app": "Chrome", "playing": False},
            "device": {"battery": 18, "charging": False, "strain": False}, "messages": 0}
    base.update(kw)
    return base


def test_the_block_says_what_is_true_and_only_that():
    text = awareness.render(snap(), NOW)
    assert 'In front: chrome, "YouTube - Lofi"' in text
    assert "Google Chrome 1h20" in text and "Visual Studio Code 54 min" in text and "Word" not in text   # under a minute is left out
    assert "next: Design review in 1h00" in text and "2 open tasks" in text and "under a minute left" in text
    assert '"Yellow" by Coldplay in Chrome, paused' in text
    assert "battery 18% (not charging)" in text
    assert len(text) <= awareness.MAX_CHARS


def test_a_private_window_is_named_by_its_app_only():
    text = awareness.render(snap(front={"app": "keepass", "title": "", "private": True}), NOW)
    assert "keepass (contents not read)" in text and "YouTube" not in text


def test_nothing_known_is_no_block():
    assert awareness.render(None) == ""
    assert awareness.render({"front": None, "activity": None, "schedule": {"next": None, "tasks": 0, "timers": [], "focus": None},
                             "media": None, "device": {"battery": 90, "charging": True}, "messages": 0}, NOW) == ""


def test_switched_off_or_stale_gives_nothing(monkeypatch):
    monkeypatch.setattr(awareness, "start", lambda: None)
    monkeypatch.setattr(awareness, "_snapshot", snap())
    monkeypatch.setattr(awareness, "_taken", __import__("time").time())
    monkeypatch.setattr(awareness, "_enabled", lambda: True)
    assert "In front" in awareness.block()
    monkeypatch.setattr(awareness, "_taken", __import__("time").time() - awareness.STALE_S - 5)
    assert awareness.block() == ""                       # an old reading is worse than none
    monkeypatch.setattr(awareness, "_taken", __import__("time").time())
    monkeypatch.setattr(awareness, "_enabled", lambda: False)
    assert awareness.block() == ""


def test_the_setting_exists_and_defaults_on():
    from core import ui_settings
    assert ui_settings.DEFAULTS["awareness"] is True


def test_what_the_user_switched_off_is_said_as_that(monkeypatch):
    from brain import agent
    monkeypatch.setattr(agent, "tool_switched_off", lambda n: n in ("create_file", "read_file", "list_folder", "open_file_or_folder", "delete_file", "take_screenshot"))
    text = agent.capability_text()
    assert "SWITCHED OFF by him in Extensions: files" in text and "do not say you cannot do it" in text
    monkeypatch.setattr(agent, "tool_switched_off", lambda n: False)
    assert "SWITCHED OFF" not in agent.capability_text()
