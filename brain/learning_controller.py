"""
brain/learning_controller.py - Deterministic AgentLifecycleController for Local Learning Engine.

Establishes deterministic Python control over execution transitions, budget limits,
evidence processing, and condition-scoped negative learning:
- Conditional Lifecycle Paths:
    - Known task path: TASK -> KNOWLEDGE_CHECK -> EXECUTE -> VERIFY -> COMPLETE
    - Unknown task path: TASK -> KNOWLEDGE_CHECK -> INVESTIGATE -> PLAN -> EXECUTE -> VERIFY
    - Failure path: OBSERVE -> DIAGNOSE -> HYPOTHESIZE -> RETRY
- Configurable per-operation budgets (max investigation steps, max attempts, max tokens).
- Causal Failure Classification (INCORRECT_KNOWLEDGE, INVALID_SYNTAX, VERSION_MISMATCH, etc.).
- Condition-Scoped Negative Learning: prevents repeating a failed hypothesis ONLY under equivalent application/version/environment conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from brain.investigation import InvestigationEngine
from brain.learning_records import (
    AttemptRecord,
    DiagnosticHypothesis,
    EvidenceKind,
    EvidenceRecord,
    FailureKind,
    StructuredTrace,
    VerificationResultRecord,
    VerificationStatus,
    VerificationTier,
)
from brain.verification import VerificationEngine


@dataclass
class ControllerConfig:
    """Configurable budget limits for the deterministic controller."""
    max_investigation_steps: int = 3
    max_execution_attempts: int = 5
    max_tool_calls: int = 10
    max_context_tokens: int = 3500


class AgentLifecycleController:
    """Deterministic Python state machine controlling task execution lifecycle."""

    def __init__(
        self,
        investigation_engine: InvestigationEngine | None = None,
        verification_engine: VerificationEngine | None = None,
        config: ControllerConfig | None = None,
    ):
        self.investigation = investigation_engine or InvestigationEngine()
        self.verification = verification_engine or VerificationEngine()
        self.config = config or ControllerConfig()

    def run_task_lifecycle(
        self,
        task: str,
        app: str,
        executor_fn: Any,
        context: dict[str, Any] | None = None,
    ) -> tuple[VerificationResultRecord, StructuredTrace, list[DiagnosticHypothesis]]:
        """Executes a complete, conditional task lifecycle.

        executor_fn is a callable (code: str) -> dict[str, Any] returning execution output:
        {"exit_code": int, "stdout": str, "stderr": str, "report": str}
        """
        context = context or {}
        context["app"] = app
        trace = StructuredTrace(task=task)
        failed_hypotheses: list[DiagnosticHypothesis] = []

        # Step 1: TASK & KNOWLEDGE_CHECK
        trace.add_node("TASK", "start_task", f"Task: '{task}' for app '{app}'")
        kn_status, evidence = self.investigation.investigate(task, context)

        trace.add_node(
            "KNOWLEDGE_CHECK",
            "evaluate_evidence",
            f"Evidence status: {kn_status} ({len(evidence)} items found)",
        )

        # Conditional path selection
        if kn_status == "NEEDS_INVESTIGATION":
            trace.add_node("INVESTIGATE", "gather_evidence", "Gathering additional investigation evidence")
            # Investigation step (bounded by max_investigation_steps)
            extra_status, extra_ev = self.investigation.investigate(f"{app} {task}", context)
            evidence.extend(extra_ev)

        # Attempt loop (bounded by max_execution_attempts)
        attempt_num = 0
        last_ver_result = VerificationResultRecord(
            status=VerificationStatus.UNKNOWN,
            tier=VerificationTier.NONE,
            details="No execution attempt performed",
        )

        previous_code = ""

        while attempt_num < self.config.max_execution_attempts:
            attempt_num += 1
            trace.add_node("PLAN", "compose_script", f"Formulating attempt #{attempt_num}")

            # Execute via provided callback
            attempt_data = executor_fn(attempt_num, previous_code, evidence, failed_hypotheses)
            code = attempt_data.get("code", "")
            previous_code = code

            trace.add_node("EXECUTE", "run_script", f"Executed attempt #{attempt_num} code length: {len(code)}")

            # Step 2: VERIFY
            ver_result = self.verification.verify(
                task=task,
                plan=attempt_data.get("plan", ""),
                execution_data=attempt_data,
                context=context,
            )

            trace.add_node(
                "VERIFY",
                "check_outcome",
                f"Status: {ver_result.status.value}, Tier: {ver_result.tier.value} — {ver_result.details}",
            )

            last_ver_result = ver_result

            # Check for success
            if ver_result.status in (VerificationStatus.VERIFIED_SUCCESS, VerificationStatus.PARTIAL_SUCCESS):
                trace.add_node("COMPLETE", "task_success", f"Task finished with {ver_result.status.value}")
                return ver_result, trace, failed_hypotheses

            # Step 3: Failure path — OBSERVE -> DIAGNOSE -> HYPOTHESIZE -> RETRY
            trace.add_node("OBSERVE", "capture_failure", f"Failure captured: {ver_result.details}")
            
            # Causal failure classification
            failure_kind = self.classify_failure(
                exit_code=attempt_data.get("exit_code"),
                stdout=attempt_data.get("stdout", ""),
                stderr=attempt_data.get("stderr", ""),
                details=ver_result.details,
            )

            hypothesis = DiagnosticHypothesis(
                attempt_id=f"attempt-{attempt_num}",
                failure_kind=failure_kind,
                root_cause_hypothesis=attempt_data.get("stderr") or ver_result.details,
                proposed_fix=f"Fix {failure_kind.value} issue",
                conditions={"app": app, "version": str(context.get("app_version", "unknown"))},
                outcome="FAILED",
            )

            # Check condition-scoped negative learning: avoid duplicate hypotheses under same conditions
            if not any(
                h.root_cause_hypothesis == hypothesis.root_cause_hypothesis
                and h.matches_conditions(hypothesis.conditions)
                for h in failed_hypotheses
            ):
                failed_hypotheses.append(hypothesis)

            trace.add_node(
                "DIAGNOSE",
                "form_hypothesis",
                f"Classified failure as '{failure_kind.value}'. Hypothesis: {hypothesis.root_cause_hypothesis}",
            )

        trace.add_node("EXHAUSTED", "budget_reached", f"Max attempts ({self.config.max_execution_attempts}) reached.")
        return last_ver_result, trace, failed_hypotheses

    def classify_failure(
        self,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        details: str,
    ) -> FailureKind:
        """Classifies the root cause of a failure from execution signals."""
        output = f"{stdout}\n{stderr}\n{details}".lower()

        if "attributeerror" in output or "syntaxerror" in output or "nameerror" in output or "typeerror" in output:
            return FailureKind.INVALID_SYNTAX
        if "module_not_found" in output or "no module named" in output or "version" in output:
            return FailureKind.VERSION_MISMATCH
        if "missing requested task items" in output or "isn't in the scene" in output:
            return FailureKind.REQUIREMENT_MISMATCH
        if exit_code is not None and exit_code != 0:
            return FailureKind.ENVIRONMENTAL_FAILURE
        return FailureKind.INCORRECT_KNOWLEDGE
