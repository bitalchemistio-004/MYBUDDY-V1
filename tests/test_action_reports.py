"""S96: honesty as general rules, not example patches.
- Code says what every PC action did (spoken_results); the model can't add claims.
- Only stage directions MBUDDY's voice can make survive (a laugh, a sigh).
- After code said the facts, a reply the guard empties stays silent."""
import asyncio

from brain import agent
from brain.llm_router import _only_playable_directions, truth_guard


def test_action_results_are_said_without_diagnostics():
    lines = agent.spoken_results([
        ("open_app", {}, 'Opened chrome (window appeared: yes, "YouTube Music - Google Chrome").'),
        ("create_file", {}, r"Created notes.txt in C:\Users\example\Documents\MBUDDY (12 bytes)."),
        ("word_write", {}, 'Wrote 40 words into "Document2" in Word (checked in the document). It isn\'t saved yet.'),
    ])
    assert lines == ["Opened chrome.", r"Created notes.txt in Documents\MBUDDY.",
                     'Wrote 40 words into "Document2" in Word. It isn\'t saved yet.']


def test_a_failed_or_unconfirmed_action_is_said_as_it_is():
    lines = agent.spoken_results([
        ("music_play", {}, 'NOT PLAYING: I opened "jazz" on YouTube Music, but Windows doesn\'t show it playing.'),
        ("open_app", {}, "FAILED: no installed app matches 'Photoshop'."),
    ])
    assert lines[0].startswith('I opened "jazz"') and "NOT PLAYING" not in lines[0]
    assert lines[1] == "no installed app matches 'Photoshop'."


def test_reads_and_waiting_questions_are_not_said_by_code():
    assert agent.spoken_results([
        ("find_apps", {}, "Apps: Notepad, Word"),                       # a read: the model answers from it
        ("lock_screen", {}, "NOT DONE YET - waiting for the user's yes."),  # asked elsewhere, in code
        ("get_time", {}, "It's 14:10."),                                 # a core tool, not a PC action
    ]) == []


def test_only_sounds_the_voice_can_make_survive():
    text = "*laughs* Sure. *types gently* Done *really* fast. *smiles* *sighs*"
    assert _only_playable_directions(text) == "*laughs* Sure.  Done really fast.  *sighs*"


def run(chunks, **kw):
    async def gen():
        for c in chunks:
            yield c

    async def go():
        return "".join([d async for d in truth_guard(gen(), memory_text="", **kw)]).strip()
    return asyncio.run(go())


def test_after_code_said_the_facts_an_emptied_reply_stays_silent():
    # The model's own claim is dropped, and no "Nothing changed" line replaces it.
    assert run(["[HAPPY] I've opened it and started the music."], tools_ran=False, quiet_if_empty=True) == ""
    # A warm sentence with no claim still comes through.
    assert run(["[HAPPY] Enjoy it!"], tools_ran=False, quiet_if_empty=True) == "[HAPPY] Enjoy it!"


# ── claims by grammar, not by a list of verbs (S96) ──────────────────────
def test_claims_of_doing_are_dropped_when_nothing_acted():
    for claim in ["[FOCUSED] Alright, I'm turning off the lights for you.",
                  "[FOCUSED] Okay, I'll get that for you.",
                  "[NORMAL] Let me order it now.",
                  "[NORMAL] I'm booking the table as we speak."]:
        assert run([claim], tools_ran=True, acted=False) == claim.split()[0] + " Nothing changed just now.", claim


def test_being_feeling_and_thinking_are_not_claims():
    for line in ["[HAPPY] I'm feeling great today.", "[NORMAL] Let me know how it goes.",
                 "[NORMAL] I'll be here if you need me.", "[NORMAL] I'm guessing it was a long day.",
                 "[NORMAL] I'm not sure about that one.", "[HAPPY] I'm doing well, thanks for asking."]:
        assert run([line], tools_ran=False, acted=False) == line, line


def test_a_promise_backed_by_an_acting_tool_stays():
    # add_reminder really ran: "I'll remind you" describes what will happen.
    assert run(["[NORMAL] Done. I'll remind you at five."], tools_ran=True, acted=True) == \
        "[NORMAL] Done. I'll remind you at five."


def test_the_rest_of_a_reply_built_on_a_false_claim_goes_too():
    assert run(["[FOCUSED] I'm turning off the lights for you. The room should go dark now."],
               tools_ran=False, acted=False) == "[FOCUSED] Nothing changed just now."
