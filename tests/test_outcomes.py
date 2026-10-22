import json

import pytest

from brain import brain_store, outcomes
from brain.obsidian_vault import Vault
from brain.tools import ToolFailure


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_OUTCOMES_PATH", str(tmp_path / "outcomes.jsonl"))
    vault = Vault(tmp_path / "vault")
    vault.init()
    monkeypatch.setattr(brain_store, "get_vault", lambda: vault)
    monkeypatch.setattr(outcomes, "_last", None)
    monkeypatch.setattr(outcomes, "_last_judge", 0.0)
    skill = vault.upsert("Skills", "Write a letter in Word", "Use word_write with a title.", source="mbuddy", strength=0.6)
    return vault, skill


def now(fn, *a):          # background work, run at once
    fn(*a)


def asker(log=None, **fixed):
    """A fake model: answers by the schema it is given."""
    def ask(system, user, schema, num_predict=160):
        if log is not None:
            log.append(user)
        props = set(schema["properties"])
        if "situation" in props:
            return fixed.get("lesson") or {"situation": "asked to write a document in Word", "do": "use the document tool word_write",
                                           "dont": "write a script that imports win32com", "because": "the script runner refuses COM imports"}
        if "reaction" in props:
            return fixed.get("reaction") or {"reaction": "unrelated", "issue": ""}
        return fixed.get("judge") or {"verdict": "unknown", "reason": ""}
    return ask


def strength_of(vault, path):
    meta, _b, _m = vault.read(path)
    return meta


def test_the_verdict_by_code():
    assert outcomes.classify("music_play", 'Playing "Song" by X in YouTube Music (web).')[0] == "pass"
    assert outcomes.classify("music_play", "NOT PLAYING: I opened it, but Windows doesn't show it playing")[0] == "fail"
    assert outcomes.classify("word_write", ToolFailure("FAILED: no document app"))[0] == "fail"
    assert outcomes.classify("music_play", "FAILED: NEEDS CHOICE: no music app chosen")[0] == "unknown"
    assert outcomes.classify("open_url", "Opened https://x.org in Chrome.")[0] == "executed"
    assert outcomes.classify("open_app", "Opened Notes (window appeared: no, not within 5 seconds).")[0] == "unknown"


def test_a_failure_writes_a_lesson_for_the_model_before_the_failing_note_is_demoted(world, monkeypatch):
    vault, skill = world
    order = []
    real_note_used = brain_store.note_used
    monkeypatch.setattr(brain_store, "note_used", lambda paths, ok: order.append(("demote", ok)) or real_note_used(paths, ok))
    real_write = outcomes.write_lesson
    monkeypatch.setattr(outcomes, "write_lesson", lambda *a, **k: order.append(("lesson",)) or real_write(*a, **k))
    outcomes.record_turn("write a letter to my landlord in Word", [("use_app", {"app": "Word"}, ToolFailure("FAILED: it imports win32com, which isn't allowed"))],
                         [skill], background=now, ask=asker())
    assert order == [("lesson",), ("demote", False)]
    lessons = [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]
    assert len(lessons) == 1
    meta, body, _ = vault.read(lessons[0])
    assert meta["audience"] == "model" and meta["tool"] == "use_app" and meta["lesson_fails"] == 1
    for key in ("WHEN:", "DO:", "DON'T:", "BECAUSE:", "EVIDENCE:"):
        assert key in body
    assert strength_of(vault, skill)["failures"] == 1


def test_the_same_mistake_revises_the_lesson_it_does_not_duplicate_it(world):
    vault, skill = world
    seen = []
    for _ in range(2):
        outcomes.record_turn("write a letter in Word", [("use_app", {}, ToolFailure("FAILED: refused"))], [skill], background=now, ask=asker(seen))
    lessons = [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]
    assert len(lessons) == 1
    assert vault.read(lessons[0])[0]["lesson_fails"] == 2
    assert "Earlier lesson:" in seen[1] and "Earlier lesson:" not in seen[0]       # the model saw the old text and revised it
    assert "2 times" in vault.read(lessons[0])[1]


def test_a_verified_success_credits_only_the_notes_in_the_prompt(world):
    vault, skill = world
    other = vault.upsert("Skills", "Something unrelated", "x", source="mbuddy", strength=0.5)
    music = vault.upsert("Skills", "Play jazz in YouTube Music", "search, then play", source="mbuddy", strength=0.5)
    outcomes.record_turn("play jazz", [("music_play", {"query": "jazz"}, 'Playing "A" by B in X.')], [music], background=now, ask=asker())
    assert strength_of(vault, music)["successes"] == 1 and strength_of(vault, other)["successes"] == 0


def test_an_unchecked_result_goes_to_the_self_judge_which_never_counts_as_verified(world):
    vault, skill = world
    outcomes.record_turn("open that page", [("open_url", {}, "Opened https://x.org in Chrome.")], [skill], background=now,
                         ask=asker(judge={"verdict": "fail", "reason": "it opened a different site than asked"}))
    meta = strength_of(vault, skill)
    assert meta["successes"] == 0 and meta["failures"] == 0                   # a judgement does not move the note's numbers
    assert [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]  # but it does write a lesson
    outcomes._last_judge = 0.0
    outcomes.record_turn("open that page", [("open_url", {}, "Opened https://x.org in Chrome.")], [], background=now, ask=asker(judge={"verdict": "unknown", "reason": ""}))
    assert len([p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]) == 1


def test_the_users_correction_outweighs_a_passing_check(world):
    vault, _word = world
    skill = vault.upsert("Skills", "Play calm music", "pick calm tracks", source="mbuddy", strength=0.6)
    outcomes.record_turn("play something calm", [("music_play", {}, 'Playing "Loud Song" by X in Y.')], [skill], background=now, ask=asker())
    assert strength_of(vault, skill)["successes"] == 1
    outcomes.on_user_message("no, I said calm, that is loud", background=now,
                             ask=asker(reaction={"reaction": "correction", "issue": "picked a loud song when calm was asked"},
                                   lesson={"situation": "asked for calm music", "do": "choose calm tracks", "dont": "pick a loud song", "because": "the user asked for calm"}))
    meta = strength_of(vault, skill)
    assert meta["failures"] == 1
    lesson = [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND][0]
    assert "DON'T: pick a loud song" in vault.read(lesson)[1]
    assert vault.read(lesson)[0]["last_source"] == "user correction"
    log = outcomes.recent()
    assert any(r.get("reaction") == "correction" for r in log)


def test_thanks_and_unrelated_messages(world):
    vault, _word = world
    skill = vault.upsert("Skills", "Open Notes from the start menu", "open_app", source="mbuddy", strength=0.6)
    outcomes.record_turn("open Notes", [("open_app", {}, "Opened Notes (window appeared: yes).")], [skill], background=now, ask=asker())
    base = strength_of(vault, skill)["successes"]
    outcomes.on_user_message("perfect, thanks", background=now, ask=asker(reaction={"reaction": "thanks", "issue": ""}))
    assert strength_of(vault, skill)["successes"] == base + 1
    outcomes.record_turn("open Notes", [("open_app", {}, "Opened Notes (window appeared: yes).")], [], background=now, ask=asker())
    outcomes.on_user_message("what's the weather", background=now, ask=asker(reaction={"reaction": "unrelated", "issue": ""}))
    assert not [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]


def test_the_lesson_reaches_the_next_similar_request_and_is_written_for_the_model(world):
    vault, skill = world
    outcomes.record_turn("write a letter in Word", [("use_app", {}, ToolFailure("FAILED: refused"))], [], background=now, ask=asker())
    picked = vault.retrieve("use_app write a document in Word", token_budget=300, max_notes=3)
    assert any(p["kind"] == outcomes.LESSON_KIND and "word_write" in p["line"] for p in picked)
    assert "audience: model" in (vault.base / "Debugging experience").glob("*.md").__next__().read_text(encoding="utf-8")


def test_reads_are_not_judged_and_the_summary_counts_real_outcomes(world):
    outcomes.record_turn("what is playing", [("music_now_playing", {}, "nothing")], [], background=now, ask=asker())
    assert outcomes.recent() == []
    outcomes.record_turn("play jazz", [("music_play", {}, 'Playing "A" by B in X.')], [], background=now, ask=asker())
    outcomes.record_turn("play jazz", [("music_play", {}, "NOT PLAYING: no")], [], background=now, ask=asker())
    s = outcomes.summary()
    assert s["turns"] == 2 and s["passed"] == 1 and s["failed"] == 1


def test_a_redo_that_names_what_was_wrong_is_a_correction(world):
    vault, skill = world
    outcomes.record_turn("set a timer for 2 minutes", [("set_timer", {}, "Timer started: 120s timer for 2 minutes")], [skill], background=now, ask=asker())
    outcomes.on_user_message("no, that was too long, make it 30 seconds", background=now,
                             ask=asker(reaction={"reaction": "retry", "issue": "the timer was too long"}))
    assert any(r.get("reaction") == "correction" for r in outcomes.recent())
    assert [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]


def test_a_background_app_job_gives_the_real_verdict_and_a_failed_one_writes_a_lesson(world):
    from types import SimpleNamespace
    vault, _word = world
    skill = vault.upsert("Skills", "FreeCAD - design a table", "steps", source="mbuddy", strength=0.6)
    outcomes.record_turn("open FreeCAD and design a table", [("use_app", {"app": "FreeCAD", "task": "table"}, "I know how to use FreeCAD. Doing it now.")],
                         [skill], background=now, ask=asker())
    job = SimpleNamespace(kind="app", subject="FreeCAD", ok=False, outcome="gave up after 6 tries: script raised TypeName error", last="")
    outcomes.on_job("failed", job, background=now, ask=asker())
    assert any(r.get("job") == "fail" for r in outcomes.recent())
    assert strength_of(vault, skill)["failures"] == 1
    assert [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]
    outcomes.on_job("failed", job, background=now, ask=asker())          # the job is matched once
    assert strength_of(vault, skill)["failures"] == 1


def test_the_lesson_for_a_failed_job_carries_the_real_error_the_driver_saw(world):
    import time
    from types import SimpleNamespace
    vault, skill = world
    vault.upsert(outcomes.LESSON_KIND, "FreeCAD - script error", "## Symptom\nran it\nException: 'PrimitivePy' object has no attribute 'TypeName'\n\n## Diagnosis\nx", source="self")
    seen = []
    outcomes.record_turn("design a table in FreeCAD", [("use_app", {"app": "FreeCAD"}, "Doing it now.")], [skill], background=now, ask=asker(seen))
    outcomes.on_job("failed", SimpleNamespace(kind="app", subject="FreeCAD", ok=False, outcome="gave up", last="", started=time.time() - 60), background=now, ask=asker(seen))
    assert any("no attribute 'TypeName'" in text for text in seen)


def test_a_launch_sentence_is_not_judged_the_job_ending_is(world):
    vault, skill = world
    seen = []
    outcomes.record_turn("design a table in FreeCAD", [("use_app", {"app": "FreeCAD"}, "Doing it now.")], [skill], background=now,
                         ask=asker(seen, judge={"verdict": "fail", "reason": "no criteria"}))
    assert seen == [] and not [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]


def test_lessons_for_gives_the_models_own_lessons_to_a_prompt(world):
    vault, skill = world
    outcomes.record_turn("design a table in FreeCAD", [("use_app", {"app": "FreeCAD"}, ToolFailure("FAILED: x"))], [], background=now,
                         ask=asker(lesson={"situation": "scripting FreeCAD tables", "do": "use Part.makeBox", "dont": "use TypeName", "because": "it has no TypeName"}))
    text = outcomes.lessons_for("FreeCAD design a table", "use_app")
    assert "use Part.makeBox" in text and "EVIDENCE" not in text
    assert outcomes.lessons_for("bake bread", "use_app") == ""


# ── the field test (2026-10-19): no lesson was ever written, because the model answered in plain lines ──
def test_a_lesson_answered_in_plain_lines_is_read():
    reply = ("situation: User requests to play music while another track is playing\n"
             "do: Check what is playing before starting a new search\n"
             "don't: Open a second search over the music that is already playing\n"
             "because: The user lost the song they were listening to")
    out = outcomes._loose(reply, outcomes.LESSON_SCHEMA)
    assert out == {"situation": "User requests to play music while another track is playing",
                   "do": "Check what is playing before starting a new search",
                   "dont": "Open a second search over the music that is already playing",
                   "because": "The user lost the song they were listening to"}


def test_a_lesson_in_markdown_or_a_code_fence_is_read():
    md = "**When:** asked for a one-off reminder\n**Do:** set it once\n**Do not:** make it repeat daily\n**Because:** the user said once"
    out = outcomes._loose(md, outcomes.LESSON_SCHEMA)
    assert out["situation"] == "asked for a one-off reminder" and out["dont"] == "make it repeat daily" and out["do"] == "set it once"
    fenced = '```json\n{"situation": "a", "do": "b", "dont": "c", "because": "d"}\n```'
    assert outcomes._loose(fenced, outcomes.LESSON_SCHEMA) == {"situation": "a", "do": "b", "dont": "c", "because": "d"}


def test_a_lesson_missing_a_field_is_not_made_up():
    assert outcomes._loose("situation: x\ndo: y\nbecause: z", outcomes.LESSON_SCHEMA) is None      # no "don't" line
    assert outcomes._loose("I am not sure what went wrong.", outcomes.LESSON_SCHEMA) is None


def test_the_judge_still_reads_plain_lines():
    assert outcomes._loose("fail\nReason: the song was loud", outcomes.JUDGE_SCHEMA) == {"verdict": "fail", "reason": "the song was loud"}


def test_a_correction_without_a_lesson_demotes_nothing(world):
    vault, _word = world
    skill = vault.upsert("Skills", "Play calm music", "pick calm tracks", source="mbuddy", strength=0.6)
    outcomes.record_turn("play something calm", [("music_play", {}, 'Playing "Loud Song" by X in Y.')], [skill], background=now, ask=asker())
    before = strength_of(vault, skill)["strength"]

    def ask(system, user, schema, num_predict=160):        # the reaction is read, the lesson can't be
        if "reaction" in schema["properties"]:
            return {"reaction": "correction", "issue": "wrong song"}
        return None
    outcomes.on_user_message("that's wrong", background=now, ask=ask)
    meta = strength_of(vault, skill)
    assert meta["strength"] == before and int(meta.get("failures", 0) or 0) == 0


def test_only_related_notes_are_credited_or_blamed(world, tmp_path):
    vault, skill = world                                     # "Write a letter in Word"
    blender = vault.upsert("Skills", "Blender 4.3 - design a fish", "steps", source="mbuddy", strength=0.5)
    cook = vault.upsert("Knowledge", "what should I cook tonight", "pasta", source="mbuddy", strength=0.6)
    # a web search about the World Cup: none of these notes bear on it
    outcomes.record_turn("who won the world cup", [("music_play", {}, 'Playing "Anthem" by X in Y.')], [blender, cook], background=now, ask=asker())
    assert strength_of(vault, blender)["strength"] == 0.5 and strength_of(vault, cook)["strength"] == 0.6
    # writing in Word: the Word skill is credited, the fish is not
    outcomes.record_turn("write a letter to Sam in Word", [("word_write", {}, 'Wrote 120 words into "Document1".')], [skill, blender], background=now, ask=asker())
    assert strength_of(vault, skill)["successes"] == 1
    assert int(strength_of(vault, blender).get("successes", 0) or 0) == 0


@pytest.mark.parametrize("said", ["no, make that every day", "actually just once, at 7pm", "make it bigger", "also add a tree",
                                  "are you sure?", "did you do it?"])
def test_a_change_of_mind_or_a_question_writes_no_lesson_and_lowers_nothing(world, said):
    vault, _w = world
    skill = vault.upsert("Skills", "Set a reminder at a time", "add_reminder", source="mbuddy", strength=0.6)
    outcomes.record_turn("remind me at 6pm to water the plants", [("add_reminder", {}, "Reminder set: water the plants on Tue 20 Oct 2026 at 18:00.")],
                         [skill], background=now, ask=asker())
    before = strength_of(vault, skill)
    outcomes.on_user_message(said, background=now, ask=asker(reaction={"reaction": "correction", "issue": "wrong repeat"}))
    after = strength_of(vault, skill)
    assert after["strength"] == before["strength"] and not [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]


def test_saying_it_was_wrong_is_still_a_correction(world):
    vault, _w = world
    skill = vault.upsert("Skills", "Set a reminder at a time", "add_reminder", source="mbuddy", strength=0.6)
    outcomes.record_turn("remind me at 6pm", [("add_reminder", {}, "Reminder set: x on Tue 20 Oct 2026 at 08:00.")], [skill], background=now, ask=asker())
    outcomes.on_user_message("that's wrong, I said 6pm not 8am", background=now,
                             ask=asker(reaction={"reaction": "correction", "issue": "set 8am instead of 6pm"}))
    assert [p for p in vault.notes() if p.parent.name == outcomes.LESSON_KIND]
