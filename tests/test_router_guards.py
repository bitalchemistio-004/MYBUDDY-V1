from core.state_manager import MBUDDYState
from brain import cognitive_state


def _tool(text):
    cog = cognitive_state.build_cognitive_state(MBUDDYState(), memory={})
    return cognitive_state.decide_behavior(cog, event="thinking", text=text).tool


def test_a_long_reasoning_question_is_not_a_calculator_request():
    text = ("I have meetings of 45, 60 and 30 minutes. Two must be before lunch, with 15 minute gaps. "
            "What is the best order and the start times from 9?")
    assert _tool(text) != "calculator"


def test_a_short_arithmetic_request_still_uses_the_calculator():
    assert _tool("what is 15 times 4") == "calculator"
