"""A quoted name or title isn't the reply's language (W1d). Live, MBUDDY's own
line about a song it had started was blocked as the wrong language because of
the song's title, and the user heard "could you ask me that once more?"."""
from stt.language_policy import reply_matches_language, scrub_arabizi_output


def test_a_quoted_title_doesnt_make_an_english_reply_the_wrong_language():
    assert reply_matches_language('[HAPPY] Playing "Bektoub Ismak Ya Habibi" by Fairuz on YouTube Music.', "en")
    assert reply_matches_language('[NORMAL] I saved it as “yalla habibi notes” in your documents.', "en")


def test_the_sentence_itself_is_still_judged():
    assert not reply_matches_language("[HAPPY] Yalla habibi, khalas, I played it.", "en")


def test_speech_keeps_a_quoted_title_whole():
    said = scrub_arabizi_output('Playing "Bektoub Ismak Ya Habibi" by Fairuz on YouTube Music.')
    assert '"Bektoub Ismak Ya Habibi"' in said
    assert "habibi" not in scrub_arabizi_output("Yalla habibi, here you go.").lower()
