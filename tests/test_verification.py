"""
tests/test_verification.py - Unit tests for VerificationEngine tiers and outcome statuses.
"""
from __future__ import annotations

import pytest
from brain.verification import VerificationEngine, VerificationStatus, VerificationTier


def test_verification_tier1_execution_failure():
    ve = VerificationEngine()
    res = ve.verify(
        task="Make a Chair",
        plan="- 1 chair",
        execution_data={"exit_code": 1, "stdout": "", "stderr": "Error: invalid syntax"},
    )
    assert res.status == VerificationStatus.VERIFIED_FAILURE
    assert res.tier == VerificationTier.EXECUTION_SUCCESS
    assert "non-zero code 1" in res.details


def test_verification_tier2_zero_volume_failure():
    ve = VerificationEngine()
    report = "MBUDDY_STATE: Seat (0.00, 2.00, 2.00) at (0.00, 0.00, 0.00)"
    res = ve.verify(
        task="Make a Chair",
        plan="- 1 seat",
        execution_data={"exit_code": 0, "stdout": report, "stderr": "", "report": report},
    )
    assert res.status == VerificationStatus.VERIFIED_FAILURE
    assert res.tier == VerificationTier.APPLICATION_STATE_VERIFIED
    assert "collapsed zero-volume" in res.details


def test_verification_tier3_task_requirements_success():
    ve = VerificationEngine()
    report = (
        "MBUDDY_STATE: Seat (1.00, 1.00, 0.20) at (0.00, 0.00, 1.00)\n"
        "MBUDDY_STATE: Leg (0.10, 0.10, 1.00) at (0.50, 0.50, 0.50)"
    )
    res = ve.verify(
        task="Make a Chair",
        plan="- 1 seat\n- 1 leg",
        execution_data={"exit_code": 0, "stdout": report, "stderr": "", "report": report},
    )
    assert res.status == VerificationStatus.VERIFIED_SUCCESS
    assert res.tier == VerificationTier.TASK_REQUIREMENTS_VERIFIED
    assert len(res.verified_elements) == 2


def test_verification_tier3_missing_requirements():
    ve = VerificationEngine()
    report = "MBUDDY_STATE: Seat (1.00, 1.00, 0.20) at (0.00, 0.00, 1.00)"
    res = ve.verify(
        task="Make a Chair",
        plan="- 1 seat\n- 4 leg",
        execution_data={"exit_code": 0, "stdout": report, "stderr": "", "report": report},
    )
    assert res.status == VerificationStatus.PARTIAL_SUCCESS
    assert res.tier == VerificationTier.TASK_REQUIREMENTS_VERIFIED
    assert len(res.missing_requirements) == 1
    assert "leg" in res.missing_requirements[0]


def test_dimension_lines_in_a_plan_are_not_parts_to_find():
    ve = VerificationEngine()
    report = "MBUDDY_STATE: Top (1.00, 0.50, 0.05) at (0.00, 0.00, 0.80)"
    res = ve.verify(task="table", plan="- 1 top\n- 600 mm\n- 0.6 m tall",
                    execution_data={"exit_code": 0, "stdout": report, "stderr": "", "report": report})
    assert "600" not in (res.details or "") and not any("600" in m for m in res.missing_requirements)


def test_a_real_blender_report_with_camera_lights_and_floor_passes():
    # live 2026-10-20: with real sizes, the camera read as 0 x 0 x 0 and 'camera CAMERA' as one name, so a good
    # chair failed six tries as 'collapsed zero-volume geometry'
    legs = "\n".join(f"MBUDDY_STATE: Leg{i} MESH (0.040, 0.040, 0.430) at ({x}, {y}, 0.215) rot (0.0, 0.0, 0.0)"
                     for i, (x, y) in enumerate([(-0.2, -0.2), (0.2, -0.2), (-0.2, 0.2), (0.2, 0.2)]))
    report = ("MBUDDY_STATE: Camera CAMERA (0.000, 0.000, 0.000) at (7.360, -6.930, 4.960) rot (63.6, 0.0, 46.7)\n"
              "MBUDDY_STATE: Camera.001 CAMERA (0.000, 0.000, 0.000) at (0.000, -4.000, 1.000) rot (80.0, 0.0, 0.0)\n"
              "MBUDDY_STATE: Light LIGHT (0.000, 0.000, 0.000) at (4.080, 1.000, 5.900) rot (37.3, 3.2, 106.9)\n"
              "MBUDDY_STATE: Floor MESH (10.000, 10.000, 0.000) at (0.000, 0.000, 0.000) rot (0.0, 0.0, 0.0)\n"
              "MBUDDY_STATE: Seat MESH (0.450, 0.450, 0.040) at (0.000, 0.000, 0.450) rot (0.0, 0.0, 0.0)\n" + legs)
    res = VerificationEngine().verify(task="a chair with four legs", plan="- Seat: a box\n- Legs: four cylinders",
                                      execution_data={"exit_code": 0, "stdout": report, "stderr": "", "report": report})
    assert res.status != VerificationStatus.VERIFIED_FAILURE, res.details
    flat = report.replace("Seat MESH (0.450, 0.450, 0.040)", "Seat MESH (0.450, 0.450, 0.000)")
    res = VerificationEngine().verify(task="a chair", plan="- Seat", execution_data={"exit_code": 0, "stdout": flat, "report": flat})
    assert res.status == VerificationStatus.VERIFIED_FAILURE and "seat" in res.details.lower()


def test_a_chair_and_a_table_are_each_checked_on_their_own_legs():
    # live: the table's legs (around x=1) were judged against the chair's seat (at 0,0) and failed every try
    def legs(prefix, cx, cy, h, half):
        return "\n".join(f"MBUDDY_STATE: {prefix}{i} MESH (0.050, 0.050, {h:.3f}) at ({cx + dx:.3f}, {cy + dy:.3f}, {h / 2:.3f})"
                         for i, (dx, dy) in enumerate([(-half, -half), (half, -half), (-half, half), (half, half)]))
    report = ("MBUDDY_STATE: Seat MESH (0.500, 0.500, 0.050) at (0.000, 0.000, 0.475)\n" + legs("LegChair", 0, 0, 0.45, 0.2)
              + "\nMBUDDY_STATE: TableTop MESH (0.800, 0.600, 0.040) at (1.200, 0.000, 0.720)\n" + legs("LegTable", 1.2, 0, 0.70, 0.3))
    res = VerificationEngine().verify(task="add a small table next to the chair", plan="- TableTop\n- Legs",
                                      execution_data={"exit_code": 0, "stdout": report, "report": report})
    assert res.status != VerificationStatus.VERIFIED_FAILURE, res.details
