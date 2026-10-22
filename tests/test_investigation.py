"""
tests/test_investigation.py - Unit tests for InvestigationEngine and InvestigationSources.
"""
from __future__ import annotations

import pytest
from brain.investigation import (
    InvestigationEngine,
    InvestigationSource,
    ProjectFilesystemSource,
)
from brain.learning_records import EvidenceKind, EvidenceRecord


class MockSource(InvestigationSource):
    def __init__(self, name: str, evidence_list: list[EvidenceRecord]):
        self._name = name
        self._evidence_list = evidence_list

    @property
    def name(self) -> str:
        return self._name

    def query(self, topic: str, context: dict) -> list[EvidenceRecord]:
        return self._evidence_list


def test_investigation_engine_sufficiency_check():
    ev_matching = EvidenceRecord(
        kind=EvidenceKind.DOCUMENTATION,
        source="doc:blender_api",
        claim="primitive_cube_add function",
        evidence_text="bpy.ops.mesh.primitive_cube_add creates a cube",
        model_confidence=0.9,
        verified=True,
        app_context="Blender",
        app_version="4.3",
    )
    mock_src = MockSource("mock", [ev_matching])
    engine = InvestigationEngine(sources=[mock_src])

    status, items = engine.investigate("Blender cube creation", {"app": "Blender", "app_version": "4.3"})
    assert status == "HAS_SUFFICIENT_EVIDENCE"
    assert len(items) == 1
    assert items[0].source == "doc:blender_api"


def test_investigation_engine_version_mismatch_needs_investigation():
    ev_old_ver = EvidenceRecord(
        kind=EvidenceKind.DOCUMENTATION,
        source="doc:blender_old",
        claim="primitive_seat_add function",
        evidence_text="bpy.ops.mesh.primitive_seat_add creates a seat",
        model_confidence=0.9,
        verified=True,
        app_context="Blender",
        app_version="2.79", # Different version
    )
    mock_src = MockSource("mock", [ev_old_ver])
    engine = InvestigationEngine(sources=[mock_src])

    status, items = engine.investigate("Blender seat creation", {"app": "Blender", "app_version": "4.3"})
    assert status == "NEEDS_INVESTIGATION"
