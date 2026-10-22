"""
Deep Learning Test: Pushing the LLM to learn from Spatial Constraints (Phase 3).

This test simulates the LLM making a structural error (a table top floating in the air)
that passes simple coverage but fails the new VerificationEngine spatial constraints.
The system generates a DiagnosticHypothesis (via remember_debugging), and then
the LLM corrects the height. Finally, a Skill note is synthesized.
"""

import tempfile
import pytest
from pathlib import Path

from brain.obsidian_vault import Vault
from brain.skill_synthesizer import SkillSynthesizer
from brain.connectors import app_driver
from brain.verification import VerificationEngine, VerificationStatus

def test_deep_learning_spatial_constraint(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        vault = Vault(tmp_dir)
        vault.init()
        # debugging notes go to this vault, not the user's (each run used to add to real lesson notes)
        from brain import brain_store
        monkeypatch.setattr(brain_store, "get_vault", lambda: vault)
        synthesizer = SkillSynthesizer(vault=vault)
        ve = VerificationEngine()

        app_name = "Blender 4.3"
        task_desc = "make a table"
        plan = "- 1 table\n- 4 leg"

        # --- Turn 1: LLM generates a floating table ---
        # The tabletop z_min will be 2.0 (z=2.1, dz=0.2)
        # The leg z_max will be 1.0 (z=0.5, dz=1.0)
        # 2.0 > 1.0 + 0.15 => Spatial error!
        report_turn_1 = (
            "MBUDDY_STATE: Table (2.00, 2.00, 0.20) at (0.00, 0.00, 2.10)\n"
            "MBUDDY_STATE: Leg1 (0.20, 0.20, 1.00) at (0.90, 0.90, 0.50)\n"
            "MBUDDY_STATE: Leg2 (0.20, 0.20, 1.00) at (-0.90, 0.90, 0.50)\n"
            "MBUDDY_STATE: Leg3 (0.20, 0.20, 1.00) at (0.90, -0.90, 0.50)\n"
            "MBUDDY_STATE: Leg4 (0.20, 0.20, 1.00) at (-0.90, -0.90, 0.50)"
        )
        
        # Verify using the VerificationEngine
        res = ve.verify(task=task_desc, plan=plan, execution_data={"exit_code": 0, "stdout": report_turn_1, "report": report_turn_1})
        assert res.status == VerificationStatus.VERIFIED_FAILURE
        assert "not resting properly" in res.details
        
        # Diagnose and record the negative learning
        diagnosis = (
            "What went wrong: The table top is floating in the air.\n"
            "Why: Table z_min is 2.00 but leg z_max is 1.00.\n"
            "Fix: Lower the table top Z location to 1.10."
        )
        app_driver.remember_debugging(
            name=app_name,
            error=res.details,
            diagnosis=diagnosis,
            fixed=False
        )

        # --- Turn 2: LLM learns and corrects the height ---
        # Table top is moved to Z=1.10. z_min = 1.10 - 0.10 = 1.00.
        # Leg z_max is still 1.00. They rest perfectly.
        report_turn_2 = (
            "MBUDDY_STATE: Table (2.00, 2.00, 0.20) at (0.00, 0.00, 1.10)\n"
            "MBUDDY_STATE: Leg1 (0.20, 0.20, 1.00) at (0.90, 0.90, 0.50)\n"
            "MBUDDY_STATE: Leg2 (0.20, 0.20, 1.00) at (-0.90, 0.90, 0.50)\n"
            "MBUDDY_STATE: Leg3 (0.20, 0.20, 1.00) at (0.90, -0.90, 0.50)\n"
            "MBUDDY_STATE: Leg4 (0.20, 0.20, 1.00) at (-0.90, -0.90, 0.50)"
        )

        res2 = ve.verify(task=task_desc, plan=plan, execution_data={"exit_code": 0, "stdout": report_turn_2, "report": report_turn_2})
        assert res2.status == VerificationStatus.VERIFIED_SUCCESS

        app_driver.remember_debugging(
            name=app_name,
            error=res.details,
            diagnosis=diagnosis,
            fixed=True
        )

        # --- Synthesize the learned skill ---
        corrected_code = (
            "import bpy\n"
            "bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0,0,1.10))\n"
        )
        skill_path = synthesizer.synthesize_from_execution(
            app_name=app_name,
            task_description=task_desc,
            script_code=corrected_code,
            user_confirmed=True
        )

        assert skill_path is not None
        assert Path(skill_path).is_file()
        
        meta, body, _ = vault.read(skill_path)
        assert meta["status"] == "known"
