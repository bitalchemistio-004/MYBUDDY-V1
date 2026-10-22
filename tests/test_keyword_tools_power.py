"""Keyword tools run whatever the power state is, and the clock answers only questions about the current time.

The field test (2026-10-19): with the laptop plugged in (charging) and MBUDDY's energy low, decide_behavior returned the
power-state behaviour first, so no keyword tool ever ran and the model invented the time ("ten fifteen" at 21:04)."""
import dataclasses

import pytest

from brain import cognitive_state as cs
from core.state_manager import MBUDDYState


def decide(text, charging=False, energy=60.0):
    cog = cs.build_cognitive_state(MBUDDYState(), memory={})
    cog = dataclasses.replace(cog, charging=charging, energy=energy)
    return cs.decide_behavior(cog, event="thinking", text=text)


@pytest.mark.parametrize("charging,energy", [(False, 60.0), (True, 60.0), (False, 8.0), (True, 8.0)])
def test_the_clock_runs_when_charging_or_tired(charging, energy):
    d = decide("WHAT TIME IS IT".lower(), charging, energy)
    assert d.tool == "clock"
    if energy < 20:
        assert d.emotion == "Sleepy"            # the mood still follows the power state


@pytest.mark.parametrize("text", ["hi, what time is it?", "whats the time", "what's the time please", "tell me the time",
                                  "what's the date today", "what day is it", "كم الساعة الآن؟", "شو الساعة"])
def test_plain_time_questions_go_to_the_clock(text):
    assert decide(text, charging=True).tool == "clock"


@pytest.mark.parametrize("text", ["what time do i take my pills again", "what time is the meeting", "what time does the shop close"])
def test_the_time_of_something_is_not_the_clock(text):
    assert decide(text).tool != "clock"


@pytest.mark.parametrize("text,tool", [("set a timer for 5 minutes", "timer"), ("what is 17 times 23", "calculator"),
                                       ("remind me to call mum", "task"), ("turn the volume down", "sys_volume")])
def test_other_keyword_tools_run_while_charging(text, tool):
    assert decide(text, charging=True, energy=8.0).tool == tool


def test_charging_without_a_tool_keeps_the_charging_behaviour():
    assert decide("hello there", charging=True).animation in ("battery_charge", "")
