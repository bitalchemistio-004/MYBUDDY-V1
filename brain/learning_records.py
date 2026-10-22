"""
brain/learning_records.py - Data Contracts & Provenance Model for Local Learning Engine.

Establishes stable, strongly-typed data contracts for:
- EvidenceRecord: evidence items with provenance, non-authoritative model confidence, and verification status.
- AttemptRecord: execution attempts and environment conditions.
- DiagnosticHypothesis: causal failure diagnosis and condition-scoped fix hypotheses.
- VerificationResultRecord: multi-tier task verification (EXECUTION_SUCCESS, APPLICATION_STATE_VERIFIED, TASK_REQUIREMENTS_VERIFIED).
- KnowledgeClaim: semantic knowledge claims with provenance, version bounds, and evidence links.
- ProceduralSkillRecord: parameterized, generalized procedural skills with version/application constraints.
- StructuredTrace: compact execution traces for UI/logging observability.

Preserves explicit distinction between model-reported confidence and objective evidence/verification.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


class EvidenceKind(str, Enum):
    OBSERVATION = "observation"       # Directly observed tool/app output
    USER_FACT = "user_fact"           # Explicitly provided by the user
    DOCUMENTATION = "documentation"   # Retrieved from documentation/reference
    EXPERIMENT = "experiment"         # Confirmed by running an experiment
    INFERENCE = "inference"           # Model-derived conclusion
    HYPOTHESIS = "hypothesis"         # Unverified explanation
    FAILURE = "failure"               # Evidence of an approach that failed


class VerificationTier(str, Enum):
    EXECUTION_SUCCESS = "execution_success"                   # Tier 1: Process exit code 0 / command ran
    APPLICATION_STATE_VERIFIED = "application_state_verified" # Tier 2: Output objects/state verified
    TASK_REQUIREMENTS_VERIFIED = "task_requirements_verified" # Tier 3: All user requirements satisfied
    NONE = "none"                                              # No verification performed


class VerificationStatus(str, Enum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    UNKNOWN = "UNKNOWN"


class FailureKind(str, Enum):
    INCORRECT_KNOWLEDGE = "incorrect_knowledge"
    INCORRECT_APPLICATION = "incorrect_application"
    INVALID_SYNTAX = "invalid_syntax"
    VERSION_MISMATCH = "version_mismatch"
    ENVIRONMENTAL_FAILURE = "environmental_failure"
    TOOL_FAILURE = "tool_failure"
    REQUIREMENT_MISMATCH = "requirement_mismatch"
    UNKNOWN_FAILURE = "unknown_failure"


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class EvidenceRecord:
    """A single piece of evidence with provenance.

    CRITICAL RULE: model_confidence is non-authoritative.
    True evidence quality is determined by kind, provenance, and verified flag.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: EvidenceKind = EvidenceKind.OBSERVATION
    source: str = ""                         # Provenance (e.g. "blender_stdout", "doc:blender_api", "vault:note_x")
    claim: str = ""                          # The assertion or factual statement
    evidence_text: str = ""                  # Raw supporting text or output log
    model_confidence: float = 0.5            # NON-AUTHORITATIVE model self-reported score (0.0 to 1.0)
    verified: bool = False                   # True if objectively verified by test/checker
    app_context: str | None = None           # e.g. "Blender"
    app_version: str | None = None           # e.g. "4.3"
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRecord:
        data = dict(data)
        if isinstance(data.get("kind"), str):
            data["kind"] = EvidenceKind(data["kind"])
        return cls(**data)


@dataclass
class VerificationResultRecord:
    """Result of verifying an execution attempt against task requirements.

    Hierarchical verification:
    - tier: strongest verification actually performed.
    - status: outcome of verification.
    """
    status: VerificationStatus = VerificationStatus.UNKNOWN
    tier: VerificationTier = VerificationTier.NONE
    details: str = ""
    missing_requirements: list[str] = field(default_factory=list)
    verified_elements: list[str] = field(default_factory=list)
    verifier_name: str = "default_verifier"
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["tier"] = self.tier.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationResultRecord:
        data = dict(data)
        if isinstance(data.get("status"), str):
            data["status"] = VerificationStatus(data["status"])
        if isinstance(data.get("tier"), str):
            data["tier"] = VerificationTier(data["tier"])
        return cls(**data)


@dataclass
class AttemptRecord:
    """Record of a single execution attempt within a task."""
    attempt_num: int
    task: str
    app: str
    code: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    environment_conditions: dict[str, str] = field(default_factory=dict) # e.g. {"app": "Blender", "version": "4.3"}
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttemptRecord:
        return cls(**data)


@dataclass
class DiagnosticHypothesis:
    """Hypothesis explaining a failure and proposing a condition-scoped fix.

    CONDITION-SCOPED NEGATIVE LEARNING:
    `conditions` store the relevant application, version, and environment.
    A failed hypothesis is avoided ONLY when identical relevant conditions apply.
    """
    attempt_id: str
    failure_kind: FailureKind
    root_cause_hypothesis: str
    proposed_fix: str
    fix_code: str = ""
    conditions: dict[str, str] = field(default_factory=dict) # e.g. {"app": "Blender", "version": "4.3"}
    supporting_evidence_ids: list[str] = field(default_factory=list)
    outcome: str | None = None                                # "SUCCESS", "FAILED", "UNTESTED"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_now_iso)

    def matches_conditions(self, current_conditions: dict[str, str]) -> bool:
        """Returns True if current conditions match the failure hypothesis conditions."""
        if not self.conditions:
            return True
        for k, v in self.conditions.items():
            if k in current_conditions and current_conditions[k] != v:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["failure_kind"] = self.failure_kind.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagnosticHypothesis:
        data = dict(data)
        if isinstance(data.get("failure_kind"), str):
            data["failure_kind"] = FailureKind(data["failure_kind"])
        return cls(**data)


@dataclass
class KnowledgeClaim:
    """A durable semantic knowledge claim derived from evidence and experiences."""
    topic: str
    claim: str
    status: str = "candidate"                  # "candidate", "validated", "archived", "conflict"
    evidence_ids: list[str] = field(default_factory=list)
    successful_uses: int = 0
    failed_uses: int = 0
    app_context: str | None = None
    app_version: str | None = None
    applicable_conditions: dict[str, str] = field(default_factory=dict)
    not_applicable_conditions: dict[str, str] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created: str = field(default_factory=_now_iso)
    last_verified: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeClaim:
        return cls(**data)


@dataclass
class ProceduralSkillRecord:
    """A generalized, reusable procedural skill (how to perform a task)."""
    title: str
    app: str
    summary: str
    procedure_steps: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)  # Variable parameters
    code_template: str = ""
    app_version_constraints: str = ""
    known_failure_modes: list[str] = field(default_factory=list)
    verified_uses: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProceduralSkillRecord:
        return cls(**data)


@dataclass
class StructuredTraceNode:
    """Node in an execution trace tree for observability."""
    stage: str                                 # e.g. "KNOWLEDGE_CHECK", "INVESTIGATE", "VERIFY"
    action: str
    details: str
    status: str = "OK"                         # "OK", "FAILED", "SKIPPED"
    timestamp: str = field(default_factory=_now_iso)
    sub_nodes: list[StructuredTraceNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sub_nodes"] = [child.to_dict() for child in self.sub_nodes]
        return d


@dataclass
class StructuredTrace:
    """Root trace tree representing a task's full execution lifecycle."""
    task: str
    nodes: list[StructuredTraceNode] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: str = field(default_factory=_now_iso)
    end_time: str | None = None

    def add_node(self, stage: str, action: str, details: str, status: str = "OK") -> StructuredTraceNode:
        node = StructuredTraceNode(stage=stage, action=action, details=details, status=status)
        self.nodes.append(node)
        return node

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "nodes": [n.to_dict() for n in self.nodes],
        }
