"""Connected apps, driven from what MBUDDY learned (S103, S106, S107). Nothing
is about a particular app: an invented app "Sketcher" stands in. No real app,
model or web is used; settings and the vault are temporary."""
import json

import pytest

from brain import brain_store as bs
from brain.connectors import app_driver as ad
from core import ui_settings

DRIVER = {"language": "Python", "file_ext": ".py", "modules": ["sketch"],
          "test": ["{exe}", "--headless", "--run", "{file}"], "show": ["{exe}", "--run", "{file}"]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "cfg" / "app_settings.json"))
    ui_settings.reset_cache()
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(bs, "vault_root", lambda: root)
    monkeypatch.setattr(bs, "_vault", None)
    monkeypatch.setattr(bs, "_vault_root", None)
    exe = tmp_path / "Sketcher" / "sketcher.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    ui_settings.update({"connected_apps": [{"name": "Sketcher 2", "path": str(exe), "added": "now"}]})
    said = []
    monkeypatch.setattr(bs, "_announce", lambda speech, topic, ok: said.append((speech, ok)))
    monkeypatch.setattr(bs, "_wait_until_idle", lambda *a: None)
    # No model here: a fixed plan, and a review that finds nothing unless a test says otherwise.
    monkeypatch.setattr(ad, "plan_result", lambda *a: "- a box, 1 m, on the ground")
    monkeypatch.setattr(ad, "review_result", lambda *a: "")
    monkeypatch.setattr(ad, "inspector", lambda name: "print('MBUDDY_STATE: part')")  # a learned inspection
    monkeypatch.setattr(ad, "learn_inspector", lambda *a: "")
    # No web and no model: the job used to reach both (learning scripting notes
    # searched the real web). Every way out is closed; a test that needs one
    # opens it itself.
    def no_network(*a, **k):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr(bs.requests, "get", no_network)
    monkeypatch.setattr(bs.requests, "post", no_network)
    monkeypatch.setattr(bs, "_search_raw", lambda q: [])
    monkeypatch.setattr(ad, "find_examples", lambda *a: [])
    ad._busy.clear()
    yield exe, said
    ui_settings.reset_cache()


def test_a_learned_driver_is_checked_before_anything_runs():
    assert ad.validate_driver(DRIVER)["test"] == ["{exe}", "--headless", "--run", "{file}"]
    assert ad.validate_driver({**DRIVER, "test": ["{exe}", "--run", "{file}", "&", "del", "x"]}) is None  # shell chars
    # Whatever program the model names, only the connected app's own program runs.
    assert ad.validate_driver({**DRIVER, "test": ["cmd.exe", "--run", "{file}"]})["test"][0] == "{exe}"
    assert ad.validate_driver({**DRIVER, "show": ["{exe}", "--gui"]}) is None                           # no script
    assert ad.validate_driver({"language": None}) is None


def test_the_models_usual_answer_is_normalised():
    # As the model really answers: the program's name and an example file, no dot.
    d = ad.validate_driver({"language": "python", "file_ext": "py", "modules": ["sketcher", "sys"],
                            "test": ["sketcher", "--headless", "--run", "myscript.py"],
                            "show": ["sketcher", "--run", "myscript.py"]})
    assert d["file_ext"] == ".py"
    assert d["test"] == ["{exe}", "--headless", "--run", "{file}"] and d["show"] == ["{exe}", "--run", "{file}"]


def test_a_launcher_shim_is_replaced_by_the_program_beside_it(tmp_path):
    launcher = tmp_path / "paint-launcher.exe"
    real = tmp_path / "paint.exe"
    launcher.write_bytes(b"")
    real.write_bytes(b"")
    assert ad.program(str(launcher)) == str(real)
    assert ad.program(str(real)) == str(real)


def test_code_is_taken_from_a_reply_with_or_without_a_fence():
    assert ad.code_from("Here you go:\n```python\nimport sketch\nsketch.box()\n```") == "import sketch\nsketch.box()"
    assert ad.code_from("Sure! Here's the script:\nimport sketch\nsketch.box()") == "import sketch\nsketch.box()"


def test_python_is_checked_against_the_learned_modules():
    assert ad.unsafe_python("import sketch\nimport math\nsketch.box(math.pi)", {"sketch"}) == ""
    assert "os" in ad.unsafe_python("import os\nos.remove('x')", {"sketch"})
    assert "open" in ad.unsafe_python("open('x', 'w')", {"sketch"})
    assert "save" in ad.unsafe_python("import sketch\nsketch.ops.save_as('x')", {"sketch"})


def test_an_app_that_isnt_connected_is_refused(world):
    out = ad.use_app({"app": "Photo Tool", "task": "crop"})
    assert str(out).startswith("FAILED:") and "isn't connected" in out and "Sketcher 2" in out


def test_a_new_app_is_learned_first_and_said_so(world, monkeypatch):
    started = []
    monkeypatch.setattr(ad.threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda s: started.append(args)})())
    out = ad.use_app({"app": "Sketcher", "task": "a small house"})
    assert out.startswith("That's new to me.") and "Sketcher 2" in out and started


def _learn_into_vault(name):
    bs.get_vault().upsert("Apps", name, "## What it is\nA drawing app.\n\n" + ad.DRIVER_HEADING
                          + "\n```json\n" + json.dumps(DRIVER) + "\n```", source="web",
                          tags=["mbuddy/app", f"mbuddy/app/{ad.slug(name)}"])
    return "", DRIVER


def test_the_learning_curve_fails_then_fixes_then_shows_and_keeps_the_skill(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    drafts = iter(["import sketch\nsketch.box(", "import sketch\nsketch.box()"])
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: next(drafts))
    monkeypatch.setattr(ad, "_test", lambda name, driver, exe, code, inspect="": (True, "", "") if "box()" in code
                        else (False, "Traceback\nSyntaxError: '(' was never closed", "script"))
    monkeypatch.setattr(ad, "diagnose", lambda *a: "What went wrong: a bracket is open.\nWhy: typo.\nFix: close it.")
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a small house")
    skill = bs.get_vault().base / "Skills" / "Sketcher 2 - a small house.md"
    text = skill.read_text(encoding="utf-8")
    assert "Try 1: failed" in text and "Try 2: worked, and matched the plan." in text and "sketch.box()" in text
    assert said and said[-1][1] and "after 2 tries" in said[-1][0] and "in front of you" in said[-1][0]


def test_an_app_without_a_script_interface_is_said_honestly(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", lambda name: ("## What it is\nA game.", None))
    ad._job("Sketcher 2", str(exe), "a small house")
    assert said and not said[-1][1] and "didn't find a way to drive it" in said[-1][0]


def test_the_next_request_starts_from_the_skill(world, monkeypatch):
    exe, said = world
    _learn_into_vault("Sketcher 2")
    bs.get_vault().upsert("Skills", "Sketcher 2 - a small house", "## Script\n```\nimport sketch\nsketch.house()\n```",
                          source="self", tags=["mbuddy/skill", "mbuddy/app/sketcher-2"])
    seen = {}
    monkeypatch.setattr(ad, "_compose", lambda name, driver, knowledge, task, example, *rest:
                        seen.setdefault("example", example) or "import sketch\nsketch.house()")
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a bigger house")
    assert "sketch.house()" in seen["example"] and "on the first try" in said[-1][0]


def test_a_fixed_problem_is_kept_as_debugging_experience(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    # Valid Python that fails when the app runs it (a syntax error is refused before running).
    drafts = iter(["import sketch\nsketch.boxx()", "import sketch\nsketch.box()"])
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: next(drafts))
    monkeypatch.setattr(ad, "_test", lambda name, driver, exe, code, inspect="": (True, "", "") if "box()" in code
                        else (False, "Traceback\nAttributeError: module 'sketch' has no attribute 'boxx'", "script"))
    monkeypatch.setattr(ad, "diagnose", lambda *a: "What went wrong: no boxx.\nWhy: the name is box.\nFix: call box().")
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a small house")
    notes = list((bs.get_vault().base / ad.DEBUG_KIND).glob("*.md"))
    assert len(notes) == 1
    text = notes[0].read_text(encoding="utf-8")
    assert "has no attribute 'boxx'" in text and "Fix: call box()." in text and "The fix worked." in text


def test_a_wrong_command_is_corrected_and_kept(world, monkeypatch):
    # The app never ran the script: the way of driving it is fixed from the
    # app's own output, and the corrected driver is saved in the app note.
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: "import sketch\nsketch.box()")
    good = ["{exe}", "--headless", "--script", "{file}"]
    monkeypatch.setattr(ad, "_test", lambda name, driver, exe, code, inspect="": (True, "", "") if "--script" in driver["test"]
                        else (False, "Error: unknown option --run", "command"))
    monkeypatch.setattr(ad, "diagnose", lambda *a: "What went wrong: wrong option.\nWhy: it is --script.\nFix: use --script.")
    monkeypatch.setattr(ad, "_fix_command", lambda name, driver, knowledge, error, diagnosis="":
                        ad.validate_driver({**driver, "test": good, "show": ["{exe}", "--script", "{file}"]}))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a small house")
    _knowledge, driver, _ = ad.learned("Sketcher 2")
    assert driver["test"] == good and "on the first try" in said[-1][0]
    assert any("unknown option" in n.read_text(encoding="utf-8")
               for n in (bs.get_vault().base / ad.DEBUG_KIND).glob("*.md"))


def test_the_error_signature_ignores_paths_and_numbers():
    a = ad.signature('Traceback\n  File "C:\\Temp\\x_123.py", line 4\nNameError: name \'thing\' is not defined')
    b = ad.signature('Traceback\n  File "C:\\Temp\\x_987.py", line 9\nNameError: name \'thing\' is not defined')
    assert a == b and "NameError" in a


def test_the_failing_line_is_found_in_the_traceback():
    error = ('Traceback (most recent call last):\n  File "C:/Temp/mbuddy/x/test_1790605.py", line 16, in <module>\n'
             '    car.name = "Car"\n    ^^^^^^^^\nAttributeError: \'set\' object has no attribute \'name\'')
    assert ad.failing_line(error) == 'car.name = "Car"'


def test_a_rewrite_that_keeps_the_failing_line_is_sent_back_before_running(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    drafts = iter(["import sketch\nsketch.boxx()",
                   "import sketch\nsketch.boxx()  # boxx is wrong",   # kept the failing line: not run
                   "import sketch\nsketch.box()"])
    ran = []
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: next(drafts))

    def test(name, driver, exe, code, inspect=""):
        ran.append(code)
        return (True, "", "") if "box()" in code else (False, 'Traceback\n  File "C:/T/test_1.py", line 2, in <module>\n'
                                                             '    sketch.boxx()\nAttributeError: no boxx', "script")
    monkeypatch.setattr(ad, "_test", test)
    monkeypatch.setattr(ad, "diagnose", lambda *a: "What went wrong: no boxx.\nWhy: typo.\nFix: call box().")
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a small house")
    assert len(ran) == 2 and "# boxx is wrong" not in "".join(ran)
    assert "after 2 tries" in said[-1][0]


def test_failed_fixes_stay_visible_as_experience(world):
    v = bs.get_vault()
    path = v.upsert(ad.DEBUG_KIND, "Sketcher 2 - AttributeError no boxx", "## Symptom\nAttributeError: no boxx\n"
                    "## Diagnosis\nFix: rename it.\n## Result\nThe fix didn't work yet.", source="self",
                    tags=["mbuddy/debugging", "mbuddy/app/sketcher-2"])
    bs.note_used([path], success=False)
    bs.note_used([path], success=False)  # now below what recall shows
    past = ad.past_debugging("Sketcher 2", "Traceback\nAttributeError: no boxx")
    assert "rename it" in past and "did NOT work" in past


def test_scripting_notes_are_learned_into_the_app_note_and_used_first(world, monkeypatch):
    _learn_into_vault("Sketcher 2")
    monkeypatch.setattr(bs, "_search_raw", lambda q: [{"title": "API", "href": "https://example.com/api", "body": "b"}])
    monkeypatch.setattr(bs, "_read_page", lambda url: "Scripts use sketch.box(size) to add a box.")

    class R:
        def json(self):
            return {"message": {"content": "- sketch.box(size) adds a box\n- sketch.move(obj, x, y) moves it"}}
    monkeypatch.setattr(bs.requests, "post", lambda *a, **k: R())
    notes = bs.learn_scripting_now("Sketcher 2", "Python")
    assert "sketch.box" in notes
    knowledge, driver, _ = ad.learned("Sketcher 2")
    assert knowledge.startswith("Scripting notes:") and "sketch.move" in knowledge and driver is not None


def test_running_without_errors_isnt_success_until_the_review_passes(world, monkeypatch):
    # S110: the script ran, but what it made didn't match the plan; the review's
    # problems drive the next try, and what they taught is kept as lessons.
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    drafts = iter(["import sketch\nsketch.box(upright=True)", "import sketch\nsketch.box(upright=False)"])
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: next(drafts))
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    reviews = iter(["- the wheels stand upright; they should lie on their side", ""])
    monkeypatch.setattr(ad, "review_result", lambda *a: next(reviews))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a small house")
    text = (bs.get_vault().base / "Skills" / "Sketcher 2 - a small house.md").read_text(encoding="utf-8")
    assert "## Lessons" in text and "lie on their side" in text and "upright=False" in text
    assert "ran, but it wasn't what was asked yet" in text
    assert said[-1][1] and "checked it against my plan" in said[-1][0]


def test_a_result_that_never_matches_is_said_honestly(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: "import sketch\nsketch.box()")
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    monkeypatch.setattr(ad, "review_result", lambda *a: "- it's a box, not a house")
    shown = []
    monkeypatch.setattr(ad, "_show", lambda *a: shown.append(1) or True)
    monkeypatch.setattr(ad, "MAX_TRIES", 2)
    ad._job("Sketcher 2", str(exe), "a small house")
    assert not said[-1][1] and "isn't right yet" in said[-1][0] and "not a house" in said[-1][0] and shown
    text = (bs.get_vault().base / "Skills" / "Sketcher 2 - a small house.md").read_text(encoding="utf-8")
    assert "## Still wrong" in text and "mbuddy/unfinished" in text


def test_only_the_apps_own_report_counts():
    # The review reads what the learned inspection printed, not the script's own claims.
    assert "made nothing" in ad.review_result("Sketcher 2", "a house", "- walls", "")
    out = 'noise\nprint("RESULT: Wall")\nMBUDDY_STATE: Wall mesh at (0,0,0) size (4,0.2,2) rot (0,0,0)\nmore'
    assert ad._report(out) == "MBUDDY_STATE: Wall mesh at (0,0,0) size (4,0.2,2) rot (0,0,0)"


def test_without_a_way_to_see_the_result_it_says_so(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    monkeypatch.setattr(ad, "inspector", lambda name: "")
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: "import sketch\nsketch.box()")
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a small house")
    assert "couldn't check how it looks" in said[-1][0]


def test_coverage_is_checked_in_code_before_any_model_judges():
    plan = ("- Chassis: a box, 4m long, grey\n- Wheels: four cylinders at the corners, lying on their side\n"
            "- Roof: a smaller box on top")
    cube = "MBUDDY_STATE: Cube (MESH) at (0,0,0)\nMBUDDY_STATE: Plane (MESH) at (0,0,0)"
    assert "chassis" in ad.coverage(plan, cube) and "wheels (4)" in ad.coverage(plan, cube)
    three_wheels = "MBUDDY_STATE: Chassis x\n" + "\n".join(f"MBUDDY_STATE: Wheel{i} x" for i in range(3)) + "\nMBUDDY_STATE: Roof x"
    assert ad.coverage(plan, three_wheels) == "- The plan's wheels (4) isn't in the scene: found 3."
    car = "MBUDDY_STATE: Chassis x\n" + "\n".join(f"MBUDDY_STATE: Wheel{i} x" for i in range(4)) + "\nMBUDDY_STATE: Roof x"
    assert ad.coverage(plan, car) == ""


def test_leftovers_the_plan_doesnt_have_are_flagged_but_cameras_arent():
    plan = "- Chassis: a box\n- Roof: a smaller box"
    report = ("MBUDDY_STATE: Camera CAMERA 1,1,1\nMBUDDY_STATE: Light LIGHT 1,1,1\nMBUDDY_STATE: Cube MESH at (0,0,0)\n"
              "MBUDDY_STATE: chassis MESH at (0,0,0)\nMBUDDY_STATE: roof MESH at (0,0,1)")
    assert ad.coverage(plan, report) == "- The scene has uncleaned default primitives: cube. Remove or transform them."


def test_examples_found_online_are_cleaned_and_the_closest_comes_first(monkeypatch):
    # S111: looked up like a developer would; only code for the app's own
    # modules, without the lines MBUDDY would refuse, comments or docstrings.
    monkeypatch.setattr(ad, "_ask", lambda *a: "sketch python house script")
    monkeypatch.setattr(bs, "_search_raw", lambda q: [{"href": "https://a.example/one"}, {"href": "https://b.example/two"}])
    house = ('"""Builds a house."""\nimport sketch\n# walls first\ndef wall(x):\n    sketch.box(x)\n'
             'for x in range(4):\n    wall(x)\nsketch.roof()\nsketch.ops.save_as("house.skt")\n')
    circles = "import sketch\nsketch.circle()\nsketch.circle()\nsketch.circle()\n"
    unrelated = "import numpy\nnumpy.zeros(3)\nnumpy.ones(3)\nnumpy.eye(3)\n"
    pages = {"https://a.example/one": [circles, unrelated], "https://b.example/two": [house]}
    monkeypatch.setattr(bs, "read_code_blocks", lambda url: pages[url])
    found = ad.find_examples("Sketcher 2", DRIVER, "a small house", "- walls: four boxes\n- roof: on top")
    assert [link for link, _ in found] == ["https://b.example/two", "https://a.example/one"]
    best = found[0][1]
    assert "sketch.roof()" in best and "for x in range(4)" in best
    assert "save_as" not in best and "walls first" not in best and "Builds" not in best
    assert ad.unsafe_python(best) == ""


def test_the_script_prompt_always_fits_the_models_window(world, monkeypatch):
    # ModelCapabilities and PriorityContextBuilder dynamically assemble
    # context based on model context window (8192 fallback) and priority rules.
    sent = []

    class R:
        def json(self):
            return {"message": {"content": "import sketch\nsketch.box()"}, "done_reason": "stop"}
    monkeypatch.setattr(ad.requests, "post", lambda url, json, timeout: sent.append(json) or R())
    args = dict(name="Sketcher 2", driver=DRIVER, knowledge="Scripting notes:\n" + "- sketch.box(size) adds a box\n" * 200,
                task="a small house", example="", last="x = 1\n" * 600, error="Traceback\n" + "E" * 3000,
                diagnosis="D" * 2000, failing="x = 1", research="R" * 5000, plan="- a wall\n" * 400,
                found=[("https://a.example", "import sketch\n" + "sketch.box()\n" * 400)])
    ad._compose(**args)
    ad._compose(**{**args, "last": "", "error": "", "diagnosis": "", "failing": "", "research": ""})
    ad._compose(**{**args, "found": ()})
    chat_sent = [s for s in sent if "messages" in s]
    for body in chat_sent:
        prompt = "".join(m["content"] for m in body["messages"])
        assert ad._tokens(prompt) + body["options"]["num_predict"] <= 9000
        assert body["options"]["num_predict"] >= 1024
    fixing, first, fixing_without_examples = chat_sent
    assert "Task to accomplish in Sketcher 2: a small house" in fixing["messages"][1]["content"]
    assert "Fix it and reply with the whole corrected script." in fixing["messages"][1]["content"]
    assert "Working example (https://a.example):" in first["messages"][1]["content"]
    assert "Working example" not in fixing_without_examples["messages"][1]["content"]


def test_the_skill_note_keeps_the_examples_it_learned_from(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    monkeypatch.setattr(ad, "find_examples", lambda *a: [("https://b.example/two", "import sketch\nsketch.roof()")])
    given = []

    def compose(*a, **k):
        given.append(a[11])
        return "import sketch\nsketch.box()"
    monkeypatch.setattr(ad, "_compose", compose)
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a small house")
    assert given and given[0][0][0] == "https://b.example/two"
    text = (bs.get_vault().base / "Skills" / "Sketcher 2 - a small house.md").read_text(encoding="utf-8")
    assert "## Examples it learned from" in text and "https://b.example/two" in text


def test_only_what_helps_write_a_script_is_given_to_the_model(world):
    v = bs.get_vault()
    tags = ["mbuddy/app", "mbuddy/app/sketcher-2"]
    driver_part = ad.DRIVER_HEADING + "\n```json\n" + json.dumps(DRIVER) + "\n```\n\n## Sources\n- https://example.com/src"
    v.upsert("Apps", "Sketcher 2", "## What it is\nA drawing app.\n\n## How to do it\n1. Download it from the site\n\n"
             + ad.SCRIPTING_HEADING + "\n- sketch.box(size) adds a box\n\n" + driver_part, source="web", tags=tags)
    knowledge, driver, _ = ad.learned("Sketcher 2")
    assert knowledge.startswith("Scripting notes:") and "sketch.box" in knowledge and driver is not None
    assert "Download" not in knowledge and "--headless" not in knowledge and "example.com/src" not in knowledge
    v.upsert("Apps", "Sketcher 2", "## What it is\nA drawing app.\n\n" + driver_part, source="web", tags=tags)
    knowledge, driver, _ = ad.learned("Sketcher 2")  # no scripting notes yet: the rest, without the code-only parts
    assert "A drawing app." in knowledge and "--headless" not in knowledge and "example.com/src" not in knowledge


def test_examples_are_run_in_the_app_first_and_only_working_ones_are_kept(world, monkeypatch):
    # An example written for another version may not work here: each is run
    # hidden first, and what it really made goes with it.
    exe, _said = world

    def test(name, driver, exe, code, inspect=""):
        ad._test.last_output = "Blender noise\nMBUDDY_STATE: Roof MESH size (4, 4, 1)"
        return (True, "", "") if "roof" in code else (False, "AttributeError: no old_call", "script")
    monkeypatch.setattr(ad, "_test", test)
    kept = ad.verify_examples("Sketcher 2", DRIVER, str(exe), [("https://a.example", "import sketch\nsketch.old_call()"),
                                                               ("https://b.example", "import sketch\nsketch.roof()")],
                              "print('MBUDDY_STATE: x')")
    assert [link for link, _ in kept] == ["https://b.example"]
    assert "What it made in Sketcher 2" in kept[0][1] and "Roof MESH size (4, 4, 1)" in kept[0][1]
    assert "noise" not in kept[0][1]


def test_a_snippet_that_leaves_out_the_import_gets_it(monkeypatch):
    monkeypatch.setattr(ad, "_ask", lambda *a: "sketch house")
    monkeypatch.setattr(bs, "_search_raw", lambda q: [{"href": "https://a.example/one"}])
    monkeypatch.setattr(bs, "read_code_blocks", lambda url: ["sketch.box(1)\nsketch.box(2)\nsketch.roof()\nsketch.door()"])
    found = ad.find_examples("Sketcher 2", DRIVER, "a small house")
    assert found and found[0][1].startswith("import sketch\n") and found[0][1].count("import sketch") == 1


def test_one_bad_box_on_a_page_doesnt_sink_the_good_ones(world, monkeypatch):
    exe, _said = world
    monkeypatch.setattr(ad, "_test", lambda name, driver, exe, code, inspect="":
                        (False, "NameError: name 'self' is not defined", "script") if "self" in code else (True, "", ""))
    kept = ad.verify_examples("Sketcher 2", DRIVER, str(exe),
                              [("https://a.example", "import sketch\nsketch.box()\n\nself.report('x')\n\nimport sketch\nsketch.roof()")], "")
    assert kept and "sketch.box()" in kept[0][1] and "sketch.roof()" in kept[0][1] and "self" not in kept[0][1]


def test_an_error_shown_in_a_dialog_is_read_and_nothing_is_left_behind():
    # S116 B-A: an app put its error in a dialog and never exited; the runner
    # waited 120 s and debugged a timeout. A real child shows a message box
    # (in this test only; never in model code).
    import ctypes
    import sys
    import time
    driver = {"language": "Python", "file_ext": ".py", "modules": [], "test": ["{exe}", "{file}"], "show": ["{exe}", "{file}"]}
    code = "import ctypes\nctypes.windll.user32.MessageBoxW(0, 'Known problem 4711: option given twice', 'Sketch runner test', 0)"
    started = time.monotonic()
    ok, error, where = ad._test("Sketcher 2", driver, sys.executable, code)
    took = time.monotonic() - started
    assert not ok and "showed a message" in error and "Known problem 4711" in error, error
    assert took < 25, took
    assert ctypes.windll.user32.FindWindowW(None, "Sketch runner test") == 0   # killed, not left on screen


def test_a_success_nobody_could_check_is_kept_as_unverified_and_reinforces_nothing(world, monkeypatch):
    # U2: "ran and the review passed" without any code check isn't a success.
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    monkeypatch.setattr(ad, "inspector", lambda name: "")          # no way to see the result
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: "import sketch\nsketch.box()")
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    used = []
    monkeypatch.setattr(bs, "note_used", lambda paths, success: used.append(success))
    ad._job("Sketcher 2", str(exe), "a small house")
    meta, body, _ = bs.get_vault().read(bs.get_vault().base / "Skills" / "Sketcher 2 - a small house.md")
    assert meta["status"] == "unverified" and float(meta["strength"]) <= 0.3 and not meta.get("verified")
    assert "Not verified by a check" in body and used == [None]


def test_a_checked_success_is_kept_as_verified(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: "import sketch\nsketch.box()")
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    ad._job("Sketcher 2", str(exe), "a small house")
    meta, _body, _ = bs.get_vault().read(bs.get_vault().base / "Skills" / "Sketcher 2 - a small house.md")
    assert meta.get("verified") and meta["status"] != "unverified"


def test_an_unverified_skill_is_only_offered_as_an_earlier_attempt(world, monkeypatch):
    exe, said = world
    _learn_into_vault("Sketcher 2")
    path = bs.get_vault().upsert("Skills", "Sketcher 2 - a small house", "## Script\n```\nimport sketch\nsketch.house()\n```",
                                 source="self", tags=["mbuddy/skill", "mbuddy/app/sketcher-2"])
    bs.get_vault().mark_checked(path, False)
    found = ad._skill("Sketcher 2", "a bigger house")
    assert found and found[2] is False and "sketch.house()" in found[1]
    seen = {}
    monkeypatch.setattr(ad, "_compose", lambda name, driver, knowledge, task, example, *rest:
                        seen.setdefault("example", example) or "import sketch\nsketch.house()")
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    started = []  # no background job from use_app: it would outlive the test
    monkeypatch.setattr(ad.threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda s: started.append(args)})())
    assert "I've tried something like this" in ad.use_app({"app": "Sketcher 2", "task": "a bigger house"})
    ad._busy.clear()
    ad._job("Sketcher 2", str(exe), "a bigger house")
    assert seen["example"].startswith("# An earlier attempt of yours, never verified by a check")


def test_a_radius_or_a_size_is_not_a_count_and_a_plural_alone_needs_one():
    # F54: "Leaves: uv_sphere, radius 2.0" wanted 2 leaves and burned every try
    one_leaf = "MBUDDY_STATE: Leaves MESH (4.0, 4.0, 4.0) at (0, 0, 5)\nMBUDDY_STATE: Trunk MESH (0.4, 0.4, 3.0) at (0, 0, 1.5)"
    assert ad.coverage("- Leaves: uv_sphere, radius 2.0\n- Trunk: cylinder 0.4 m wide", one_leaf) == ""
    legs = "\n".join(f"MBUDDY_STATE: Leg{i} MESH (0.04, 0.04, 0.43) at (0, 0, 0)" for i in range(3))
    assert "legs (4)" in ad.coverage("- Legs: 4 cylinders, radius 0.02 m", legs)


def test_a_solid_part_flattened_to_nothing_is_caught_but_a_plane_is_fine():
    flat = "MBUDDY_STATE: Seat MESH (0.45, 0.45, 0.00) at (0, 0, 0.45)"
    assert "collapsed or flat" in ad.coverage("- Seat: a box 45x45x4 cm", flat)
    assert ad.coverage("- Floor: a plane 10 m wide", "MBUDDY_STATE: Floor MESH (10.0, 10.0, 0.0) at (0, 0, 0)") == ""


def test_the_request_is_checked_not_only_the_plan():
    # F53: the plan forgot a leg, the user asked for four
    three = "\n".join(f"MBUDDY_STATE: Leg{i} MESH (0.04, 0.04, 0.43) at (0, 0, 0)" for i in range(3))
    assert ad.against_request("a chair with four wooden legs", three) == "- You asked for 4 legs; the scene has 3."
    assert ad.against_request("a chair with 5 cm legs", three) == ""                 # a size, not a count
    assert ad.against_request("a two storey house", "MBUDDY_STATE: House MESH (4, 4, 6) at (0, 0, 0)") == ""
    assert ad.against_request("3 windows", "MBUDDY_STATE: Wall MESH (4, 0.2, 3) at (0, 0, 0)") == ""  # named otherwise


def test_a_file_is_written_only_when_asked_and_only_into_the_folder(tmp_path):
    # F4: 'render it to my folder' was refused on every try
    assert ad.wants_file("render the chair to my folder") and ad.wants_file("export it as chair.stl")
    assert not ad.wants_file("make a red chair")
    render = f"import bpy\nbpy.context.scene.render.filepath = r'{tmp_path / 'chair.png'}'\nbpy.ops.render.render(write_still=True)"
    assert "write_still" in ad.unsafe_python(render)                                     # not asked: still refused
    assert ad.unsafe_python(render, out_dir=tmp_path) == ""
    outside = "import bpy\nbpy.ops.wm.save_as_mainfile(filepath='C:/Windows/x.blend')"
    assert "may only go in" in ad.unsafe_python(outside, out_dir=tmp_path)
    assert "may only go in" in ad.unsafe_python("bpy.ops.wm.save_as_mainfile(filepath='chair.blend')", out_dir=tmp_path)
    assert "quit" in ad.unsafe_python("import bpy\nbpy.ops.wm.quit_blender()", out_dir=tmp_path)  # closing is never allowed


def test_a_follow_up_builds_on_what_was_made_and_asks_before_closing_the_old_window(world, monkeypatch):
    # F12: 'house', then 'add a tree' opened a second window with only the tree
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    composed, ran, asked = [], [], []
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: composed.append(a[-1] if len(a) > 11 else k.get("base", ""))
                        or "import sketch\nsketch.tree()")
    monkeypatch.setattr(ad, "_test", lambda name, driver, exe, code, *r: ran.append(code) or (True, "", ""))
    monkeypatch.setattr(ad, "review_result", lambda *a: "")
    windows = iter([111, 222])
    monkeypatch.setattr(ad, "_show", lambda name, *a: ad._SHOWN.__setitem__(ad.slug(name), next(windows)) or True)
    from brain import pc_windows
    monkeypatch.setattr(pc_windows, "request_hwnd", lambda action, hwnd: asked.append((action, hwnd)) or True)
    ad._ACTIVE_SESSIONS.clear()
    ad._job("Sketcher 2", str(exe), "a small house")
    house = ad.get_active_session("Sketcher 2")["code"]
    ad._job("Sketcher 2", str(exe), "add a tree", ad.get_active_session("Sketcher 2"))
    assert composed[-1] == house                                   # the model is told what already exists
    assert ran[-1].startswith(house) and ran[-1].endswith("sketch.tree()")   # and it runs on top of it
    assert asked == [("close", 111)] and "shall I close it?" in said[-1][0]   # never closed without a yes
    assert ad.get_active_session("Sketcher 2")["code"] == ran[-1]


def test_a_job_ended_as_stalled_shows_and_says_nothing_later(world, monkeypatch):
    # F52: 'I got stuck, I've stopped' and then, a minute later, 'I made it' and a new window
    exe, said = world
    from core import jobs
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: "import sketch\nsketch.box()")
    monkeypatch.setattr(ad, "review_result", lambda *a: "")
    shown = []
    monkeypatch.setattr(ad, "_show", lambda *a: shown.append(1) or True)

    def stalls_while_testing(*a):
        jobs.check_stalled(now=jobs.time.time() + jobs.STALL_S + 60)   # the watcher ends it mid-run
        return True, "", ""
    monkeypatch.setattr(ad, "_test", stalls_while_testing)
    before = len(said)
    ad._job("Sketcher 2", str(exe), "a small house")
    assert not shown and len(said) == before
    assert jobs.snapshot()[0]["outcome"].startswith("stalled")


def test_measured_geometry_is_judged_by_code_not_by_the_model(monkeypatch):
    # live: the model judge failed a correct chair three times; with real sizes, code's checks decide
    asked = []
    monkeypatch.setattr(ad, "_ask", lambda *a, **k: asked.append(1) or "- the seat is wrong")
    legs = "\n".join(f"MBUDDY_STATE: Leg{i} MESH (0.040, 0.040, 0.430) at ({x}, {y}, 0.215)"
                     for i, (x, y) in enumerate([(-0.2, -0.2), (0.2, -0.2), (-0.2, 0.2), (0.2, 0.2)]))
    report = "MBUDDY_STATE: Seat MESH (0.450, 0.450, 0.050) at (0.000, 0.000, 0.450)\n" + legs
    assert ad.review_result("Blender 4.3", "a chair with four legs", "- Seat: a box\n- Legs: four cylinders", report) == ""
    assert not asked
    assert ad.review_result("Sketcher 2", "a house", "- Wall: a wall", "MBUDDY_STATE: Wall x") == "- the seat is wrong"
    assert asked                                                   # no measurements: the model still judges


def test_a_script_cut_off_by_thinking_is_asked_again_without_it(monkeypatch):
    # live: qwen3:4b's thinking used the whole budget four tries running, before any script
    import brain.agent as agent
    monkeypatch.setattr(agent, "_chat_model", lambda: "qwen3:4b-instruct")   # its policy thinks (as live)
    sent = []

    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def post(url, json, timeout):
        sent.append(json["think"])
        return R({"done_reason": "length", "message": {"content": ""}} if json["think"]
                 else {"done_reason": "stop", "message": {"content": "import bpy\nbpy.ops.mesh.primitive_cube_add()"}})
    monkeypatch.setattr(ad.requests, "post", post)
    task = ("make a simple chair with four legs and render it to my folder (The user asked for a file: write it "
            "with its full path inside C:/Users/x/Documents/MBUDDY/blender and nowhere else.)")   # long: thinking is on
    out = ad._compose("Blender 4.3", {"language": "Python"}, "", task, "", "", "")
    assert sent == [True, False] and "primitive_cube_add" in out


def test_a_task_that_works_after_failing_is_not_left_unfinished(world, monkeypatch):
    exe, said = world
    monkeypatch.setattr(bs, "learn_app_now", _learn_into_vault)
    monkeypatch.setattr(ad, "_compose", lambda *a, **k: "import sketch\nsketch.box()")
    monkeypatch.setattr(ad, "_test", lambda *a: (True, "", ""))
    monkeypatch.setattr(ad, "_show", lambda *a: True)
    monkeypatch.setattr(ad, "MAX_TRIES", 1)
    monkeypatch.setattr(ad, "review_result", lambda *a: "- it's a box, not a house")
    ad._job("Sketcher 2", str(exe), "a small house")
    note = bs.get_vault().base / "Skills" / "Sketcher 2 - a small house.md"
    assert "mbuddy/unfinished" in note.read_text(encoding="utf-8")
    monkeypatch.setattr(ad, "review_result", lambda *a: "")
    ad._job("Sketcher 2", str(exe), "a small house")
    assert "mbuddy/unfinished" not in note.read_text(encoding="utf-8")
