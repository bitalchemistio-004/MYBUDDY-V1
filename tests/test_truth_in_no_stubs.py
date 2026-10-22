"""The model is told what is true, and a guard never leaves a stub (W1).

Live: "are you working on it?" while a job in an app was on its second try.
The model wrote "Yes, I'm working on it" (true); the guard dropped it as an
unsupported claim and the user heard "Nothing changed just now." Then the
model said it hadn't opened the app. And asked to say something to a friend,
it said it had no voice. No model runs here."""
import asyncio

import pytest

import brain.llm_router as router
from brain import self_state
from core import jobs, ui_settings


@pytest.fixture(autouse=True)
def clean():
    jobs.reset()
    yield
    jobs.reset()


async def stream(text):
    yield text


def guarded(text, **facts):
    base = dict(tools_ran=False, memory_text="", acted=False)
    base.update(facts)

    async def run():
        return "".join([piece async for piece in router.truth_guard(stream(text), **base)])
    return asyncio.run(run())


def test_the_status_block_says_what_runs_how_far_and_how_it_ended():
    assert "nothing is running" in jobs.status_text()
    job = jobs.start("app", "Sketcher 2", "a small house", total=6)
    jobs.step(job, 2, doing="writing the script", last="it failed: NameError")
    text = jobs.status_text()
    assert "RUNNING NOW" in text and "Sketcher 2" in text and "try 2 of 6" in text and "NameError" in text
    assert jobs.plain_status() == "I'm working in Sketcher 2 right now, on try 2."
    jobs.finish(job, False, "the roof was missing")
    text = jobs.status_text()
    assert "NOT RUNNING ANY MORE" in text and "STOPPED without success (the roof was missing)" in text
    assert "Nothing is running right now" in text and "working in" not in text   # an ended job never reads as ongoing


def test_a_job_bound_to_its_thread_ends_once_with_its_outcome():
    jobs.begin("learn", "origami", "learn origami")
    jobs.step_current(doing="reading about it online")
    jobs.finish_current(True, "I learned about origami.")
    jobs.finish_current(False, "it stopped unexpectedly")      # the finally: nothing to end
    snap = jobs.snapshot()
    assert len(snap) == 1 and snap[0]["ok"] is True and snap[0]["outcome"] == "I learned about origami."


def test_saying_it_is_working_is_kept_while_a_job_runs_and_dropped_when_none_does():
    draft = "[NORMAL] Yes, I'm working on it."
    assert "Nothing changed" in guarded(draft)                       # no job: the old stub (no regenerate hook here)
    jobs.start("app", "Sketcher 2", "a small house")
    assert guarded(draft, background=jobs.snapshot()).strip() == "[NORMAL] Yes, I'm working on it."


def test_a_claim_naming_the_app_it_worked_in_is_kept_after_the_job_too():
    job = jobs.start("app", "Sketcher 2", "a small house")
    jobs.finish(job, True, "made it")
    out = guarded("[NORMAL] I opened Sketcher earlier and made the house.", background=jobs.snapshot())
    assert "I opened Sketcher earlier" in out


def test_a_running_job_doesnt_back_a_claim_about_something_a_tool_does():
    jobs.start("app", "Sketcher 2", "a small house")
    out = guarded("[NORMAL] I'm playing some jazz for you now.", background=jobs.snapshot())
    assert "jazz" not in out


def test_nothing_left_is_written_again_from_the_facts_never_a_stub():
    asked = []

    async def regenerate(kind, dropped):
        asked.append((kind, dropped))
        yield "[NORMAL] I haven't started that, but I can open the app for you."
    out = guarded("[NORMAL] I'll set that up right away.", on_empty=regenerate)
    assert out == "[NORMAL] I haven't started that, but I can open the app for you."
    assert asked == [("action", ["I'll set that up right away."])]
    assert "Nothing changed" not in out


def test_it_knows_it_has_a_voice_from_the_real_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    ui_settings.reset_cache()
    monkeypatch.setattr(self_state, "speakers_present", lambda: True)
    assert "You have a voice" in self_state.self_card() and "spoken out loud" in self_state.self_card()
    monkeypatch.setattr(self_state, "speakers_present", lambda: False)
    assert "no active speakers" in self_state.self_card()
    ui_settings.update({"speak_replies": False})
    assert "voice is switched off" in self_state.self_card()
    ui_settings.reset_cache()


def test_an_ended_job_doesnt_back_a_claim_of_still_working():
    # Live: the job had finished; "NOT RUNNING" used to read "ENDED: working in
    # <app>", and the reply said "I'm still in <app>, working on that chair".
    job = jobs.start("app", "Sketcher 2", "a chair")
    jobs.finish(job, True, "I made it after 2 tries")
    out = guarded("[NORMAL] I'm still in Sketcher 2, working on that chair.", background=jobs.snapshot())
    assert "still" not in out
    kept = guarded("[NORMAL] I finished the chair in Sketcher 2 after two tries.", background=jobs.snapshot())
    assert "finished the chair in Sketcher 2" in kept


def test_a_job_with_no_progress_is_said_so_then_ended_as_stalled(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(jobs.time, "time", lambda: clock["t"])
    events = []
    jobs.on_change(lambda event, job: events.append((event, job.outcome)))
    job = jobs.start("app", "Sketcher 2", "a chair", total=6)
    jobs.step(job, 2, doing="writing the script")
    clock["t"] += jobs.QUIET_S + 30
    assert "NO PROGRESS" in jobs.status_text() and "RUNNING NOW" not in jobs.status_text()
    assert "no progress" in jobs.plain_status()
    assert jobs.check_stalled() == []                       # quiet, not yet stalled
    clock["t"] += jobs.STALL_S
    ended = jobs.check_stalled()
    assert [j.id for j in ended] == [job] and ended[0].outcome.startswith("stalled: no progress for")
    assert "while writing the script" in ended[0].outcome and events[-1][0] == "stalled"
    assert not jobs.running() and "STOPPED without success (stalled" in jobs.status_text()
    # one owner (F52): the stalled job was cancelled and told; a late ending is not a second story
    jobs.finish(job, True, "it came through after all")
    assert jobs.snapshot()[0]["ok"] is False and jobs.snapshot()[0]["outcome"].startswith("stalled")
