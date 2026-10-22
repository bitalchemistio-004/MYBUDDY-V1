"""The decision step is told the lessons and skills that apply (W5): bounded,
only what the brain mode allows, and nothing when nothing applies."""
import pytest

from brain import agent, brain_store
from brain.obsidian_vault import Vault


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = Vault(tmp_path / "vault")
    v.init()
    v.upsert("Debugging experience", "Lists go to text files",
             "When the user asks to jot down or note down a list, save it as a text file with create_file, not as a task.",
             source="user", status="known", strength=0.8)
    v.upsert("Facts", "Pet", "The cat is called Biscuit.", source="user", status="known", strength=0.8)
    monkeypatch.setattr(brain_store, "get_vault", lambda: v)
    brain_store._rules_cache.clear()
    return v


def test_a_lesson_that_applies_reaches_the_decision(vault, monkeypatch):
    monkeypatch.setenv("MBUDDY_BRAIN_MODE", "lessons")
    out = brain_store.recall_rules("Jot down my packing list: passport, charger")
    assert "create_file" in out and len(out) < 700
    assert "Biscuit" not in out                                   # facts are not rules
    assert "create_file" in agent._rules_line("Jot down my packing list: passport, charger")


def test_nothing_applies_means_nothing_is_added(vault, monkeypatch):
    monkeypatch.setenv("MBUDDY_BRAIN_MODE", "full")
    assert brain_store.recall_rules("what time is it?") == ""
    assert agent._rules_line("what time is it?") == ""


def test_the_brain_mode_switches_it_off(vault, monkeypatch):
    for mode in ("off", "memory"):
        monkeypatch.setenv("MBUDDY_BRAIN_MODE", mode)
        brain_store._rules_cache.clear()
        assert brain_store.recall_rules("Jot down my packing list") == ""


def test_a_rule_from_one_app_stays_with_that_app(vault, monkeypatch):
    # F8: a Blender rule was handed to the decision for an unrelated request
    monkeypatch.setenv("MBUDDY_BRAIN_MODE", "full")
    vault.upsert("Debugging experience", "Blender 4.3 - packing the scene",
                 "When packing a list of objects into the scene, remove the default cube first.",
                 source="self", status="known", strength=0.9, tags=["mbuddy/debugging", "mbuddy/app/blender-4-3"])
    assert "default cube" not in brain_store.recall_rules("Jot down my packing list: passport, charger")
    brain_store._rules_cache.clear()
    assert "default cube" in brain_store.recall_rules("in blender, packing a list of objects into the scene")


def test_learning_a_known_connected_app_does_the_task_instead_of_promising(vault, monkeypatch):
    # F11: 'I already have notes on Blender, so I'll go from those' and nothing happened
    vault.upsert("Apps", "Blender", "Blender is a 3D app driven by Python scripts.", source="self", status="known", strength=0.9)
    from core import ui_settings
    from brain.connectors import app_driver
    monkeypatch.setattr(ui_settings, "connected_app", lambda name: {"name": "Blender", "path": "x"} if "blender" in name.lower() else None)
    handed = []
    monkeypatch.setattr(app_driver, "use_app", lambda args: handed.append(args) or "I know how to use Blender. Doing it now.")
    out = brain_store.learn_about("Blender", "app", "make a chair and render it")
    assert handed == [{"app": "Blender", "task": "make a chair and render it"}] and out.startswith("I know how to use Blender")
    assert brain_store.learn_about("Blender", "topic", "") == "I already have notes on Blender."   # no promise of work
