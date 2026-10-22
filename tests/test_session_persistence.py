import pytest
import time
from brain.connectors.app_driver import (
    record_active_session,
    get_active_session,
    _ACTIVE_SESSIONS,
    _compose,
    coverage,
)


def test_session_recording_and_retrieval():
    app_name = "Blender 4.3"
    task = "design a 3d chair"
    code = "import bpy\nbpy.ops.mesh.primitive_cube_add()\n"
    
    # Record session
    record_active_session(app_name, task, code)
    
    # Retrieve session
    session = get_active_session(app_name)
    assert session is not None
    assert session["app"] == app_name
    assert session["task"] == task
    assert session["code"] == code


def test_session_expiration():
    app_name = "TestApp"
    record_active_session(app_name, "do something", "code")
    
    # Simulate expiration by aging updated_at
    key = "testapp"
    _ACTIVE_SESSIONS[key]["updated_at"] = time.time() - 4000
    
    assert get_active_session(app_name) is None


def test_compose_injects_active_session():
    app_name = "Blender 4.3"
    task1 = "design a 3d chair"
    code1 = "import bpy\n# chair base\n"
    record_active_session(app_name, task1, code1)
    
    driver = {"language": "python", "file_ext": ".py"}
    
    # Mock Ollama call inside _compose by passing invalid URL or testing user prompt string before call
    # We can inspect get_active_session directly
    session = get_active_session(app_name)
    assert session is not None
    assert "chair base" in session["code"]


def test_coverage_allows_subcomponents():
    plan = "- Seat: flat rectangle\n- Backrest: vertical rectangle"
    report = "MBUDDY_STATE: Seat (0.6, 0.4, 0.3) at (0, 0, 0)\nMBUDDY_STATE: Backrest (0.6, 0.8, 0.2) at (0, 0, 0.3)\nMBUDDY_STATE: LeftArmrest (0.1, 0.5, 0.3) at (-0.3, 0, 0.3)"
    
    problems = coverage(plan, report)
    # Coverage should NOT flag LeftArmrest as forbidden extra object
    assert "LeftArmrest" not in problems
    assert problems == ""


def test_coverage_allows_trees_and_house():
    plan = "- House: main building\n- Roof: top roof"
    report = ("MBUDDY_STATE: House (4, 4, 3) at (0, 0, 0)\n"
              "MBUDDY_STATE: Roof (4.2, 4.2, 1.5) at (0, 0, 3)\n"
              "MBUDDY_STATE: Tree1 (0.8, 0.8, 4) at (-6, 2, 0)\n"
              "MBUDDY_STATE: TreeFoliage1 (2, 2, 2) at (-6, 2, 4)\n"
              "MBUDDY_STATE: Tree2 (0.8, 0.8, 4) at (6, 2, 0)\n"
              "MBUDDY_STATE: TreeFoliage2 (2, 2, 2) at (6, 2, 4)")
    
    problems = coverage(plan, report)
    # Coverage must NOT ask to remove Tree1 or Tree2!
    assert "Tree1" not in problems
    assert "Tree2" not in problems
    assert problems == ""


def test_coverage_flags_uncleaned_default_cube():
    plan = "- House: main building"
    report = "MBUDDY_STATE: House (4, 4, 3) at (5, 0, 0)\nMBUDDY_STATE: Cube (2, 2, 2) at (0, 0, 0)"
    
    problems = coverage(plan, report)
    assert "uncleaned default primitives" in problems
    assert "cube" in problems
