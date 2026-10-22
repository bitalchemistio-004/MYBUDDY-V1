"""Small response authenticity context for reducing repeated phrasing."""
from __future__ import annotations

from brain.personality import strip_emotion_tags


def build_authenticity_context(history: list) -> dict:
    assistant_replies = [
        strip_emotion_tags(str(item.get("content", ""))).strip()
        for item in history[-12:]
        if isinstance(item, dict) and item.get("role") == "assistant"
    ]
    openings = []
    structures = []
    for reply in assistant_replies[-6:]:
        words = reply.split()
        if words:
            openings.append(" ".join(words[:4]).lower())
        structures.append(
            "question" if reply.endswith("?")
            else "list" if "\n-" in reply or "\n1." in reply
            else "short" if len(words) <= 18
            else "paragraph"
        )
    repeated_openings = sorted({item for item in openings if openings.count(item) > 1})
    repeated_structures = sorted({item for item in structures if structures.count(item) >= 3})
    return {
        "recent_openings": openings[-4:],
        "repeated_openings": repeated_openings,
        "recent_structures": structures[-6:],
        "repeated_structures": repeated_structures,
        "guidance": (
            "Avoid repeated openings and rigid answer shapes. Match this turn naturally."
            if repeated_openings or repeated_structures
            else "No repetition penalty is active."
        ),
    }
