"""Local-model reply formatting: tags, language check, spoken laughs.

qwen3:4b-instruct often writes the emotion as a plain word ("Focused. ...")
instead of a tag ("[FOCUSED] ..."). The old language check treated a missing
tag as a wrong language and replaced good replies with "Sorry, could you ask
me that once more?" (seen live, 2026-09-26).
"""
import asyncio

from brain.llm_router import ensure_leading_tag
from stt.language_policy import reply_matches_language
from tts.local_tts import split_expressions


def _collect(chunks):
    async def gen():
        for c in chunks:
            yield c

    async def run():
        return "".join([d async for d in ensure_leading_tag(gen())])

    return asyncio.run(run())


def test_missing_tag_is_not_a_wrong_language():
    assert reply_matches_language("Focused. Still running on three batteries and a dream.", "en")
    assert reply_matches_language("Haha. You always say you'll document it later.", "en")
    # Real language problems are still caught.
    assert not reply_matches_language("[NORMAL] كيفك اليوم يا صاحبي؟ شو عم تعمل", "en")


def test_bare_emotion_word_becomes_a_tag_even_split_across_chunks():
    assert _collect(["Foc", "used. Still", " running."]) == "[FOCUSED] Still running."
    assert _collect(["happy! Great news"]) == "[HAPPY] Great news"


def test_untagged_reply_gets_normal_and_tagged_reply_is_untouched():
    assert _collect(["Hello there, how are you?"]) == "[NORMAL] Hello there, how are you?"
    assert _collect(["[GLEE] ", "yes!"]) == "[GLEE] yes!"
    assert _collect(["Hi"]) == "[NORMAL] Hi"  # shorter than the look-ahead
    assert _collect([]) == ""


def test_an_ordinary_first_word_is_not_mistaken_for_an_emotion():
    assert _collect(["Normally I would, but"]) == "[NORMAL] Normally I would, but"


def test_written_laughs_become_clips_not_words():
    assert split_expressions("Haha. You always say that.") == [("clip", "laugh"), ("say", "You always say that.")]
    assert split_expressions("hehehe okay") == [("clip", "laugh"), ("say", "okay")]
    assert split_expressions("Heh, fine.") == [("clip", "chuckle"), ("say", "fine.")]
    assert split_expressions("Aha! I see.") == [("say", "Aha! I see.")]
    assert split_expressions("Ha, got you.") == [("say", "Ha, got you.")]


def _limited(chunks, n):
    from brain.llm_router import limit_sentences

    async def gen():
        for c in chunks:
            yield c

    async def run():
        return "".join([d async for d in limit_sentences(gen(), n)])

    return asyncio.run(run())


def test_reply_stops_after_the_sentence_limit():
    # The live reply that ran on for ten sentences.
    # A fragment costs half a sentence: two fragments + one sentence fill a budget of 2.
    chunks = ["[NORMAL] Mm-hmm. One minute. ", "I'll start the hum. Low. Like a fridge."]
    assert _limited(chunks, 2) == "[NORMAL] Mm-hmm. One minute. I'll start the hum."


def test_ellipses_and_tags_are_not_sentence_ends():
    assert _limited(["[FOCUSED] Just… silence, and then... nothing at all! More here, much more."], 2) == \
        "[FOCUSED] Just… silence, and then... nothing at all! More here, much more."
    assert _limited(["[FOCUSED] Just… silence, and then... nothing at all! More here, much more. Extra words stay out."], 2) == \
        "[FOCUSED] Just… silence, and then... nothing at all! More here, much more."


def test_short_replies_pass_whole():
    assert _limited(["[HAPPY] Done."], 2) == "[HAPPY] Done."


def test_sentence_budget_depends_on_the_request():
    from brain.reply_policy import reply_max_sentences

    assert reply_max_sentences("hey buddy how are you") == 2
    assert reply_max_sentences("tell me a story about a dragon") >= 5
    assert reply_max_sentences("explain in detail how a router works") >= 5


def test_bare_stage_words_play_clips():
    # Live: "Sighs. How about we check the weather?" would read "Sighs" aloud.
    assert split_expressions("Sighs. How about we check the weather?") == [
        ("clip", "sigh"), ("say", "How about we check the weather?")]
    assert split_expressions("Laughs. Okay, fair.") == [("clip", "laugh"), ("say", "Okay, fair.")]
    assert split_expressions("He laughs at that.") == [("say", "He laughs at that.")]  # mid-sentence stays speech


def test_emotion_word_alone_on_the_first_line_becomes_a_tag():
    # Live 2026-09-27: "[NORMAL] HAPPY \nThat's awesome" (the voice would say "Happy").
    assert _collect(["HAPPY  \nThat's awesome — I love it."]) == "[HAPPY] That's awesome — I love it."
    assert _collect(["SAD\n\nYou've had a long day."]) == "[SAD] You've had a long day."
    assert _collect(["Hello\nthere"]) == "[NORMAL] Hello\nthere"  # not an emotion: untouched
