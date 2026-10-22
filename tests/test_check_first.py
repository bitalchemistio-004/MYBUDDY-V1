"""Check the real state instead of trusting earlier answers (user, 2026-09-26).

Live: "Are you sure it's still high?" -> "I just checked. It's not." and
"The audio is still high." -> "I recalibrated. Now it's at 50%." Nothing had
been checked or changed; MBUDDY repeated its own earlier claim. Now a
verification question makes it say "Let me check." and answer from the
real state and from actions it really took.
"""
import asyncio

import pytest

from brain import action_log
from brain.llm_router import is_verification_question, limit_sentences, truth_guard


@pytest.mark.parametrize("text", [
    "Are you sure it's still high?", "Did you do it?", "Is it muted?", "What's the volume?",
    "Did it work?", "Can you check again?", "Is the timer still running?", "What did you just do?",
])
def test_verification_questions(text):
    assert is_verification_question(text)


@pytest.mark.parametrize("text", ["I'm bored.", "Tell me a joke.", "My sister's name is Lina.", "Set a timer for 5 minutes."])
def test_not_verification(text):
    assert not is_verification_question(text)


def test_action_log_records_only_real_actions_and_is_bounded():
    action_log.clear()
    for i in range(30):
        action_log.record("adjust_volume", {"action": "down"}, f"System volume set to {60 - i}%.")
    recent = action_log.recent()
    assert len(recent) == 10 and recent[-1]["result"] == "System volume set to 31%."
    assert "System volume set to 31%" in action_log.block()
    action_log.clear()
    assert action_log.block() == ""


def _guard(chunks, **kw):
    async def gen():
        for c in chunks:
            yield c

    async def go():
        return "".join([d async for d in truth_guard(gen(), **kw)]).strip()

    return asyncio.run(go())


def test_wrong_volume_number_is_replaced_with_the_real_one():
    assert _guard(["[NORMAL] It's at 50%."], tools_ran=False, memory_text="", real_volume=23) == "[NORMAL] It's at 23%."


def test_more_false_claims_are_dropped():
    assert _guard(["[NORMAL] Mm-hmm. Did it."], tools_ran=False, memory_text="") == "[NORMAL] Nothing changed just now."
    assert _guard(["[FOCUSED] Yeah, it's still high, checked the last reading."], tools_ran=False, memory_text="") \
        == "[FOCUSED] Nothing changed just now."
    assert _guard(["[NORMAL] You've been staring at the screen for 45 minutes."], tools_ran=False, memory_text="") \
        == "[NORMAL] I don't know that about you yet."


def test_a_real_check_may_say_it_checked():
    assert _guard(["[NORMAL] I checked, it's at 23%."], tools_ran=False, memory_text="", checked=True, real_volume=23) \
        == "[NORMAL] I checked, it's at 23%."


def test_filler_openers_are_removed_when_content_follows():
    assert _guard(["[NORMAL] Mm-hmm. You're welcome, anytime."], tools_ran=False, memory_text="") == "[NORMAL] You're welcome, anytime."


def test_fragments_do_not_use_up_the_sentence_budget():
    async def gen():
        yield "[NORMAL] Oof. Long day, huh? What happened at work today? Tell me everything. And more."

    async def go():
        return "".join([d async for d in limit_sentences(gen(), 2)])

    assert asyncio.run(go()) == "[NORMAL] Oof. Long day, huh? What happened at work today?"


def test_claims_backed_by_the_action_log_stay_and_others_go():
    log_text = "WHAT YOU ACTUALLY DID RECENTLY:\n- 19:13 set_timer: Timer set for 3 minutes."
    assert _guard(["[NORMAL] I set a timer for three minutes."], tools_ran=False, memory_text="", actions_text=log_text) \
        == "[NORMAL] I set a timer for three minutes."
    assert _guard(["[NORMAL] I lowered the volume."], tools_ran=False, memory_text="", actions_text=log_text) \
        == "[NORMAL] Nothing changed just now."


def test_a_question_about_the_state_does_not_change_it():
    from brain.agent import drop_writes_for_a_question
    calls = [{"name": "adjust_volume", "args": {"action": "set", "amount": 50}},
             {"name": "adjust_volume", "args": {"action": "down", "amount": 15}},
             {"name": "set_timer", "args": {"minutes": 3}}, {"name": "get_time", "args": {}}]
    assert [c["name"] + str(c["args"].get("action", "")) for c in drop_writes_for_a_question(calls)] == \
        ["adjust_volumedown", "get_time"]


def test_invented_past_from_the_harness_is_dropped():
    assert _guard(["[SAD] Oh. I just remembered, last week you said you loved that old arcade game we played when you were ten."],
                  tools_ran=False, memory_text="") == "[SAD] I don't know that about you yet."
    # Said for the first time this turn: "before" is still invented.
    assert _guard(["[HAPPY] Oh wow, that's so cool! I remember you mentioned Lina before."], tools_ran=False, memory_text="") \
        == "[HAPPY] Oh wow, that's so cool!"


def test_made_up_tags_become_real_ones():
    from brain.llm_router import _leading_tag_fix
    assert _leading_tag_fix("[WOORRIED] Man, that sounds rough.") == "[WORRIED] Man, that sounds rough."
    assert _leading_tag_fix("[HOPEFUL] Oh! So we need some.") == "[NORMAL] Oh! So we need some."
    assert _leading_tag_fix("[HAPPY] Hi.") == "[HAPPY] Hi."


def test_the_models_own_let_me_check_is_not_said_twice():
    from brain.llm_router import strip_own_check_opener
    assert strip_own_check_opener("[FOCUSED] Let me check… yeah, it's 45% now. ") == "Yeah, it's 45% now. "
    assert strip_own_check_opener("[NORMAL] Okay, I'll check again. ") == ""
    assert strip_own_check_opener("[NORMAL] It's at 37%. ") == "It's at 37%. "


def test_a_tag_split_across_chunks_is_still_fixed():
    from brain.llm_router import ensure_leading_tag

    async def gen():
        for c in ["[WOO", "RRIED] Oof, ", "that sounds rough."]:
            yield c

    async def go():
        return "".join([d async for d in ensure_leading_tag(gen())])

    assert asyncio.run(go()) == "[WORRIED] Oof, that sounds rough."


def test_last_and_latest_searches_get_the_current_year():
    from datetime import datetime
    from brain.tools import with_current_year
    year = datetime.now().year
    assert with_current_year("who won the last Champions League final") == f"who won the last Champions League final {year}"
    assert with_current_year("Champions League final 2024") == "Champions League final 2024"
    assert with_current_year("capital of France") == "capital of France"


def test_knowing_it_already_is_invented_when_memory_was_empty():
    assert _guard(["[HAPPY] Oh yeah! I knew that already, you're the shawarma king."], tools_ran=False, memory_text="") \
        == "[HAPPY] Oh yeah!"
    # The remember tool did run this turn; what's invented is "already" and "before".
    assert _guard(["[HAPPY] Lina, that's the name I already saved. You're the one who told me that before, right?"],
                  tools_ran=True, memory_text="") == "[HAPPY] I don't know that about you yet."


def test_still_loud_right_after_a_volume_change_is_a_volume_followup():
    from brain.agent import volume_followup_call
    action_log.clear()
    assert volume_followup_call("It's still high.") is None  # no volume change to follow up on
    action_log.record("sys_volume", {}, "System volume set to 60%.")
    assert volume_followup_call("Are you sure? It's still high.") == {"name": "adjust_volume", "args": {"action": "down"}}
    assert volume_followup_call("I still can't hear you.") == {"name": "adjust_volume", "args": {"action": "up"}}
    assert volume_followup_call("I'm bored.") is None
    action_log.clear()


def test_invented_continuity_is_cut_and_the_answer_kept():
    assert _guard(["[FOCUSED] The volume's been at 37% since I checked."], tools_ran=False, memory_text="",
                  checked=True, real_volume=37) == "[FOCUSED] The volume's been at 37%."
    assert _guard(["[FOCUSED] Yeah, it's at 37%. No changes since."], tools_ran=False, memory_text="",
                  checked=True, real_volume=37) == "[FOCUSED] Yeah, it's at 37%."
    assert _guard(["[NORMAL] I'll remind you in 10 minutes, just like we agreed."], tools_ran=True, memory_text="") \
        == "[NORMAL] I'll remind you in 10 minutes."


def test_volume_promises_and_stale_levels_are_dropped():
    # Live: the tool had lowered it to 45%, the reply said it was "still" there and promised to lower it.
    assert _guard(["[FOCUSED] Yeah, it's still at 60%. I'll lower it to 45% now. That should help."],
                  tools_ran=True, memory_text="", real_volume=45) == "[FOCUSED] That should help."
    assert _guard(["[NORMAL] I'll turn it down for you."], tools_ran=False, memory_text="") == "[NORMAL] Nothing changed just now."


@pytest.mark.parametrize("text,item", [
    ("We're out of milk.", "milk"), ("I ran out of coffee again", "coffee"),
    ("we are out of the paper towels, ugh", "paper towels"), ("I'm out of time.", None), ("I'm bored.", None),
])
def test_running_out_of_something_becomes_a_task(text, item):
    from brain.agent import ran_out_of
    assert ran_out_of(text) == item


def test_a_recall_does_not_back_a_claim_of_saving_a_document():
    # live 2026-10-11: the job request went to the memory tool (a lookup) and the reply said "saved ... into that Word document"
    log_text = "WHAT YOU ACTUALLY DID RECENTLY:\n- memory: Recalled from memory:"
    said = "[GLEE] All done! I've gathered the facts on Mount Everest and saved everything into that Word document for you!"
    out = _guard([said], tools_ran=True, memory_text="", actions_text=log_text, acted=False)
    assert "saved" not in out.lower() and "word document" not in out.lower()
    # a real save still backs "I saved that"
    ok = "WHAT YOU ACTUALLY DID RECENTLY:\n- memory: Saved: your sister's birthday is in May."
    assert "saved" in _guard(["[NORMAL] I saved that."], tools_ran=False, memory_text="", actions_text=ok).lower()


def test_a_plain_line_verdict_from_the_self_check_is_read_not_lost():
    # live 2026-10-11: "fail\nReason: ..." came back instead of JSON, so every self-check ended "unknown"
    from brain.outcomes import JUDGE_SCHEMA, _loose
    got = _loose("fail\nReason: The action only recalled the request; it did not create a document", JUDGE_SCHEMA)
    assert got["verdict"] == "fail" and "recalled" in got["reason"]
    assert _loose("no idea, honestly", JUDGE_SCHEMA) is None
