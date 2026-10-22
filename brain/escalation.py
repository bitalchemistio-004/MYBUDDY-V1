"""Asking the bigger model when the user says so ("use the bigger model to ...", "ask the cloud ...").

The 4B did not route these by itself in a live test (2026-10-08: "use the bigger model to explain ..." was
answered locally), and a request that names the tool must be honoured, so the words are recognised here and
the cloud ability runs directly. Everything else is left to the model's own choice of tool.
"""
from __future__ import annotations

import re

_EXPLICIT = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|hey|please|so)[,\s]+)*(?:can\s+you\s+|could\s+you\s+|i\s+want\s+you\s+to\s+|i'?d\s+like\s+you\s+to\s+|let'?s\s+)?"
    r"(?:(?:use|ask|call|try|get|check\s+with|consult)\s+(?:the\s+|your\s+|a\s+)?(?:bigger|big|smarter|stronger|better|larger|cloud|more\s+powerful)\s+(?:model|brain|ai|one|cloud)"
    r"|(?:use|ask)\s+(?:the\s+)?cloud"
    r"|think\s+harder)"
    r"\b[\s,:.-]*(?:(?:to|and|about|for|on)\b[\s,:.-]*)?(?P<task>.*)$", re.IGNORECASE | re.DOTALL)


def explicit(user_text: str) -> str | None:
    """The task when the user asked for the bigger model by name (at least two words of it); None otherwise."""
    m = _EXPLICIT.match(user_text or "")
    if not m:
        return None
    task = m.group("task").strip()
    return task if len(task.split()) >= 2 else None


_SOURCES = (
    (re.compile(r"\b(clipboard|what i copied|the text i copied)\b", re.IGNORECASE), "clipboard"),
    (re.compile(r"\b(my screen|the screen|screenshot|what'?s on (?:my )?screen|what i'?m looking at)\b", re.IGNORECASE), "screen"),
)
_PATH = re.compile(r"""(?:"([A-Za-z]:\\[^"]+)"|([A-Za-z]:\\[^\s"']+)|(~[\\/][^\s"']+))""")


def source_of(task: str) -> str:
    """"clipboard", "screen", a file path, or "" when the task names nothing from this PC."""
    for pattern, name in _SOURCES:
        if pattern.search(task):
            return name
    m = _PATH.search(task)
    return next((g for g in m.groups() if g), "") if m else ""


def run(task: str) -> tuple[bool, str]:
    """(ok, words): the bigger model's answer, or why it could not answer."""
    from brain.connectors import cloud as ability
    from brain.tools import ToolFailure
    out = ability.ask_bigger_model({"task": task})
    if isinstance(out, ToolFailure) or str(out).startswith("FAILED"):
        return False, str(out).removeprefix("FAILED: ").strip()
    return True, str(out)
