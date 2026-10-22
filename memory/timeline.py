"""
memory/timeline.py - the shared timeline: things that really happened.

It used to ask the model to summarise what the user SAID as a shared event, so
requests were stored as done ("We scheduled the release date", "We planned a
calendar event" when the reminder had failed; live 2026-09-27), and those
false events went back into later prompts. It also cost a model call after
every turn. Now it copies only actions that really succeeded, from the
action log, with no model call.
"""
from __future__ import annotations

from datetime import datetime

import utils.logger as log

# Actions worth remembering as shared events (reads and small volume steps aren't).
MEANINGFUL = {"add_reminder", "add_task", "set_timer", "remember", "window_action"}
KEEP = 25


def record_actions(memory: dict) -> list[str]:
    """Add actions that succeeded since the last call; returns the new events."""
    from brain import action_log

    last = float(memory.get("timeline_action_at", 0.0))
    new = [a for a in action_log.since(last) if a["tool"] in MEANINGFUL and not a["result"].startswith("Cancelled")]
    events = []
    for a in new:
        event = a["result"]
        entry = {"time": datetime.fromtimestamp(a["at"]).strftime("%Y-%m-%d %H:%M"), "event": event}
        memory.setdefault("shared_timeline", []).append(entry)
        events.append(event)
        log.info(f"Timeline event saved: {event}")
    if new:
        memory["shared_timeline"] = memory["shared_timeline"][-KEEP:]
    # Advance only past actions that were seen (never to "now": an action logged
    # in the same clock tick would be skipped).
    seen = action_log.since(last)
    if seen:
        memory["timeline_action_at"] = max(a["at"] for a in seen)
    return events


async def extract_timeline_event(user_text: str, reply: str, memory: dict) -> None:
    """Kept for the post-processing call site: records real actions only."""
    record_actions(memory)
