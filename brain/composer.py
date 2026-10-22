"""
brain/composer.py - writing new text for an ability (a story, a letter, a note).

A tool call carries only a short description of what to write ("a short story
about a robot"); the words are written here, with room for them. Live
2026-09-28: the model put a whole story inside a word_write call, the tool
step's small output budget cut it off mid-JSON, and Ollama rejected the
broken call, so nothing was written (S98). Any ability that writes new
content uses this.
"""
from __future__ import annotations

import requests

from brain.tools import ToolFailure

_SYSTEM = ("Write exactly what is asked, as plain text ready to put in a document: no preamble, no notes about "
           "the text, no markdown, no emoji. Write in the same language as the request. At most {words} words.")


def compose(instruction: str, max_words: int = 400) -> str:
    """The written text, or a ToolFailure saying why it couldn't be written.
    Blocking: abilities run in a worker thread."""
    instruction = (instruction or "").strip()
    if not instruction:
        return ToolFailure("FAILED: nothing was described to write.")
    from brain.agent import _chat_model, _ollama_chat_url
    body = {
        "model": _chat_model(), "stream": False, "think": False,
        "messages": [{"role": "system", "content": _SYSTEM.format(words=max_words)},
                     {"role": "user", "content": instruction}],
        "options": {"temperature": 0.8, "num_predict": int(max_words * 1.7)},
        "keep_alive": "30m",
    }
    try:
        data = requests.post(_ollama_chat_url("job"), json=body, timeout=120).json()
    except Exception as exc:
        return ToolFailure(f"FAILED: I couldn't write it ({type(exc).__name__}).")
    if data.get("error"):
        return ToolFailure(f"FAILED: I couldn't write it ({str(data['error'])[:80]}).")
    text = str((data.get("message") or {}).get("content") or "").strip()
    return text or ToolFailure("FAILED: the writing came back empty.")
