"""Preferences are kept and followed (F31); a new name replaces the old one (F33)."""
import asyncio
import json

from brain import agent
from brain.memory_context import build_tiered_memory_context
from memory.facts import remember_fact


def test_a_preference_is_saved_and_reaches_every_reply():
    memory = {}
    out = agent._remember({"fact": "Always answer with bullet points", "kind": "preference"}, memory)
    assert out.startswith("Saved as a preference") and memory["preferences"] == ["Always answer with bullet points"]
    assert "already saved" in agent._remember({"fact": "always answer with bullet points.", "kind": "preference"}, memory)
    assert "core_facts" not in memory                                   # a preference is not a fact about the user
    ctx = build_tiered_memory_context(memory)
    assert "STANDING PREFERENCES" in ctx and "bullet points" in ctx


def test_a_new_name_replaces_the_old_one():
    memory = {}
    remember_fact(memory, "Sam is a visual engineer")
    remember_fact(memory, "The user's name is Karim.")
    out = agent._remember({"fact": "The user's name is Kim, not Karim.", "kind": "fact"}, memory)
    assert "it replaces: The user's name is Karim." in out
    assert memory["core_facts"] == ["Sam is a visual engineer", "The user's name is Kim, not Karim."]


def test_the_memory_page_lists_and_forgets_preferences(monkeypatch):
    from api import ui_api
    memory = {"core_facts": ["Sam is a visual engineer"], "preferences": ["Keep it short"]}
    monkeypatch.setattr(ui_api, "_live_memory", lambda: memory)
    monkeypatch.setattr(ui_api, "_require_local", lambda r: None)
    saved = []

    async def fake_save(m):
        saved.append(1)
    monkeypatch.setattr("memory.long_term.save_memory", fake_save)

    class Req:
        def __init__(self, method, body=None):
            self.method, self._body = method, body

        async def json(self):
            return self._body

    async def run(req):
        return json.loads((await ui_api.memory_handler(req)).text)
    got = asyncio.run(run(Req("GET")))
    assert got["preferences"] == ["Keep it short"] and got["facts"] == ["Sam is a visual engineer"]
    got = asyncio.run(run(Req("DELETE", {"preference": "Keep it short"})))
    assert got["ok"] and got["preferences"] == [] and saved


def test_the_lean_prompt_most_turns_use_carries_the_preferences():
    # live: the preference was saved, but the next answer ignored it; the lean prompt never had it
    from brain.memory_context import build_minimal_system

    class S:
        current, energy = "Normal", 0.8
    prompt = build_minimal_system(S(), {"preferences": ["always answer with bullet points"]}, "en")
    assert "STANDING PREFERENCES" in prompt and "bullet points" in prompt
    assert prompt.index("- always answer with bullet points") > prompt.index("1-3 sentences")   # after the style it overrides
    assert "STANDING PREFERENCES" not in build_minimal_system(S(), {}, "en")


def test_a_name_given_as_a_preference_replaces_the_old_name():
    # live: 'actually call me Samuel, not Sam' was saved as a preference and 'The user's name is Sam.' stayed
    memory = {}
    remember_fact(memory, "The user's name is Sam.")
    agent._remember({"fact": "The user wants to be called Samuel, not Sam.", "kind": "preference"}, memory)
    assert memory.get("core_facts") == [] and memory["preferences"] == ["The user wants to be called Samuel, not Sam."]
    agent._remember({"fact": "The user wants to be called Sami.", "kind": "preference"}, memory)
    assert memory["preferences"] == ["The user wants to be called Sami."]
    agent._remember({"fact": "Keep it short", "kind": "preference"}, memory)
    assert memory["preferences"] == ["The user wants to be called Sami.", "Keep it short"]
