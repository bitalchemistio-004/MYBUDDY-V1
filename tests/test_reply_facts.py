"""A reply never states a detail that this turn's tool results contradict (the field test, 2026-10-19:
"it's just a one-time reminder" while the tool set a daily one; "Saved as [QA] Bicycle history.docx" when it was
"... (2).docx"; "sunny tomorrow, 25" from the current reading)."""
import asyncio

from brain.llm_router import contradicts_results, truth_guard

DAILY = "TOOL OUTPUT (add_reminder): Reminder set: call the supplier, every day. The first one is Tue 20 Oct at 18:00."
ONCE = "TOOL OUTPUT (add_reminder): Reminder set: call the supplier on Tue 20 Oct 2026 at 18:00."
SAVED = 'TOOL OUTPUT (word_write): Saved as C:\\Users\\dell\\Documents\\MBUDDY\\[QA] Bicycle history (2).docx.'
NOW = "TOOL OUTPUT (get_weather): Current weather in Cairo, Egypt: Sunny, 25°C (feels like 26°C), humidity 40%, wind 10 km/h"


def test_once_against_a_daily_result():
    assert contradicts_results("I've made it a one-time reminder for tomorrow.", DAILY)
    assert not contradicts_results("It's set for every day at 6 PM.", DAILY)


def test_repeating_against_a_one_time_result():
    assert contradicts_results("It will remind you every day.", ONCE)
    assert not contradicts_results("I'll remind you tomorrow at 6 PM.", ONCE)


def test_a_time_the_result_does_not_have():
    assert contradicts_results("It's set for 7 PM.", ONCE)
    assert not contradicts_results("It's set for 6 PM.", ONCE)


def test_a_file_name_the_result_does_not_have():
    assert contradicts_results("Saved as [QA] Bicycle history.docx.", SAVED)
    assert not contradicts_results("Saved as [QA] Bicycle history (2).docx.", SAVED)


def test_a_forecast_from_the_current_weather():
    assert contradicts_results("Cairo will be sunny tomorrow, around 25 degrees.", NOW)
    assert not contradicts_results("It's sunny in Cairo right now, 25 degrees.", NOW)


def test_web_results_with_times_do_not_trip_the_time_check():
    web = "TOOL OUTPUT (web_search): Live search results (fetched Monday, October 19, 2026 at 23:07): the match starts at 20:00."
    assert not contradicts_results("The match starts at 8 PM.", web)


def test_the_guard_drops_the_contradicting_sentence_and_keeps_the_rest():
    async def stream():
        yield "[NORMAL] Done. I've changed it to a one-time reminder. You'll get it at 6 PM."

    async def go():
        return "".join([p async for p in truth_guard(stream(), tools_ran=True, memory_text="", acted=True, results_text=DAILY)])
    out = asyncio.run(go())
    assert "one-time" not in out and "6 PM" in out


def test_a_translation_request_may_be_answered_in_that_language():
    from stt.language_policy import asks_for_other_language
    assert asks_for_other_language("translate 'the meeting moved to Thursday' to French and Arabic")
    assert asks_for_other_language("how do you say thank you in Spanish")
    assert asks_for_other_language("ترجم هذه الجملة")
    assert not asks_for_other_language("what's the weather in Paris")
