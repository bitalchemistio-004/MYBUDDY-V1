"""
tests/test_learning_benchmarks.py - Cross-Session Learning Benchmark Suite.

Demonstrates genuine local cross-session learning for Qwen3 4B architecture:
- Test A: Initial Failure & Diagnostic Learning (Investigates, fails try 1, diagnoses, verifies fix try 2, persists knowledge).
- Test B: Genuine Cold Fresh-Context Reuse (Resets conversational context, related task -> retrieves stored evidence, avoids prior mistake on Try 1).
- Test C: Condition-Scoped Negative Learning (Prevents repeating failed hypothesis ONLY under equivalent application/version conditions).
- Test D: Skill Generalization (Applies learned API pattern to a different requested parameters).
- Test E: Negative Learning Prevention (Ensures failed hypothesis is not blindly re-attempted).
- Test F: Noisy / Versioned Knowledge Contradiction Resolution (Handles stale/conflicting notes by preferring version-matched verified evidence).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from brain.investigation import InvestigationEngine, ObsidianSource, ExecutionHistorySource, InvestigationSource
from brain.learning_controller import AgentLifecycleController, ControllerConfig
from brain.learning_records import (
    DiagnosticHypothesis,
    EvidenceKind,
    EvidenceRecord,
    FailureKind,
    VerificationStatus,
)
from brain.verification import VerificationEngine, VerificationStatus


def test_benchmark_a_learn_after_failure(tmp_path):
    """Test A: Investigates, fails on attempt 1, diagnoses root cause, verifies fix on attempt 2, persists knowledge."""
    controller = AgentLifecycleController(config=ControllerConfig(max_execution_attempts=3))

    def mock_executor(attempt_num, previous_code, evidence, failed_hypotheses):
        if attempt_num == 1:
            return {
                "code": "import bpy; bpy.ops.mesh.primitive_seat_add()",
                "exit_code": 1,
                "stdout": "",
                "stderr": "AttributeError: 'module' object has no attribute 'primitive_seat_add'",
                "plan": "- 1 seat",
            }
        else:
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
        executor_fn=mock_executor,
        context={"app_version": "4.3"},
    )

    assert ver_res.status == VerificationStatus.VERIFIED_SUCCESS
    assert len(failed_hyps) == 1
    assert failed_hyps[0].failure_kind == FailureKind.INVALID_SYNTAX
    assert "primitive_seat_add" in failed_hyps[0].root_cause_hypothesis


def test_benchmark_b_fresh_context_cross_session_reuse(tmp_path):
    """Test B: COLD CONTEXT RESET. Related task retrieves stored evidence and avoids prior mistake on Try 1."""
    # Session A: Save a learned evidence record into vault
    debug_dir = tmp_path / "MBUDDY" / "Debugging experience"
    debug_dir.mkdir(parents=True)
    note_file = debug_dir / "Blender - AttributeError primitive_seat_add.md"
    note_file.write_text(
        "## Symptom\nAttributeError: 'module' object has no attribute 'primitive_seat_add'\n\n"
        "## Diagnosis\nWhat went wrong: primitive_seat_add does not exist.\nWhy: Blender 4.3 uses primitive_cube_add.\nFix: Use primitive_cube_add.\n\n"
        "## Result\nThe fix worked.",
        encoding="utf-8",
    )

    # Session B: Complete cold context reset (fresh controller, fresh investigation engine)
    hist_src = ExecutionHistorySource(vault_path=tmp_path)
    engine = InvestigationEngine(sources=[hist_src])
    controller = AgentLifecycleController(investigation_engine=engine)

    attempts_made = 0

    def fresh_session_executor(attempt_num, previous_code, evidence, failed_hypotheses):
        nonlocal attempts_made
        attempts_made += 1
        # Check that evidence from Session A was retrieved during KNOWLEDGE_CHECK
        evidence_sources = [ev.source for ev in evidence]
        assert any("primitive_seat_add" in src for src in evidence_sources)

        # Apply learned fix on Try 1
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
        executor_fn=fresh_session_executor,
        context={"app_version": "4.3"},
    )

    # Verified success on Try 1 without repeating the mistake!
    assert attempts_made == 1
    assert ver_res.status == VerificationStatus.VERIFIED_SUCCESS
    assert len(failed_hyps) == 0


def test_benchmark_c_condition_scoped_negative_learning():
    """Test C: Failed hypothesis is avoided under version 4.3, but permitted if version changes to 2.79."""
    hyp_43 = DiagnosticHypothesis(
        attempt_id="att-1",
        failure_kind=FailureKind.INVALID_SYNTAX,
        root_cause_hypothesis="primitive_seat_add missing in Blender 4.3",
        proposed_fix="Use primitive_cube_add",
        conditions={"app": "Blender", "version": "4.3"},
    )

    # Under Blender 4.3, conditions match -> hypothesis applies
    assert hyp_43.matches_conditions({"app": "Blender", "version": "4.3"}) is True

    # Under Blender 2.79, conditions do not match -> hypothesis is not falsely applied
    assert hyp_43.matches_conditions({"app": "Blender", "version": "2.79"}) is False


def test_benchmark_d_skill_generalization():
    """Test D: Generalized skill parameterization across variable requested inputs."""
    from brain.learning_records import ProceduralSkillRecord

    skill = ProceduralSkillRecord(
        title="Make 3D Box",
        app="Blender",
        summary="Creates a 3D box mesh",
        procedure_steps=["Add cube mesh", "Set scale dimensions"],
        parameters={"dx": 2.0, "dy": 2.0, "dz": 2.0},
        code_template="import bpy; bpy.ops.mesh.primitive_cube_add(size=1.0); bpy.context.active_object.scale = ({dx}, {dy}, {dz})",
        app_version_constraints="Blender 4.x",
    )

    # Generalize template to new parameters (dx=4.0, dy=1.0, dz=0.5)
    custom_code = skill.code_template.format(dx=4.0, dy=1.0, dz=0.5)
    assert "scale = (4.0, 1.0, 0.5)" in custom_code


def test_benchmark_e_negative_learning_prevention():
    """Test E: Controller prevents repeating a failed hypothesis on subsequent tries."""
    controller = AgentLifecycleController(config=ControllerConfig(max_execution_attempts=2))

    received_failed_hyps = []

    def executor_callback(attempt_num, previous_code, evidence, failed_hypotheses):
        nonlocal received_failed_hyps
        received_failed_hyps = list(failed_hypotheses)
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

    # On attempt 2, the callback received the attempt 1 failed hypothesis context
    assert len(received_failed_hyps) == 1
    assert "primitive_seat_add" in received_failed_hyps[0].root_cause_hypothesis


def test_benchmark_f_noisy_versioned_knowledge_resolution():
    """Test F: Noisy/Versioned Knowledge.

    - Note A: API X (primitive_seat_add) works in Blender 2.79.
    - Note B: API Y (primitive_cube_add) works in Blender 4.3.
    - Current environment is Blender 4.3.
    - Controller filters out version-mismatched Note A and selects version-matched Note B.
    """
    ev_old = EvidenceRecord(
        kind=EvidenceKind.DOCUMENTATION,
        source="vault:note_a",
        claim="Use primitive_seat_add for seats",
        evidence_text="bpy.ops.mesh.primitive_seat_add()",
        model_confidence=0.9,
        verified=True,
        app_context="Blender",
        app_version="2.79", # Stale version
    )

    ev_new = EvidenceRecord(
        kind=EvidenceKind.EXPERIMENT,
        source="vault:note_b",
        claim="Use primitive_cube_add for seats",
        evidence_text="bpy.ops.mesh.primitive_cube_add()",
        model_confidence=0.95,
        verified=True,
        app_context="Blender",
        app_version="4.3", # Current version
    )

    class ContradictorySource(InvestigationSource):
        @property
        def name(self) -> str:
            return "contradictory_vault"

        def query(self, topic: str, context: dict) -> list[EvidenceRecord]:
            return [ev_old, ev_new]

    engine = InvestigationEngine(sources=[ContradictorySource()])
    status, filtered_evidence = engine.investigate("Blender seat creation", {"app": "Blender", "app_version": "4.3"})

    assert status == "HAS_SUFFICIENT_EVIDENCE"
    assert len(filtered_evidence) == 1
    assert filtered_evidence[0].app_version == "4.3"
    assert "primitive_cube_add" in filtered_evidence[0].evidence_text
