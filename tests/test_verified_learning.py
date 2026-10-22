"""Only code-checked outcomes move a note's strength (U2), and the one-time
migration of never-checked notes is safe to repeat and to undo."""
import json

import pytest

from brain import brain_store as bs
from brain import verify_migration as vm


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(bs, "vault_root", lambda: root)
    monkeypatch.setattr(bs, "_vault", None)
    monkeypatch.setattr(bs, "_vault_root", None)
    return bs.get_vault()


def test_a_use_without_a_check_counts_the_use_but_not_as_success(vault):
    path = vault.upsert("Apps", "Sketcher", "How to drive it.", source="web")
    before = vault.read(path)[0]
    vault.reinforce(path, success=None)
    after = vault.read(path)[0]
    assert after["uses"] == before["uses"] + 1 and after["strength"] == before["strength"]
    assert after.get("successes", 0) == before.get("successes", 0)


def test_the_migration_marks_unchecked_notes_backs_up_repeats_safely_and_undoes_exactly(vault, tmp_path):
    skill = vault.upsert("Skills", "Sketcher - a car", "## Script\n```\nx = 1\n```", source="self")
    for _ in range(4):
        vault.reinforce(skill, success=True)            # the old "successes" nobody checked
    checked = vault.upsert("Skills", "Sketcher - a box", "## Script\n```\ny = 2\n```", source="self")
    vault.mark_checked(checked, True)
    original = skill.read_text(encoding="utf-8")
    checked_before = checked.read_text(encoding="utf-8")
    backup = tmp_path / "backup.json"
    assert vm.apply(vault, backup) == 1
    meta, body, _ = vault.read(skill)
    assert meta["status"] == "unverified" and float(meta["strength"]) <= 0.3 and vm.LINE in body
    assert str(skill) in json.loads(backup.read_text(encoding="utf-8"))   # backed up
    assert checked.read_text(encoding="utf-8") == checked_before           # a checked note is left alone
    assert vm.apply(vault, backup) == 0                                    # repeating changes nothing
    assert vm.undo(vault, backup) == 1
    assert skill.read_text(encoding="utf-8") == original                   # exactly as it was
