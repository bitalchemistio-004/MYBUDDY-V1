"""Emotion tags come out of a reply; the user's own bracket text does not (field test F40: "[S1] send the deck")."""
import re
from pathlib import Path

from brain.personality import EMOTION_TAG_TO_STATE, extract_emotion_state, strip_emotion_tags, strip_for_tts


def test_known_tags_go_anywhere_and_invented_ones_only_at_the_start():
    assert strip_emotion_tags("[HAPPY] Reminder set: [S1] send the deck") == "Reminder set: [S1] send the deck"
    assert strip_emotion_tags("[CURIOUS] Done.") == "Done."
    assert strip_emotion_tags("Done [glee] now") == "Done now"
    assert strip_emotion_tags("[FOCUSED] Your list: [URGENT] call mom, [draft] essay") == "Your list: [URGENT] call mom, [draft] essay"


def test_a_first_word_that_is_also_a_mood_stays():
    assert strip_emotion_tags("Happy birthday to Sam") == "Happy birthday to Sam"
    assert strip_emotion_tags("Sad news, the build failed") == "Sad news, the build failed"
    assert strip_emotion_tags("HAPPY yeah sure") == "yeah sure"


def test_the_voice_reads_user_brackets_but_never_a_tag():
    assert strip_for_tts("[SKEPTIC] Added [S1] send the deck [WEIRDTAG]") == "Added [S1] send the deck"


def test_the_mood_is_read_from_the_tag_not_from_user_brackets():
    assert extract_emotion_state("[S1] done [HAPPY]") == "Happy"
    assert extract_emotion_state("[S1] nothing else") is None


def test_the_ui_strips_the_same_tags_as_the_backend():
    js = (Path(__file__).resolve().parents[1] / "shell" / "ui" / "js" / "api.js").read_text(encoding="utf-8")
    listed = set(re.search(r"const EMOTION_TAGS = /\\\[\(([^)]*)\)", js).group(1).split("|"))
    assert listed == {k.strip("[]") for k in EMOTION_TAG_TO_STATE}


def test_a_bullet_is_not_a_stage_direction():
    # live: with bullet points on, "* *Chuckles* That's a lot" became "Chuckles* That's a lot"
    from brain.llm_router import _only_playable_directions
    out = _only_playable_directions("* *types furiously* That's a lot of Blenders.\n* Want me to close the older ones?")
    assert "types" not in out and out.startswith("* ") and "\n* Want me" in out
    js = (Path(__file__).resolve().parents[1] / "shell" / "ui" / "js" / "api.js").read_text(encoding="utf-8")
    assert r"\*(?!\s)" in js
