"""Inner thoughts must not loop on one image (live: 37 of 40 mentioned "neon")."""
from core.state_manager import MBUDDYState, drop_motif, overused_words

NEON = [
    "The neon's still humming, soft as a breath.",
    "The neon's just a little light, quiet and steady.",
    "The neon's still humming like the room remembers how to breathe.",
    "I wonder what Sam is building tonight.",
    "The neon's still humming, keeping time.",
]


def test_overused_words_finds_the_repeated_image():
    banned = overused_words(NEON)
    assert "neon" in banned and "humming" in banned
    assert "sam" not in banned  # said once
    assert "still" not in banned and "the" not in banned  # filler, not an image


def test_drop_motif_removes_lines_with_the_image():
    assert drop_motif(NEON, {"neon"}) == ["I wonder what Sam is building tonight."]
    assert drop_motif(NEON, set()) == NEON


def test_narrative_context_can_exclude_the_motif():
    state = MBUDDYState()
    for t in NEON:
        state.push_narrative(t)
    context = state.get_narrative_context(exclude={"neon"})
    assert "neon" not in context.lower() and "building tonight" in context
    assert "neon" in state.get_narrative_context().lower()  # unchanged by default
