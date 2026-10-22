"""
Comprehensive Unit & Integration Test Suite for MBUDDY Model-Capability-Aware Architecture.
"""

import pytest
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
from brain.connectors.app_driver import unsafe_python
from brain.verification import VerificationEngine, VerificationStatus


def test_detect_model_capabilities_fallback():
    caps = detect_model_capabilities("unknown-model-v1")
    assert caps.context_window >= 8192
    assert caps.max_generation_tokens >= 2048
    assert caps.capability_status == CapabilityStatus.FALLBACK

    qwen_caps = detect_model_capabilities("qwen3:4b")
    assert qwen_caps.supports_thinking is True


def test_derive_execution_policy():
    caps = ModelCapabilities(model_name="qwen3:4b", context_window=8192, supports_thinking=True)

    # Simple task -> think = False
    simple_policy = derive_execution_policy(caps, TaskComplexity.SIMPLE)
    assert simple_policy.enable_thinking is False
    assert simple_policy.min_generation_tokens == 512

    # Complex task -> think = True
    complex_policy = derive_execution_policy(caps, TaskComplexity.COMPLEX)
    assert complex_policy.enable_thinking is True
    assert complex_policy.max_generation_tokens == caps.max_generation_tokens

    # Debugging task -> think = True
    debug_policy = derive_execution_policy(caps, TaskComplexity.DEBUGGING, is_retry=True)
    assert debug_policy.enable_thinking is True
    assert debug_policy.min_generation_tokens >= 1024


def test_preserve_structured_script():
    # Long script simulation
    long_script = "import bpy\n" + "\n".join(f"# Line {i}\nprint({i})" for i in range(150)) + "\n# Failing target\nx = 1 / 0\n"
    preserved = preserve_structured_script(long_script, max_tokens=300, failing_line="x = 1 / 0")

    assert "import bpy" in preserved
    assert "x = 1 / 0" in preserved
    assert estimate_tokens(preserved) <= 400


def test_preserve_structured_error():
    long_error = (
        "Traceback (most recent call last):\n"
        + "\n".join(f'  File "test_{i}.py", line {i}, in func_{i}\n    do_something({i})' for i in range(100))
        + '\n  File "run_app.py", line 120, in execute\n    raise ValueError("Target object is zero volume")\n'
        + "ValueError: Target object is zero volume"
    )
    preserved = preserve_structured_error(long_error, max_tokens=300)

    assert "Traceback" in preserved
    assert "ValueError" in preserved


def test_priority_context_builder_p0_protection():
    caps = ModelCapabilities(model_name="test", context_window=2000)
    policy = derive_execution_policy(caps, TaskComplexity.MODERATE)
    builder = PriorityContextBuilder(caps, policy)

    # P0 (Critical)
    builder.add_item(0, "task", "Build a detailed 2-story house with garage")
    builder.add_item(0, "failing_code", "import bpy\n" + "# code\n" * 100)

    # P3 (Optional)
    builder.add_item(3, "verbose_notes", "Some very long background note " * 200)

    assembled, report, gen_budget = builder.build()

    assert "Build a detailed 2-story house" in assembled
    assert report.context_pressure in ("NONE", "LIGHT", "MODERATE", "SEVERE")
    assert any("task" in item for item in report.included_priorities)
    # Ensure generation budget headroom is preserved
    assert gen_budget >= policy.min_generation_tokens


def test_security_sandboxing_integrity():
    # Dangerous scripts must be rejected regardless of architecture or size
    dangerous_code_1 = "import os\nos.system('dir')"
    dangerous_code_2 = "import subprocess\nsubprocess.Popen(['calc.exe'])"
    dangerous_code_3 = "eval('__import__(\"os\").system(\"dir\")')"

    assert "os" in unsafe_python(dangerous_code_1)
    assert "subprocess" in unsafe_python(dangerous_code_2)
    assert unsafe_python(dangerous_code_3) != ""


def test_verification_outcome_fidelity_integration():
    # Execution succeeded (exit code 0), but scene has zero volume -> MUST fail verification
    ver_engine = VerificationEngine()
    empty_report = "MBUDDY_STATE: House (0.00, 0.00, 0.00) at (0.00, 0.00, 0.00)"

    res = ver_engine.verify(
        task="Build a 2-story villa",
        plan="First floor walls, second floor walls, roof",
        execution_data={"exit_code": 0, "stdout": empty_report, "stderr": "", "report": empty_report},
    )

    assert res.status == VerificationStatus.VERIFIED_FAILURE
    assert "collapsed or flat" in res.details.lower() or "volume" in res.details.lower()
