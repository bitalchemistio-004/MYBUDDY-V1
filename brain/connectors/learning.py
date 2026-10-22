"""
Brain connector: MBUDDY's own notes (brain/brain_store.py, an Obsidian vault).

- search_notes: look something up in what MBUDDY has learned. A deterministic
  keyword/title/tag search in code; the model only says what to look for
  (S104: a small model does better matching words than writing queries).
- learn_about: something new MBUDDY can't do or doesn't know yet. The brain is
  checked first; if it's new, a background job reads the web, writes a note
  and comes back to the user (S102).
"""
from __future__ import annotations

from brain.connectors import Ability


def _search(args: dict) -> str:
    from brain import brain_store
    return brain_store.search_notes(str(args.get("query", "")))


def _learn(args: dict) -> str:
    from brain import brain_store
    return brain_store.learn_about(str(args.get("topic", "")), str(args.get("kind") or "topic"),
                                   str(args.get("why", "")))


ABILITIES = [
    Ability(
        name="search_notes",
        topic="brain",
        description="Look something up in your own notes: what you have learned before.",
        params={"query": {"type": "string", "description": "the words to look for"}},
        required=["query"],
        risk="read",
        execute=_search,
    ),
    Ability(
        name="learn_about",
        topic="brain",
        description="Learn something you can't do yet, or an app or topic to study. Not for advice, or work in a connected app (use_app).",
        params={
            "topic": {"type": "string", "description": "what to learn, in a few words"},
            "kind": {"type": "string", "enum": ["topic", "app"], "description": "app = an app on the PC"},
            "why": {"type": "string", "description": "what the user asked for, in a few words"},
        },
        required=["topic"],
        risk="low",
        execute=_learn,
        preview=lambda args: f"learn about {str(args.get('topic', '')).strip()}",
        says_all=True,
    ),
]
