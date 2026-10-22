"""A turn only sees this conversation (F46: 'try again' ran another person's routine from an earlier one)."""
from brain.llm_router import CONVERSATION_GAP_S, this_conversation


def test_a_long_gap_starts_a_new_conversation():
    now = 1_000_000.0
    old = now - CONVERSATION_GAP_S - 600
    history = [{"role": "user", "content": "run my morning routine", "at": old},
               {"role": "assistant", "content": "Done.", "at": old},
               {"role": "user", "content": "make a chair in blender", "at": now - 120},
               {"role": "assistant", "content": "Doing it.", "at": now - 118}]
    assert [h["content"] for h in this_conversation(history, now)] == ["make a chair in blender", "Doing it."]
    assert this_conversation(history[:2], now) == []                 # 'try again' has nothing to redo


def test_a_conversation_is_kept_while_it_goes_on():
    now = 1_000_000.0
    steps = [{"role": "user", "content": str(i), "at": now - 3 * 3600 + i * 600} for i in range(19)]
    assert len(this_conversation(steps, now - 3 * 3600 + 18 * 600 + 60)) == 19   # ten-minute steps: one conversation
    assert this_conversation([{"role": "user", "content": "legacy"}], now) == [{"role": "user", "content": "legacy"}]
