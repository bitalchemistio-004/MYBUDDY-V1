"""MBUDDY_BRAIN_MODE decides what of the brain may reach a prompt, and every
turn logs what it put in (for tools/bench_brain.py: brain on versus off)."""
import pytest

from brain import brain_store as bs


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(bs, "vault_root", lambda: root)
    monkeypatch.setattr(bs, "_vault", None)
    monkeypatch.setattr(bs, "_vault_root", None)
    v = bs.get_vault()
    v.upsert("Facts", "Sister", "His sister is called Lina.", source="user")
    skill = v.upsert("Skills", "Sketcher - a house", "How the house was made in Sketcher.", source="self")
    v.mark_checked(skill, True)
    for _ in range(3):
        v.reinforce(skill, success=True)
    return v


def test_off_lets_nothing_from_the_vault_in(vault, monkeypatch):
    monkeypatch.setenv("MBUDDY_BRAIN_MODE", "off")
    assert bs.recall("what is my sister called") == ("", [])
    assert bs.used_line() == "[Brain] used: off n_notes=0 lessons=0 skills=0 examples=0"


def test_memory_lets_facts_in_but_not_skills(vault, monkeypatch):
    monkeypatch.setenv("MBUDDY_BRAIN_MODE", "memory")
    block, paths = bs.recall("what is my sister called")
    assert "Lina" in block and bs.used_line().startswith("[Brain] used: memory n_notes=1")
    assert bs.recall("make a house in Sketcher") == ("", [])
    monkeypatch.setenv("MBUDDY_BRAIN_MODE", "skills")
    block, _ = bs.recall("make a house in Sketcher")
    assert "house" in block and "skills=1" in bs.used_line()


def test_an_unknown_mode_is_full(monkeypatch):
    monkeypatch.setenv("MBUDDY_BRAIN_MODE", "everything")
    assert bs.brain_mode() == "full" and bs.allows("examples")
