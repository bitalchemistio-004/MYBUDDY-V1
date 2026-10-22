"""
tests/test_learning_controller.py - Unit tests for AgentLifecycleController.
"""
from __future__ import annotations

import pytest
from brain.learning_controller import AgentLifecycleController, ControllerConfig
from brain.learning_records import FailureKind, VerificationStatus


def test_controller_known_task_success_path():
    controller = AgentLifecycleController(config=ControllerConfig(max_execution_attempts=3))

    def executor_callback(attempt_num, previous_code, evidence, failed_hypotheses):
        report = "MBUDDY_STATE: Seat (1.00, 1.00, 0.20) at (0.00, 0.00, 1.00)"
        return {
            "code": "import bpy; bpy.ops.mesh.primitive_cube_add()",
            "exit_code": 0,
            "stdout": report,
            "stderr": "",
            "report": report,
            "plan": "- 1 seat",
        }

    ver_res, trace, failed_hyps = controller.run_task_lifecycle(
        task="Make a Chair Seat",
        app="Blender",
        executor_fn=executor_callback,
        context={"app_version": "4.3"},
    )

    assert ver_res.status == VerificationStatus.VERIFIED_SUCCESS
    assert len(failed_hyps) == 0
    stages = [node.stage for node in trace.nodes]
    assert "TASK" in stages
    assert "KNOWLEDGE_CHECK" in stages
    assert "EXECUTE" in stages
    assert "VERIFY" in stages
    assert "COMPLETE" in stages


def test_controller_failure_retry_and_causal_classification():
    controller = AgentLifecycleController(config=ControllerConfig(max_execution_attempts=2))

    calls = 0

    def executor_callback(attempt_num, previous_code, evidence, failed_hypotheses):
        nonlocal calls
        calls += 1
        if attempt_num == 1:
            return {
                "code": "bpy.ops.mesh.primitive_seat_add()",
                "exit_code": 1,
                "stdout": "",
                "stderr": "AttributeError: 'module' object has no attribute 'primitive_seat_add'",
                "plan": "- 1 seat",
            }
        else:
            report = "MBUDDY_STATE: Seat (1.00, 1.00, 0.20) at (0.00, 0.00, 1.00)"
            return {
                "code": "bpy.ops.mesh.primitive_cube_add()",
                "exit_code": 0,
                "stdout": report,
                "stderr": "",
                "report": report,
                "plan": "- 1 seat",
            }

    ver_res, trace, failed_hyps = controller.run_task_lifecycle(
        task="Make a Chair Seat",
        app="Blender",
        executor_fn=executor_callback,
        context={"app_version": "4.3"},
    )

    assert calls == 2
    assert ver_res.status == VerificationStatus.VERIFIED_SUCCESS
    assert len(failed_hyps) == 1
    assert failed_hyps[0].failure_kind == FailureKind.INVALID_SYNTAX
    assert failed_hyps[0].conditions == {"app": "Blender", "version": "4.3"}
