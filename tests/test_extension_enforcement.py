"""S93: the Extensions switches are enforced in the brain, not only shown.
Settings live in a tmp file; the app index and shortcut targets are fakes;
nothing is opened, closed or killed."""
import os

import pytest

from brain import agent, pc_actions as pa
from brain import pc_windows
from core import setup_checks, ui_settings as s


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    s.reset_cache()
    monkeypatch.setattr(setup_checks, "ollama_models", lambda: ["qwen3:4b-instruct"])
    # One program, two names: a Start-menu shortcut and the App Paths exe.
    index = {"google chrome": r"C:\ProgramData\Start Menu\Google Chrome.lnk",
             "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             "notepad": r"C:\Windows\notepad.exe"}
    monkeypatch.setattr(pa, "app_index", lambda: dict(index))
    monkeypatch.setattr(pa, "_identity", lambda t: "chrome" if "Chrome" in t else os.path.splitext(os.path.basename(t))[0].lower())
    monkeypatch.setattr(os, "startfile", lambda *a: pytest.fail("nothing may be opened"), raising=False)
    monkeypatch.setattr(pa, "_open", lambda *a, **k: pytest.fail("nothing may be opened"))
    monkeypatch.setattr(pa, "_publish", lambda: None)
    monkeypatch.setattr(pc_windows, "_publish", lambda: None)
    pa._pending = pc_windows._pending = None
    yield
    pa._pending = pc_windows._pending = None
    s.reset_cache()


def test_a_switched_off_group_leaves_its_area_and_is_refused_at_the_gate():
    s.update({"extensions": {"settings": False}})
    assert agent.tool_switched_off("open_settings")
    assert "open_settings" not in {t["function"]["name"] for t in agent.area_tools("settings")}
    assert str(pa.run("open_settings", {"page": "sound"})).startswith("FAILED:")


def test_system_switch_also_covers_power():
    s.update({"extensions": {"system": False}})
    assert agent.tool_switched_off("power") and agent.tool_switched_off("lock_screen")


def test_a_question_waiting_when_its_group_is_switched_off_does_nothing(monkeypatch):
    ran = []
    monkeypatch.setattr(pa.REGISTRY["lock_screen"], "execute", lambda plan: ran.append(plan) or "locked")
    assert isinstance(pa.run("lock_screen", {}), pa.NeedsConfirmation)
    s.update({"extensions": {"system": False}})
    settled = pa.resolve("yes")
    assert not ran and str(settled[2]).startswith("FAILED:")


def test_a_switched_off_app_is_blocked_under_any_of_its_names():
    s.update({"app_rules": {"Google Chrome": False}})
    assert pa.blocked_app("chrome") == "Google Chrome"          # the other name, same program
    assert pa.blocked_app("Google Chrome") == "Google Chrome"
    assert pa.blocked_app("notepad") is None


def test_open_app_refuses_a_switched_off_app():
    s.update({"app_rules": {"Google Chrome": False}})
    out = pa.run("open_app", {"name": "chrome"})
    assert str(out).startswith("FAILED:") and "Google Chrome is switched off" in out


def test_window_actions_refuse_a_switched_off_app(monkeypatch):
    s.update({"app_rules": {"Google Chrome": False}})

    class W:
        app, title, hwnd = "chrome", "YouTube - Google Chrome", 1
    monkeypatch.setattr(pc_windows, "find_window", lambda target: W())
    out = pc_windows.request("close", "chrome")
    assert "switched off in your extensions" in str(out) and pc_windows.pending() is None


def test_an_app_switched_back_on_is_allowed_again():
    s.update({"app_rules": {"Google Chrome": True}})
    assert pa.blocked_app("chrome") is None
