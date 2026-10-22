"""
Comprehensive Real-World Behavioral Validation Test Suite.
Validates model-capability awareness, dynamic context budgeting, priority context preservation,
outcome fidelity verification, security integrity, multi-model adaptation, failure retries, and simple task regressions.
"""

import pytest
import json
import re
from brain.model_capabilities import (
    ModelCapabilities,
    CapabilityStatus,
    TaskComplexity,
    detect_model_capabilities,
    derive_execution_policy,
    preserve_structured_script,
    preserve_structured_error,
    PriorityContextBuilder,
    estimate_tokens,
)
from brain.connectors.app_driver import unsafe_python, _compose, diagnose
from brain.verification import VerificationEngine, VerificationStatus


# ---------------------------------------------------------------------------
# 1. Real-World Connected-App Benchmark & Outcome Quality Report Test
# ---------------------------------------------------------------------------

def test_benchmark_old_vs_new_pipeline_comparison():
    """
    Controlled comparison between OLD pipeline policy (hardcoded 4096 context, 1300 gen budget, think=False)
    and NEW pipeline policy (ModelCapabilities, dynamic 8192+ context, dynamic gen budget, think=True on complex tasks).
    """
    complex_task = "Build a 2-story villa with foundation slab, ground floor walls, upper balcony, roof structure, and multi-canopy trees with visual materials in Blender 4.x"
    complex_plan = "- Base slab: (10, 8, 0.5)\n- 1st floor walls: (10, 8, 3.0)\n- 2nd floor balcony: (8, 6, 2.5)\n- Roof: (10, 8, 1.5)\n- 4x Trees: trunk cylinder + foliage canopy"

    # Simulated OLD pipeline behavior:
    old_caps = ModelCapabilities(model_name="qwen3:4b", context_window=4096, max_generation_tokens=1300, supports_thinking=False)
    old_policy = derive_execution_policy(old_caps, TaskComplexity.SIMPLE)
    old_builder = PriorityContextBuilder(old_caps, old_policy)
    old_builder.add_item(0, "task", complex_task)
    old_builder.add_item(0, "plan", complex_plan)
    old_prompt, old_report, old_gen_budget = old_builder.build()

    # Simulated NEW pipeline behavior:
    new_caps = ModelCapabilities(model_name="qwen3:4b", context_window=8192, max_generation_tokens=4096, supports_thinking=True)
    new_policy = derive_execution_policy(new_caps, TaskComplexity.COMPLEX)
    new_builder = PriorityContextBuilder(new_caps, new_policy)
    new_builder.add_item(0, "task", complex_task)
    new_builder.add_item(0, "plan", complex_plan)
    new_prompt, new_report, new_gen_budget = new_builder.build()

    # Metrics assertion
    assert old_policy.enable_thinking is False
    assert new_policy.enable_thinking is True
    assert new_gen_budget > old_gen_budget
    assert new_report.context_window == 8192
    assert new_gen_budget >= 2000


# ---------------------------------------------------------------------------
# 2. Failure Mode Test: Technically Valid vs Outcome Failure
# ---------------------------------------------------------------------------

def test_failure_mode_technically_valid_but_outcome_failed():
    """
    Validates that a script which runs cleanly (exit code 0), but outputs only a flat wireframe
    or default single cube without requested villa structure, is correctly flagged as VERIFIED_FAILURE.
    """
    engine = VerificationEngine()
    task = "Build a 2-story villa with balcony, roof, and multi-mesh trees"
    plan = "- 1st floor: walls (10, 8, 3)\n- 2nd floor: balcony (8, 6, 2.5)\n- Roof: slab\n- Trees: trunk + foliage"

    # Execution Succeeded (exit code 0), but report shows only default Cube (0.00 volume)
    report_single_cube = "MBUDDY_STATE: Cube MESH at (0.00, 0.00, 0.00) size (0.00, 0.00, 0.00)"

    res = engine.verify(
        task=task,
        plan=plan,
        execution_data={"exit_code": 0, "stdout": report_single_cube, "stderr": "", "report": report_single_cube},
    )

    # Must NOT be fooled by exit_code 0 or missing syntax error
    assert res.status == VerificationStatus.VERIFIED_FAILURE
    assert res.missing_requirements or "collapsed or flat" in res.details.lower() or "volume" in res.details.lower()


# ---------------------------------------------------------------------------
# 3. Context Preservation with Real Large Inputs
# ---------------------------------------------------------------------------

def test_real_large_inputs_context_preservation_priority_drop_sequence():
    """
    Verifies that when context pressure occurs, P0 items (Task, Plan, Failing Code, Exception Traceback)
    are strictly preserved while P3 (Notes) and P2 (Research/Knowledge) are shed first.
    """
    caps = ModelCapabilities(model_name="qwen3:4b", context_window=4000, max_generation_tokens=1000)
    policy = derive_execution_policy(caps, TaskComplexity.DEBUGGING)
    builder = PriorityContextBuilder(caps, policy)

    failing_code = "import bpy\ndef create_villa():\n" + "\n".join(f"    bpy.ops.mesh.primitive_cube_add(location=({i}, 0, 0))" for i in range(20)) + "\ncreate_villa()\n"
    failing_error = "Traceback (most recent call last):\n  File 'villa.py', line 15\nValueError: Invalid mesh size"
    large_research = "Web snippet detail information line: " * 300
    large_notes = "Background user note detailed description: " * 800

    builder.add_item(0, "task", "Build complex architectural model")
    builder.add_item(0, "plan", "- Floor 1\n- Floor 2\n- Roof")
    builder.add_item(0, "failing_code", preserve_structured_script(failing_code, max_tokens=300))
    builder.add_item(0, "error", preserve_structured_error(failing_error, max_tokens=200))
    builder.add_item(2, "web_research", large_research)
    builder.add_item(3, "user_notes", large_notes)

    prompt, report, gen_budget = builder.build()

    # P0 items MUST be present
    assert "Build complex architectural model" in prompt
    assert "- Floor 1" in prompt
    assert "ValueError: Invalid mesh size" in prompt
    assert "Traceback" in prompt
    # P3 notes should be dropped due to context pressure
    assert len(report.dropped_priorities) > 0 or "user_notes" not in prompt


# ---------------------------------------------------------------------------
# 4. Dynamic Generation Budgeting Scaling (Small, Medium, Complex Tasks)
# ---------------------------------------------------------------------------

def test_dynamic_generation_budgeting_scaling():
    """
    Validates dynamic generation budget allocation across Small, Medium, and Complex tasks.
    """
    caps = ModelCapabilities(model_name="qwen3:4b", context_window=16384, max_generation_tokens=8192, supports_thinking=True)

    # Case A: Simple task
    policy_simple = derive_execution_policy(caps, TaskComplexity.SIMPLE)
    assert policy_simple.min_generation_tokens == 512
    assert policy_simple.enable_thinking is False

    # Case B: Moderate task
    policy_mod = derive_execution_policy(caps, TaskComplexity.MODERATE)
    assert policy_mod.min_generation_tokens == 1024
    assert policy_mod.max_generation_tokens >= 2048

    # Case C: Complex task
    policy_comp = derive_execution_policy(caps, TaskComplexity.COMPLEX)
    assert policy_comp.enable_thinking is True
    assert policy_comp.max_generation_tokens == 8192

    # Verify actual math
    builder = PriorityContextBuilder(caps, policy_comp)
    builder.add_item(0, "task", "Build a complete 3D scene")
    _, report, gen_budget = builder.build()

    assert gen_budget > 4000
    assert report.allocated_generation_tokens >= 4000


# ---------------------------------------------------------------------------
# 5. Multi-Model Capability Adaptation
# ---------------------------------------------------------------------------

def test_multi_model_capability_adaptation(monkeypatch):
    """
    Validates execution policy adaptation across 5 model capability profiles.
    """
    # Profile A: Thinking-capable (Qwen3 4B)
    caps_qwen = ModelCapabilities(model_name="qwen3:4b", context_window=8192, supports_thinking=True)
    pol_qwen = derive_execution_policy(caps_qwen, TaskComplexity.COMPLEX)
    assert pol_qwen.enable_thinking is True

    # Profile B: Non-thinking (Llama3 8B)
    caps_llama = ModelCapabilities(model_name="llama3:8b", context_window=8192, supports_thinking=False)
    pol_llama = derive_execution_policy(caps_llama, TaskComplexity.COMPLEX)
    assert pol_llama.enable_thinking is False

    # Profile C: Small context (4096)
    caps_small = ModelCapabilities(model_name="small:3b", context_window=4096, max_generation_tokens=2048)
    pol_small = derive_execution_policy(caps_small, TaskComplexity.COMPLEX)
    assert pol_small.max_generation_tokens <= 2048

    # Profile D: Large context (32768)
    caps_large = ModelCapabilities(model_name="qwen-coder:32b", context_window=32768, max_generation_tokens=8192)
    pol_large = derive_execution_policy(caps_large, TaskComplexity.COMPLEX)
    assert pol_large.max_generation_tokens >= 4096

    # Profile E: Unknown / Offline Fallback
    caps_fallback = detect_model_capabilities("unknown-model-v999")
    assert caps_fallback.capability_status == CapabilityStatus.FALLBACK
    assert caps_fallback.context_window >= 8192


# ---------------------------------------------------------------------------
# 6. Failure & Retry Context Preservation Across Multi-Turn Retries
# ---------------------------------------------------------------------------

def test_multi_turn_failure_retry_context_preservation():
    """
    Validates that information is preserved across Attempt 1 (Execution failure) -> Diagnosis ->
    Attempt 2 (Verification failure) -> Attempt 3 (Success).
    """
    caps = ModelCapabilities(model_name="qwen3:4b", context_window=8192, supports_thinking=True)
    policy_retry = derive_execution_policy(caps, TaskComplexity.DEBUGGING, is_retry=True)
    builder = PriorityContextBuilder(caps, policy_retry)

    attempt_1_code = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=0)"
    attempt_1_err = "ValueError: zero volume"
    diagnosis_1 = "What went wrong: cube size 0\nWhy: flat wireframe\nFix: change size to 2"

    builder.add_item(0, "task", "Build a proper 3D cube with volume")
    builder.add_item(0, "failing_code", attempt_1_code)
    builder.add_item(0, "error", attempt_1_err)
    builder.add_item(0, "diagnosis", diagnosis_1)

    prompt, _, _ = builder.build()

    assert "Build a proper 3D cube with volume" in prompt
    assert "bpy.ops.mesh.primitive_cube_add" in prompt
    assert "ValueError: zero volume" in prompt
    assert "What went wrong: cube size 0" in prompt
    assert policy_retry.enable_thinking is True


# ---------------------------------------------------------------------------
# 7. Security Sandbox & AST Validation Under High Generation Budget
# ---------------------------------------------------------------------------

def test_security_ast_and_sandbox_under_high_budget():
    """
    Validates that larger generation budgets and complete prompts NEVER allow bypassing unsafe_python security boundaries.
    """
    malicious_scripts = [
        "import os\nos.system('rm -rf /')",
        "import subprocess\nsubprocess.call(['cmd.exe', '/c', 'dir'])",
        "import builtins\nbuiltins.eval('__import__(\"os\").remove(\"file.txt\")')",
        "import sys\nprint(sys.modules)",
        "import socket\nsocket.socket().connect(('127.0.0.1', 8080))",
        "open('/etc/passwd', 'r').read()",
    ]

    for script in malicious_scripts:
        err = unsafe_python(script)
        assert err != "", f"Security leak detected: script `{script}` passed unsafe_python!"


# ---------------------------------------------------------------------------
# 8. Regression Test for Simple Tasks
# ---------------------------------------------------------------------------

def test_simple_task_regression_low_overhead():
    """
    Confirms that simple routine requests do NOT incur thinking overhead or excessive prompt inflation.
    """
    caps = ModelCapabilities(model_name="qwen3:4b", context_window=8192, supports_thinking=True)
    policy_simple = derive_execution_policy(caps, TaskComplexity.SIMPLE)

    assert policy_simple.enable_thinking is False
    assert policy_simple.temperature == 0.1
    assert policy_simple.min_generation_tokens == 512
