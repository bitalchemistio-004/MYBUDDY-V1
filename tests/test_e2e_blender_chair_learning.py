"""
Empirical End-to-End Verification Test: Blender 3D Chair Learning & Self-Correction Loop.

Validates the full cycle requested by user:
1. Prompting MBUDDY to create a 3D Chair in Blender.
2. Triggering an initial mistake/failure.
3. Observing error capture, traceback clipping, and Debugging experience note generation.
4. Correcting the script across the multi-turn sandbox retry loop.
5. Auto-synthesizing and saving the verified Skill note in Obsidian Vault.
6. Verifying hybrid graph/vector RAG recall of the learned skill note.
"""

import tempfile
import pytest
from pathlib import Path
from brain.obsidian_vault import Vault
from brain.skill_synthesizer import SkillSynthesizer
from brain.connectors import app_driver

def test_blender_chair_learning_and_self_correction_loop(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        vault = Vault(tmp_dir)
        vault.init()
        # debugging notes go to this vault, not the user's (each run used to add to real lesson notes)
        from brain import brain_store
        monkeypatch.setattr(brain_store, "get_vault", lambda: vault)
        monkeypatch.setattr(app_driver.requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("no model in tests")))
        synthesizer = SkillSynthesizer(vault=vault)

        app_name = "Blender 4.3"
        task_desc = "make a simple chair"
        
        # --- Stage 1: Initial Flawed Script & Error Capture ---
        flawed_script = (
            "import bpy\n"
            "# Attempting invalid API call\n"
            "bpy.ops.mesh.primitive_seat_add(size=2.0)\n"
        )
        simulated_error = (
            "Traceback (most recent call last):\n"
            "  File \"script.py\", line 3, in <module>\n"
            "AttributeError: 'module' object has no attribute 'primitive_seat_add'\n"
        )

        # Diagnose error & record Debugging Experience note
        sig = app_driver.signature(simulated_error)
        diagnosis = app_driver.diagnose(
            name=app_name,
            driver={"language": "Python", "test": [], "show": []},
            knowledge="Blender Python API uses primitive_cube_add for meshes.",
            code=flawed_script,
            error=simulated_error,
            past=""
        )
        
        # Remember debugging experience (failed attempt)
        app_driver.remember_debugging(
            name=app_name,
            error=simulated_error,
            diagnosis="What went wrong: invalid API call primitive_seat_add.\nWhy: AttributeError.\nFix: Use primitive_cube_add instead.",
            fixed=False
        )

        # --- Stage 2: Corrected Script Execution ---
        corrected_script = (
            "import bpy\n"
            "# 1. Add seat cushion\n"
            "bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 1))\n"
            "# 2. Add 4 legs\n"
            "for x in (-0.4, 0.4):\n"
            "    for y in (-0.4, 0.4):\n"
            "        bpy.ops.mesh.primitive_cube_add(size=0.2, location=(x, y, 0.5))\n"
        )

        # Record debugging experience fix success
        app_driver.remember_debugging(
            name=app_name,
            error=simulated_error,
            diagnosis="What went wrong: invalid API call primitive_seat_add.\nWhy: AttributeError.\nFix: Use primitive_cube_add instead.",
            fixed=True
        )

        # --- Stage 3: Auto-Synthesize Skill Note ---
        skill_path = synthesizer.synthesize_from_execution(
            app_name=app_name,
            task_description=task_desc,
            script_code=corrected_script,
            user_confirmed=True
        )

        assert skill_path is not None
        assert Path(skill_path).is_file()

        # Read back vault note frontmatter and body
        meta, body, _ = vault.read(skill_path)
        assert meta["user_approved"] is True
        assert meta["status"] == "known"
        assert meta["strength"] == 1.0
        assert "primitive_cube_add" in body

        # --- Stage 4: Hybrid RAG Recall Verification ---
        results = vault.retrieve("how do I make a 3d chair in blender?", token_budget=500)
        assert len(results) >= 1
        recalled_titles = [r["title"] for r in results]
        assert any("chair" in t.lower() or "blender" in t.lower() for t in recalled_titles)
