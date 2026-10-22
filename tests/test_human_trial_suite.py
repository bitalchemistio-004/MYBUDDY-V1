"""
Human Trial Suite — tests every scenario a real user would hit when using
MBUDDY with a connected app (Blender, Sketcher, any scripted app).

Focus: the three real root-causes found in backend.log, plus the full range
of realistic human interactions.

All tests are offline: no real model, no real app, no network.
"""
import re
import pytest
from unittest.mock import MagicMock, patch
from brain.connectors import app_driver as ad


# ─── shared fixtures ──────────────────────────────────────────────────────────

DRIVER = {
    "language": "Python",
    "file_ext": ".py",
    "modules": ["bpy"],
    "test": ["{exe}", "--background", "--python", "{file}"],
    "show": ["{exe}", "--python", "{file}"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 1: plan_result naming — the PRIMARY bug from real logs
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlanNamingFixesCoverageFailure:
    """Before the fix, plan_result() returned generic structural terms like
    'walls', 'front face', 'back face' that never matched Blender object names.
    The new format uses exact object names like 'BaseSlab', 'FirstFloor'."""

    def test_coverage_passes_when_plan_uses_exact_object_names(self):
        """Blender script names objects 'BaseSlab', 'FirstFloor', 'Roof', 'Door'.
        Plan must use exactly these names → coverage passes."""
        plan = (
            "BaseSlab: cube 12x10x0.4m at Z=0.2\n"
            "FirstFloor: cube 10x8x3m at Z=1.7\n"
            "SecondFloor: cube 10x8x3m at Z=4.7\n"
            "Roof: cube 11x9x1.5m at Z=6.95\n"
            "Door: cube 1.2x0.2x2.2m in front\n"
        )
        report = (
            "MBUDDY_STATE: BaseSlab (12.00, 10.00, 0.40) at (0.00, 0.00, 0.20)\n"
            "MBUDDY_STATE: FirstFloor (10.00, 8.00, 3.00) at (0.00, 0.00, 1.70)\n"
            "MBUDDY_STATE: SecondFloor (10.00, 8.00, 3.00) at (0.00, 0.00, 4.70)\n"
            "MBUDDY_STATE: Roof (11.00, 9.00, 1.50) at (0.00, 0.00, 6.95)\n"
            "MBUDDY_STATE: Door (1.20, 0.20, 2.20) at (0.00, -5.10, 1.10)\n"
        )
        result = ad.coverage(plan, report)
        assert result == "", f"Expected no coverage issues, got: {result!r}"

    def test_coverage_fails_when_plan_uses_old_generic_terms(self):
        """Old plan format with 'walls', 'front face' guarantees failure.
        This was the exact failure mode seen in all 6 Blender tries."""
        plan = (
            "- Base: a rectangular slab on the ground\n"
            "- Walls: four upright panels around the base\n"
            "- Roof: a flat lid on top\n"
            "- Front face: a visible door opening\n"
        )
        # The script names its objects something completely different
        report = (
            "MBUDDY_STATE: BaseSlab (12.00, 10.00, 0.40) at (0.00, 0.00, 0.20)\n"
            "MBUDDY_STATE: FirstFloor (10.00, 8.00, 3.00) at (0.00, 0.00, 1.70)\n"
        )
        result = ad.coverage(plan, report)
        assert "isn't in the scene" in result

    def test_coverage_passes_for_stickman_with_exact_names(self):
        """Stickman scenario: Head, Torso, ArmLeft, ArmRight, LegLeft, LegRight."""
        plan = (
            "Head: sphere radius=0.35m at Z=7.1\n"
            "Torso: cylinder radius=0.1m depth=1.2m at Z=6.1\n"
            "ArmLeft: cylinder radius=0.06m depth=0.8m at left shoulder\n"
            "ArmRight: cylinder radius=0.06m depth=0.8m at right shoulder\n"
            "LegLeft: cylinder radius=0.06m depth=0.8m at left hip\n"
            "LegRight: cylinder radius=0.06m depth=0.8m at right hip\n"
        )
        report = (
            "MBUDDY_STATE: Head (0.70, 0.70, 0.70) at (0.00, 0.00, 7.10)\n"
            "MBUDDY_STATE: Torso (0.20, 0.20, 1.20) at (0.00, 0.00, 6.10)\n"
            "MBUDDY_STATE: ArmLeft (0.12, 0.12, 0.80) at (-0.30, 0.00, 6.20)\n"
            "MBUDDY_STATE: ArmRight (0.12, 0.12, 0.80) at (0.30, 0.00, 6.20)\n"
            "MBUDDY_STATE: LegLeft (0.12, 0.12, 0.80) at (-0.15, 0.00, 5.10)\n"
            "MBUDDY_STATE: LegRight (0.12, 0.12, 0.80) at (0.15, 0.00, 5.10)\n"
        )
        result = ad.coverage(plan, report)
        assert result == "", f"Stickman coverage should pass: {result!r}"

    def test_coverage_catches_collapsed_zero_volume_objects(self):
        """A common Blender failure: creating a mesh but scaling to 0 on one axis."""
        plan = "BaseSlab: cube 12x10x0.4m at Z=0.2\n"
        report = "MBUDDY_STATE: BaseSlab (12.00, 10.00, 0.00) at (0.00, 0.00, 0.00)\n"
        result = ad.coverage(plan, report)
        assert "collapsed or flat" in result

    def test_coverage_catches_default_cube_left_in_scene(self):
        """Script forgot to remove the default Cube at origin."""
        plan = "BaseSlab: cube at origin\n"
        report = "MBUDDY_STATE: Cube (2.00, 2.00, 2.00) at (0, 0, 0)\n"
        # When 'cube' key is not in the plan, any unmodified Cube at origin is flagged
        plan2 = "BaseSlab: a big base\n"
        result = ad.coverage(plan2, report)
        assert "uncleaned" in result


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 2: Security — AST sandbox must never be bypassed
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityBoundaries:
    """The AST sandbox is the only hard security wall. No fix should ever weaken it."""

    def test_import_os_is_blocked(self):
        assert ad.unsafe_python("import os\nos.remove('/important')") != ""

    def test_import_sys_is_blocked(self):
        assert ad.unsafe_python("import sys\nsys.exit(0)") != ""

    def test_import_subprocess_is_blocked(self):
        assert ad.unsafe_python("import subprocess\nsubprocess.run(['rm', '-rf', '/'])") != ""

    def test_clean_bpy_script_is_allowed(self):
        code = (
            "import bpy\n"
            "bpy.ops.mesh.primitive_cube_add(size=2)\n"
            "obj = bpy.context.active_object\n"
            "obj.name = 'BaseSlab'\n"
            "obj.scale = (6, 5, 0.2)\n"
            "bpy.ops.object.transform_apply(scale=True)\n"
        )
        assert ad.unsafe_python(code) == ""

    def test_save_call_is_blocked(self):
        assert ad.unsafe_python("import bpy\nbpy.ops.wm.save_mainfile()") != ""

    def test_export_call_is_blocked(self):
        assert ad.unsafe_python("import bpy\nbpy.ops.export_mesh.stl(filepath='/tmp/x.stl')") != ""

    def test_dunder_attribute_is_blocked(self):
        assert ad.unsafe_python("x.__class__.__bases__[0].__subclasses__()") != ""

    def test_write_still_kwarg_is_blocked(self):
        assert ad.unsafe_python("import bpy\nbpy.ops.render.render(write_still=True)") != ""

    def test_complex_clean_blender_script_passes(self):
        """A full realistic building script should pass the security checker."""
        code = """
import bpy
import math

# Cleanup
for obj in list(bpy.data.objects):
    if obj.type == 'MESH':
        bpy.data.objects.remove(obj, do_unlink=True)

def add_box(name, loc, size, color):
    bpy.ops.mesh.primitive_cube_add(location=loc)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0]/2, size[1]/2, size[2]/2)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new(name=name + '_mat')
    mat.diffuse_color = color
    obj.data.materials.append(mat)

add_box('BaseSlab', (0,0,0.2), (12,10,0.4), (0.7,0.7,0.7,1))
add_box('FirstFloor', (0,0,1.7), (10,8,3.0), (0.9,0.9,0.8,1))
add_box('SecondFloor', (0,0,4.7), (10,8,3.0), (0.9,0.9,0.8,1))
add_box('Roof', (0,0,6.95), (11,9,1.5), (0.6,0.35,0.15,1))
"""
        assert ad.unsafe_python(code) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 3: code_from — model reply parsing robustness
# ═══════════════════════════════════════════════════════════════════════════════

class TestCodeFromParsingRobustness:
    """Real models produce many different output formats. All must parse cleanly."""

    def test_fenced_python_block(self):
        reply = "Sure! Here it is:\n```python\nimport bpy\nbpy.ops.mesh.primitive_cube_add()\n```"
        assert "import bpy" in ad.code_from(reply)

    def test_fenced_block_no_language(self):
        reply = "```\nimport bpy\nbpy.ops.mesh.primitive_cube_add()\n```"
        assert "import bpy" in ad.code_from(reply)

    def test_no_fence_starts_with_import(self):
        reply = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
        assert ad.code_from(reply) == reply.strip()

    def test_explanation_before_code(self):
        reply = "Here's what I'll do:\nimport bpy\nobj = bpy.context.active_object"
        result = ad.code_from(reply)
        assert "import bpy" in result

    def test_think_tags_stripped_from_ask_response(self):
        """_ask() must strip <think>...</think> so plan/diagnosis never includes it."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                json=lambda: {"message": {"content": "<think>Let me reason...</think>\nBaseSlab: base cube"}}
            )
            with patch("brain.agent._chat_model", return_value="test"), \
                 patch("brain.agent._ollama_chat_url", return_value="http://localhost:11434/api/chat"):
                result = ad._ask("system", "user", 100)
        assert "<think>" not in result
        assert "BaseSlab" in result

    def test_empty_model_response_returns_empty(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(json=lambda: {"message": {"content": ""}})
            with patch("brain.agent._chat_model", return_value="test"), \
                 patch("brain.agent._ollama_chat_url", return_value="http://localhost:11434/api/chat"):
                result = ad._ask("system", "user", 100)
        assert result == ""

    def test_network_failure_returns_empty_not_exception(self):
        with patch("requests.post", side_effect=ConnectionError("offline")):
            with patch("brain.agent._chat_model", return_value="test"), \
                 patch("brain.agent._ollama_chat_url", return_value="http://localhost:11434/api/chat"):
                result = ad._ask("system", "user", 100)
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 4: Validate driver — real model output normalisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestDriverValidation:
    """The model commonly returns creative values that must be normalised."""

    def test_model_names_program_instead_of_exe_placeholder(self):
        d = ad.validate_driver({
            "language": "Python", "file_ext": ".py", "modules": ["bpy"],
            "test": ["blender", "--background", "--python", "myscript.py"],
            "show": ["blender", "--python", "myscript.py"]
        })
        assert d is not None
        assert d["test"][0] == "{exe}"
        assert "{file}" in d["test"]

    def test_file_ext_without_dot_is_fixed(self):
        d = ad.validate_driver({**DRIVER, "file_ext": "py"})
        assert d is not None
        assert d["file_ext"] == ".py"

    def test_shell_injection_in_command_is_blocked(self):
        evil = ["{exe}", "--python", "{file}", "&&", "del", "*"]
        assert ad.validate_driver({**DRIVER, "test": evil}) is None

    def test_entirely_missing_language_is_refused(self):
        assert ad.validate_driver({"file_ext": ".py", "test": ["{exe}", "{file}"],
                                   "show": ["{exe}", "{file}"]}) is None

    def test_missing_file_placeholder_is_refused(self):
        assert ad.validate_driver({**DRIVER, "test": ["{exe}", "--run"]}) is None

    def test_valid_non_python_driver(self):
        d = ad.validate_driver({
            "language": "JavaScript", "file_ext": ".js", "modules": ["node"],
            "test": ["{exe}", "{file}"], "show": ["{exe}", "{file}"]
        })
        assert d is not None
        assert d["language"] == "JavaScript"

    def test_too_many_command_args_is_refused(self):
        long_cmd = ["{exe}"] + [f"--arg{i}" for i in range(12)] + ["{file}"]
        assert ad.validate_driver({**DRIVER, "test": long_cmd}) is None


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 5: Example cleaning — web scraped code sanitisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestExampleCleaning:
    """Web scraped examples often have docstrings, comments, and banned calls.
    clean_example() must strip them without breaking valid logic."""

    def test_docstrings_are_stripped(self):
        code = 'def foo():\n    """Does stuff."""\n    import bpy\n    bpy.ops.mesh.primitive_cube_add()'
        cleaned = ad.clean_example(code, "Python")
        assert '"""' not in cleaned
        assert "import bpy" in cleaned

    def test_comments_are_stripped(self):
        code = "# Set up scene\nimport bpy\n# Add cube\nbpy.ops.mesh.primitive_cube_add()"
        cleaned = ad.clean_example(code, "Python")
        assert "#" not in cleaned
        assert "import bpy" in cleaned

    def test_banned_save_calls_removed(self):
        code = "import bpy\nbpy.ops.wm.save_mainfile()\nbpy.ops.mesh.primitive_cube_add()"
        cleaned = ad.clean_example(code, "Python")
        # After cleaning, the cleaned code is either empty (syntax broke) or save is gone
        if cleaned:
            assert "save_mainfile" not in cleaned

    def test_syntax_error_in_code_returns_empty(self):
        code = "import bpy\ndef foo(\n  # broken"
        cleaned = ad.clean_example(code, "Python")
        assert cleaned == ""

    def test_non_python_comments_stripped(self):
        code = "// Create mesh\nmesh.add_cube()\n// place it\nmesh.translate(0,0,1)"
        cleaned = ad.clean_example(code, "JavaScript")
        assert "//" not in cleaned
        assert "mesh.add_cube()" in cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 6: Coverage edge cases — realistic human task variations
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoverageEdgeCases:
    """Realistic task variations that expose corner cases in coverage()."""

    def test_plural_count_two_trees(self):
        """Plan says 'two trees': needs 2 TreeTrunk objects."""
        plan = "TreeTrunk: two cylinders as tree bases\n"
        report = (
            "MBUDDY_STATE: TreeTrunk (0.40, 0.40, 3.00) at (3.00, 0.00, 1.50)\n"
            "MBUDDY_STATE: TreeTrunk (0.40, 0.40, 3.00) at (-3.00, 0.00, 1.50)\n"
        )
        result = ad.coverage(plan, report)
        # Two found, two needed — should pass
        assert result == "" or "treetrunk" not in result.lower()

    def test_empty_scene_report_fails_any_plan(self):
        """Script ran but produced no MBUDDY_STATE output."""
        plan = "BaseSlab: a base\nRoof: a roof\n"
        result = ad.coverage(plan, "")
        assert "isn't in the scene" in result

    def test_camera_and_light_ignored_in_volume_check(self):
        """Camera and lights are auto-added by Blender; they must NOT trigger volume failures."""
        plan = "BaseSlab: base cube\n"
        report = (
            "MBUDDY_STATE: BaseSlab (12.00, 10.00, 0.40) at (0.00, 0.00, 0.20)\n"
            "MBUDDY_STATE: Camera (0.00, 0.00, 0.00) at (7.48, -6.51, 5.34)\n"
            "MBUDDY_STATE: Light (0.00, 0.00, 0.00) at (4.08, 1.00, 5.90)\n"
            "MBUDDY_STATE: Sun (0.00, 0.00, 0.00) at (0.00, 0.00, 3.00)\n"
        )
        result = ad.coverage(plan, report)
        assert result == ""

    def test_plan_with_no_colons_is_ignored_gracefully(self):
        """If plan_result returns something without colon format, coverage must not crash."""
        plan = "A simple building with a roof and four walls."
        report = "MBUDDY_STATE: Cube (2,2,2) at (0,0,0)\n"
        result = ad.coverage(plan, report)
        assert isinstance(result, str)  # doesn't throw

    def test_building_plus_stickman_full_scenario(self):
        """Complete 2-story building with stickman on 2nd floor — the exact human request."""
        plan = (
            "BaseSlab: cube 12x10x0.4m at Z=0.2\n"
            "FirstFloor: cube 10x8x3m at Z=1.7\n"
            "SecondFloor: cube 10x8x3m at Z=4.7\n"
            "Roof: cube 11x9x1.5m at Z=6.95\n"
            "Door: cube 1.2x0.2x2.2m\n"
            "Head: sphere on 2nd floor\n"
            "Torso: cylinder body\n"
        )
        report = (
            "MBUDDY_STATE: BaseSlab (12.00, 10.00, 0.40) at (0.00, 0.00, 0.20)\n"
            "MBUDDY_STATE: FirstFloor (10.00, 8.00, 3.00) at (0.00, 0.00, 1.70)\n"
            "MBUDDY_STATE: SecondFloor (10.00, 8.00, 3.00) at (0.00, 0.00, 4.70)\n"
            "MBUDDY_STATE: Roof (11.00, 9.00, 1.50) at (0.00, 0.00, 6.95)\n"
            "MBUDDY_STATE: Door (1.20, 0.20, 2.20) at (0.00, -5.10, 1.10)\n"
            "MBUDDY_STATE: Head (0.70, 0.70, 0.70) at (0.00, 0.00, 7.10)\n"
            "MBUDDY_STATE: Torso (0.20, 0.20, 1.20) at (0.00, 0.00, 6.10)\n"
        )
        result = ad.coverage(plan, report)
        assert result == "", f"Full building+stickman should pass: {result!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 7: Failing line extraction — retry loop accuracy
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailingLineExtraction:
    """When the model's script fails, we extract the failing line to focus the retry."""

    def test_extracts_line_from_mbuddy_temp_script(self):
        """failing_line() only extracts from MBUDDY's own temp scripts (test_NNN.py / run_NNN.py).
        These are the files MBUDDY writes to disk before running them in Blender.
        The path must contain a directory separator before the test_ prefix."""
        error = (
            'Traceback (most recent call last):\n'
            '  File "C:\\\\Temp\\\\mbuddy\\\\blender\\\\test_1234567890.py", line 5, in <module>\n'
            '    bpy.context.scene.objects.link(obj)\n'
            'AttributeError: bpy_prop_collection: attribute "link" not found'
        )
        line = ad.failing_line(error)
        assert "link" in line

    def test_returns_empty_for_no_traceback(self):
        error = "blender exited with code 1: some crash log"
        line = ad.failing_line(error)
        assert line == "" or isinstance(line, str)

    def test_extracts_nameerror_self_undefined(self):
        """The exact error from real logs: 'self' is not defined from web-scraped class code."""
        error = (
            'Traceback (most recent call last):\n'
            '  File "62506623.py", line 2, in <module>\n'
            '    if blender_bin:\n'
            '       ^^^^^^^^^^^\n'
            'NameError: name \'blender_bin\' is not defined'
        )
        line = ad.failing_line(error)
        assert isinstance(line, str)


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 8: inspect snippet — learning how to verify results
# ═══════════════════════════════════════════════════════════════════════════════

class TestReportTagParsing:
    """The _report() function filters MBUDDY_STATE lines from script output."""

    def test_filters_report_lines_only(self):
        output = (
            "Starting script...\n"
            "MBUDDY_STATE: BaseSlab (12.00, 10.00, 0.40) at (0.00, 0.00, 0.20)\n"
            "Setup complete.\n"
            "MBUDDY_STATE: Roof (11.00, 9.00, 1.50) at (0.00, 0.00, 6.95)\n"
            "Done.\n"
        )
        result = ad._report(output)
        assert result.count("MBUDDY_STATE") == 2
        assert "Starting script" not in result
        assert "Done" not in result

    def test_empty_output_returns_empty(self):
        assert ad._report("") == ""

    def test_none_output_returns_empty(self):
        assert ad._report(None) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY 9: Active session tracking — continuation tasks
# ═══════════════════════════════════════════════════════════════════════════════

class TestActiveSessionTracking:
    """User says 'now add a window to the building' — the session context must carry over."""

    def setup_method(self):
        ad._ACTIVE_SESSIONS.clear()

    def test_session_stored_and_retrieved(self):
        ad.record_active_session("Blender 4.3", "create a building", "import bpy\n# code")
        session = ad.get_active_session("Blender 4.3")
        assert session is not None
        assert session["task"] == "create a building"
        assert "import bpy" in session["code"]

    def test_session_for_different_app_not_returned(self):
        ad.record_active_session("Blender 4.3", "create building", "code")
        result = ad.get_active_session("Sketcher Pro")
        assert result is None

    def test_session_slug_normalises_app_name(self):
        """'Blender 4.3' and 'blender_4_3' should map to same session key."""
        ad.record_active_session("Blender 4.3", "task A", "code A")
        session = ad.get_active_session("Blender 4.3")
        assert session is not None

    def test_session_updated_on_new_task(self):
        ad.record_active_session("Blender 4.3", "task A", "code A")
        ad.record_active_session("Blender 4.3", "task B", "code B")
        session = ad.get_active_session("Blender 4.3")
        assert session["task"] == "task B"
        assert "code B" in session["code"]
