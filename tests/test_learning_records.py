"""
tests/test_learning_records.py - Unit tests for Phase 2 data contracts and provenance model.

Tests:
- Serialization & deserialization roundtrips.
- Non-authoritative model_confidence behavior vs verified flag.
- Hierarchical verification tier classification.
- DiagnosticHypothesis condition matching for condition-scoped negative learning.
- StructuredTrace tree generation.
"""
from __future__ import annotations

import pytest
from brain.learning_records import (
    EvidenceKind,
    EvidenceRecord,
    VerificationTier,
    VerificationStatus,
    VerificationResultRecord,
    AttemptRecord,
    FailureKind,
    DiagnosticHypothesis,
    KnowledgeClaim,
    ProceduralSkillRecord,
    StructuredTrace,
    StructuredTraceNode,
)


def test_evidence_record_provenance_and_serialization():
    ev = EvidenceRecord(
        kind=EvidenceKind.EXPERIMENT,
        source="blender_stdout",
        claim="primitive_cube_add creates a cube mesh",
        evidence_text="MBUDDY_STATE: Cube (2.00, 2.00, 2.00) at (0.00, 0.00, 0.00)",
        model_confidence=0.95,
        verified=True,
        app_context="Blender",
        app_version="4.3",
    )
    assert ev.kind == EvidenceKind.EXPERIMENT
    assert ev.verified is True
    assert ev.app_version == "4.3"

    # Serialization roundtrip
    d = ev.to_dict()
    assert d["kind"] == "experiment"
    assert d["verified"] is True

    reconstituted = EvidenceRecord.from_dict(d)
    assert reconstituted.kind == EvidenceKind.EXPERIMENT
    assert reconstituted.source == ev.source
    assert reconstituted.claim == ev.claim
    assert reconstituted.verified is True


def test_verification_result_record_tiers():
    res = VerificationResultRecord(
        status=VerificationStatus.VERIFIED_SUCCESS,
        tier=VerificationTier.APPLICATION_STATE_VERIFIED,
        details="Cube object volume verified (dx=2.00, dy=2.00, dz=2.00)",
        verified_elements=["Cube"],
    )
    assert res.status == VerificationStatus.VERIFIED_SUCCESS
    assert res.tier == VerificationTier.APPLICATION_STATE_VERIFIED

    d = res.to_dict()
    assert d["status"] == "VERIFIED_SUCCESS"
    assert d["tier"] == "application_state_verified"

    reconstituted = VerificationResultRecord.from_dict(d)
    assert reconstituted.status == VerificationStatus.VERIFIED_SUCCESS
    assert reconstituted.tier == VerificationTier.APPLICATION_STATE_VERIFIED


def test_diagnostic_hypothesis_condition_matching():
    hyp = DiagnosticHypothesis(
        attempt_id="att-123",
        failure_kind=FailureKind.INVALID_SYNTAX,
        root_cause_hypothesis="primitive_seat_add function does not exist in bpy.ops.mesh in Blender 4.3",
        proposed_fix="Use bpy.ops.mesh.primitive_cube_add instead",
        conditions={"app": "Blender", "version": "4.3"},
    )
    
    # Matches identical version 4.3
    assert hyp.matches_conditions({"app": "Blender", "version": "4.3"}) is True
    # Does NOT match version 3.6 where primitive_seat_add might exist
    assert hyp.matches_conditions({"app": "Blender", "version": "3.6"}) is False
    # Matches when condition key is omitted (wildcard match)
    assert hyp.matches_conditions({"app": "Blender"}) is True


def test_structured_trace_tree():
    trace = StructuredTrace(task="Make a 3D Chair in Blender")
    trace.add_node("KNOWLEDGE_CHECK", "query_vault", "Found 2 Blender notes", status="OK")
    trace.add_node("EXECUTE", "run_script", "Attempt 1 failed with AttributeError", status="FAILED")
    
    d = trace.to_dict()
    assert d["task"] == "Make a 3D Chair in Blender"
    assert len(d["nodes"]) == 2
    assert d["nodes"][0]["stage"] == "KNOWLEDGE_CHECK"
    assert d["nodes"][1]["status"] == "FAILED"


def test_knowledge_claim_and_skill_records():
    claim = KnowledgeClaim(
        topic="Blender Mesh Primitives",
        claim="bpy.ops.mesh.primitive_cube_add creates a cube",
        status="validated",
        app_context="Blender",
        app_version="4.3",
    )
    assert claim.topic == "Blender Mesh Primitives"

    skill = ProceduralSkillRecord(
        title="Make 3D Chair",
        app="Blender",
        summary="Creates a 3D chair using cubes",
        procedure_steps=["Create seat", "Create backrest", "Create 4 legs"],
        app_version_constraints="Blender 4.x",
    )
    assert len(skill.procedure_steps) == 3
    assert skill.app_version_constraints == "Blender 4.x"
