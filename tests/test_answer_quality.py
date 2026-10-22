"""Answers that say what was asked (S115). A plain lookup gets a one-sentence
reply, so the clock's answer is one sentence with the time first ("What time
is it?" was answered with the date only); and a lookup's answer is given, not
just "Yes."."""
import re

from brain.memory_context import TURN_RULES
from brain.tools import execute_clock_tool


def test_the_clock_answers_in_one_sentence_with_the_time_first():
    text = execute_clock_tool()
    assert text.startswith("It's ") and re.match(r"It's \d{1,2}:\d{2} (AM|PM) on ", text)
    assert text.count(". ") == 0 and text.endswith(").")      # one sentence


def test_a_lookup_is_answered_with_what_it_found():
    assert "tell him what it found" in TURN_RULES and "not only yes or no" in TURN_RULES
