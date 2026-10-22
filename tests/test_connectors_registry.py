"""Connector registry: entry shape (same as pc_actions), two-step routing, argument checks, failures."""
import pytest

import brain.connectors as conn
from brain.tools import ToolFailure


def test_every_entry_has_the_registry_shape():
    abilities = conn.all_abilities()
    assert len(abilities) >= 6
    names = [a.name for a in abilities]
    assert len(names) == len(set(names)), "ability names must be unique"
    for a in abilities:
        assert a.topic in conn.CATEGORIES
        assert a.risk in conn.RISKS
        assert isinstance(a.params, dict) and set(a.required) <= set(a.params)
        assert callable(a.execute)
        assert a.description and "\n" not in a.description and len(a.description) <= 120
        if a.risk != "read":
            assert a.preview is not None


def test_same_fields_as_pc_actions_ability():
    pc_actions = pytest.importorskip("brain.pc_actions")
    theirs = set(pc_actions.Ability.__dataclass_fields__)
    ours = set(conn.Ability.__dataclass_fields__)
    assert theirs <= ours, f"missing: {theirs - ours}"
    # The connectors are merged into pc_actions.REGISTRY; its native abilities
    # (the non-connector entries) must not share a name with them.
    ours_names = {a.name for a in conn.all_abilities()}
    native = {n for n, a in pc_actions.REGISTRY.items() if a.topic not in {c.topic for c in conn.all_abilities()}}
    assert not native & ours_names, "connector names must not clash with pc_actions"
    assert ours_names - pc_actions._NOT_OFFERED <= set(pc_actions.REGISTRY), "every offered connector is in pc_actions"


def test_entries_run_through_pc_actions_gate(monkeypatch):
    """Put one entry in a copy of pc_actions.REGISTRY and call pc_actions.run:
    a read runs at once; the same entry marked critical only asks."""
    pc_actions = pytest.importorskip("brain.pc_actions")
    ability = conn.Ability("music_now_playing_t", "d", "read", "music", execute=lambda a: "Song in App")
    monkeypatch.setattr(pc_actions, "REGISTRY", {**pc_actions.REGISTRY, ability.name: ability})
    assert pc_actions.run(ability.name, {}) == "Song in App"

    ran = []
    crit = conn.Ability("clip_crit_t", "d", "critical", "clipboard", execute=lambda a: ran.append(a) or "done",
                        params={"text": {"type": "string"}}, required=["text"],
                        preview=lambda a: f"copy \"{a.get('text')}\"")
    assert crit.prepare is not None, "a critical entry gets its question from its preview"
    monkeypatch.setattr(pc_actions, "REGISTRY", {**pc_actions.REGISTRY, crit.name: crit})
    monkeypatch.setattr(pc_actions, "_publish", lambda: None)
    out = pc_actions.run(crit.name, {"text": "hi"})
    try:
        assert "NOT DONE YET" in out and "Want me to copy \"hi\"?" in out
        assert ran == []
    finally:
        pc_actions.cancel()


def test_bad_entries_are_rejected():
    with pytest.raises(ValueError):
        conn.Ability("x", "d", "low", "music", execute=lambda a: "")          # action without preview
    with pytest.raises(ValueError):
        conn.Ability("x", "d", "dangerous", "music", execute=lambda a: "")    # unknown risk
    with pytest.raises(ValueError):
        conn.Ability("x", "d", "critical", "music", execute=lambda a: "")     # critical, nothing to ask with
    with pytest.raises(ValueError):
        conn.Ability("x", "d", "read", "weather", execute=lambda a: "")       # unknown topic


def test_two_step_routing_offers_few_tools():
    cats = conn.categories()
    assert set(cats) == {"clipboard", "calendar", "music", "office", "brain", "create", "routines", "day", "habits", "cloud"}
    for cat in cats:
        tools = conn.abilities_in(cat)
        assert 1 <= len(tools) <= 5
        assert all(t.topic == cat for t in tools)


def test_spec_matches_agent_tool_format():
    fn = conn.get("music_search").spec()["function"]
    assert fn["name"] == "music_search"
    assert fn["parameters"]["type"] == "object"
    assert fn["parameters"]["required"] == ["query"]


def test_run_checks_arguments_before_executing(monkeypatch):
    called = []
    ability = conn.get("clipboard_write")
    monkeypatch.setattr(ability, "execute", lambda a: called.append(a) or "ok")
    out = conn.run("clipboard_write", {})
    assert isinstance(out, ToolFailure) and out.startswith("FAILED:") and "required" in out
    out = conn.run("clipboard_write", {"text": 5})
    assert isinstance(out, ToolFailure) and "string" in out
    assert called == []


def test_run_rejects_enum_values_and_unknown_names():
    assert isinstance(conn.run("no_such_tool", {}), ToolFailure)
    out = conn.run("choose_app", {"app": "x", "role": "napster"})
    assert isinstance(out, ToolFailure) and "must be one of" in out


def test_run_turns_exceptions_into_failures(monkeypatch):
    ability = conn.get("clipboard_read")

    def boom(args):
        raise RuntimeError("clipboard locked")

    monkeypatch.setattr(ability, "execute", boom)
    out = conn.run("clipboard_read", {})
    assert isinstance(out, ToolFailure) and "clipboard locked" in out


def test_prepare_from_preview_plans_exact_args():
    a = conn.get("clipboard_write")
    question, text, plan = conn.prepare_from_preview(a)({"text": "abc"})
    assert question == "Want me to replace what you copied with: \"abc\"?"
    assert plan == {"text": "abc"} and text in question
