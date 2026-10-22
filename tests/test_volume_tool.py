"""Volume tool: relative changes, and muting everything except MBUDDY.

Live failures, 2026-09-26: "turn up the volume" answered "System audio
device unreachable" (COM was not initialised on the worker thread), and even
when reachable a request without a number only reported the level; "mute"
muted the whole PC, MBUDDY's own voice included.
"""
import pytest

from brain.tools import plan_volume, is_mbuddy_voice


@pytest.mark.parametrize("query, current, expected", [
    ("turn up the volume", 40, ("set", 50)),
    ("turn it down a bit", 40, ("set", 30)),
    ("turn it up a lot", 40, ("set", 60)),
    ("louder", 95, ("set", 100)),
    ("quieter please", 5, ("set", 0)),
    ("set the volume to 30 percent", 80, ("set", 30)),
    ("max volume", 20, ("set", 100)),
    ("mute the audio", 50, ("mute_others", None)),
    ("unmute", 50, ("unmute_all", None)),
    ("what's the volume?", 42, ("report", 42)),
    ("my head hurts, the volume is too loud", 60, ("set", 50)),
    ("it's way too loud", 60, ("set", 40)),
    ("I can't hear anything, it's too quiet", 30, ("set", 40)),
    ("um, the volume is so high", 70, ("set", 60)),
    ("the audio is still really high", 70, ("set", 60)),
    ("set the volume to 50 percent", 20, ("set", 50)),
])
def test_plan_volume(query, current, expected):
    assert plan_volume(query, current) == expected


class FakeProc:
    def __init__(self, name, parents=()):
        self._name, self._parents = name, [FakeProc(p) for p in parents]

    def name(self):
        return self._name

    def parents(self):
        return self._parents


def test_mbuddy_voice_is_the_shell_and_its_children():
    assert is_mbuddy_voice(FakeProc("mbuddy-shell.exe"))
    assert is_mbuddy_voice(FakeProc("msedgewebview2.exe", parents=["msedgewebview2.exe", "mbuddy-shell.exe", "explorer.exe"]))
    # Another app's WebView2 (Teams, widgets) is not MBUDDY.
    assert not is_mbuddy_voice(FakeProc("msedgewebview2.exe", parents=["ms-teams.exe"]))
    assert not is_mbuddy_voice(FakeProc("spotify.exe"))
    assert not is_mbuddy_voice(None)  # system sounds


def test_extra_voice_processes_from_env(monkeypatch):
    monkeypatch.setenv("MBUDDY_VOICE_PROCESSES", "chrome.exe")
    assert is_mbuddy_voice(FakeProc("chrome.exe"))


@pytest.mark.parametrize("action, amount, current, expected", [
    ("down", 40, 60, ("set", 45)),   # steps are capped at 15 points
    ("down", 30, 12, ("set", 5)),    # never below 5% (live: agent went 70 -> 40 -> 10)
    ("down", None, 60, ("set", 50)),
    ("up", 30, 90, ("set", 100)),
    ("up", 40, 30, ("set", 45)),
    ("set", 25, 60, ("set", 25)),
    ("mute", 0, 60, ("mute_others", None)),
    ("unmute", None, 60, ("unmute_all", None)),
])
def test_structured_volume_actions(action, amount, current, expected):
    from brain.tools import volume_plan_for_action
    assert volume_plan_for_action(action, amount, current) == expected


def test_percent_about_volume_goes_to_the_volume_tool():
    # Live: "Set the volume to 50 percent" matched the calculator rule first.
    from brain.cognitive_state import build_cognitive_state, decide_behavior
    from brain.world_model import build_world_model
    from core.state_manager import MBUDDYState
    from core.status import runtime_status

    text = "Set the volume to 50 percent."
    st = MBUDDYState()
    wm = build_world_model(st, session=None, runtime=runtime_status, memory={}, event="thinking", text=text)
    cs = build_cognitive_state(st, session=None, runtime=runtime_status, memory={}, world_model=wm)
    assert decide_behavior(cs, event="thinking", text=text).tool == "sys_volume"
