import tempfile
import pytest
from brain.obsidian_vault import Vault
from brain.skill_synthesizer import SkillSynthesizer

def test_skill_synthesizer():
    with tempfile.TemporaryDirectory() as tmp_dir:
        vault = Vault(tmp_dir)
        vault.init()
        synthesizer = SkillSynthesizer(vault=vault)
        
        path_str = synthesizer.synthesize_from_execution(
            app_name="Blender",
            task_description="Make a 3D Chair",
            script_code="import bpy\nbpy.ops.mesh.primitive_cube_add()",
            user_confirmed=True
        )
        
        assert path_str is not None
        meta, body, _ = vault.read(path_str)
        assert meta["user_approved"] is True
        assert meta["status"] == "known"
        assert "Blender" in path_str
        assert "Make a 3D Chair" in body or "Make A 3D Chair" in path_str or "primitive_cube_add" in body
