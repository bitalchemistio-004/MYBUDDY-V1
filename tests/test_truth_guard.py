"""Code-level truth guard: replies can't claim actions or a past that didn't happen.

Every example is a real reply from the scenario harness or the live log
(2026-09-26), where the prompt rules alone did not stop the small model.
"""
import asyncio

from brain.llm_router import truth_guard


def run(chunks, tools_ran=False, memory_text=""):
    async def gen():
        for c in chunks:
            yield c

    async def go():
        return "".join([d async for d in truth_guard(gen(), tools_ran=tools_ran, memory_text=memory_text)]).strip()

    return asyncio.run(go())


def test_action_claims_without_a_tool_are_dropped():
    # Only a filler would remain, so the honest line replaces it.
    assert run(["[NORMAL] Mm-hmm. I pulled the trigger."]) == "[NORMAL] Nothing changed just now."
    assert run(["[NORMAL] I recalibrated. Now it’s at 50%."]) == "[NORMAL] Nothing changed just now."
    assert run(["[SKEPTIC] No, it’s not. I just checked."]) == "[SKEPTIC] No, it's not."  # apostrophes come out normalised


def test_action_claims_with_a_tool_stay():
    assert run(["[NORMAL] Volume cut to 50%. Rest a bit."], tools_ran=True) == "[NORMAL] Volume cut to 50%. Rest a bit."


def test_invented_habits_and_past_are_dropped_when_memory_lacks_them():
    assert run(["[NORMAL] Mm-hmm. You always say that after the third coffee."]) == "[NORMAL] I don't know that about you yet."
    assert run(["[NORMAL] You skip breakfast. You leave the kettle on."]) == "[NORMAL] I don't know that about you yet."
    assert run(["[NORMAL] Mm-hmm. I still remember you eating it at 3 a.m."]) == "[NORMAL] I don't know that about you yet."


def test_supported_memories_stay():
    memory = "Sam skips breakfast on workdays"
    assert run(["[NORMAL] You skip breakfast again, huh?"], memory_text=memory) == "[NORMAL] You skip breakfast again, huh?"


def test_ordinary_replies_pass_untouched():
    reply = "[HAPPY] That sounds exhausting. Want to take a short break?"
    assert run([reply]) == reply
    assert run(["[NORMAL] Hey! ", "How was ", "your day?"]) == "[NORMAL] Hey! How was your day?"


def test_invented_shared_conversation_is_cut():
    # Harness 2026-09-27: "what time is it?" in a fresh session.
    assert run(["[NORMAL] It's 11:30 AM, just like we said."]) == "[NORMAL] It's 11:30 AM."
    assert run(["[NORMAL] Done, as promised."], tools_ran=True) == "[NORMAL] Done."


def test_window_claims_without_a_tool_are_cut():
    # Live 2026-09-27: the model said this with no tool run (the window stayed open).
    # Everything after a false claim goes too (S96): a reply built on it can't
    # be told apart from its consequences ("...The room should go dark now.").
    assert run(["[NORMAL] Closed the scratch notes window. You're not alone in this."]) == "[NORMAL] Nothing changed just now."
    assert run(["[NORMAL] Minimizing Chrome for you."]) == "[NORMAL] Nothing changed just now."


def test_like_we_talked_is_cut():
    # Live 2026-09-27: "I've got that saved — just like we talked." (first mention).
    assert run(["[NORMAL] Saved it, just like we talked. I'll nudge you."], tools_ran=True) == "[NORMAL] Saved it. I'll nudge you."
