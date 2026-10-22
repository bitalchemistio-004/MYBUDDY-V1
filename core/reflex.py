"""Reflexes: requests answered by code alone, with no model call.

The keyword rules in brain/cognitive_state.py already recognise a few explicit
commands (the time, a calculation, a timer, a task or reminder, the volume)
and brain/pc_windows.direct_command recognises window and media commands. The
real work for these is done by tools. What used to follow was two model calls
anyway: one to decide (already decided) and one to word a reply that the tool
had already written in plain words. In the logs that was 12 s on average for
"set a timer", against well under a second of real work.

A reflex skips both. It keeps the tool's own sentence as the reply, speaks it,
and writes it to the ledger (core/ledger.py) so the model, when it next runs,
knows what was done for it. The user can switch instant replies off (then the
model words every reply as before).

Safety: a reflex only applies to tools whose result is a short, finished
sentence for the user; a failure, a long result, or anything that needs the
model's judgment goes the normal way.
"""
from __future__ import annotations

import re

from core import ledger

# Keyword tools (brain/cognitive_state.py) whose result is already a sentence
# for the user, and a window or media command done by code (pc_windows).
REFLEX_TOOLS = {"clock", "calculator", "timer", "task", "sys_volume"}
MAX_REPLY_CHARS = 280
_FAILED_SOUNDING = re.compile(r"\b(couldn'?t|can'?t|cannot|unable|didn'?t|failed|try something|not (?:found|available|set))\b", re.I)
_LABEL = re.compile(r"^\s*(TOOL (OUTPUT|FAILED|ERROR)[^\n]*\n)")


def enabled() -> bool:
    try:
        from core import ui_settings
        return bool(ui_settings.get("instant_replies"))
    except Exception:
        return True


def reply_for(tool_name: str, result) -> str | None:
    """The reply to speak as it is, or None to let the model word it."""
    if not enabled() or tool_name not in REFLEX_TOOLS:
        return None
    from brain.tools import ToolFailure
    if isinstance(result, ToolFailure) or result is None:
        return None
    text = _LABEL.sub("", str(result)).strip()
    if not text or "\n" in text or len(text) > MAX_REPLY_CHARS or text.upper().startswith(("FAILED", "NOT ")):
        return None
    if _FAILED_SOUNDING.search(text):
        return None                 # a tool's own "couldn't do it" sentence: the model words what went wrong
    return text


def handled(user_text: str, tool_name: str, reply: str) -> None:
    """Tell the ledger the model did not run for this."""
    ledger.record(user_text, f"{tool_name}: {reply}", ok=True)
