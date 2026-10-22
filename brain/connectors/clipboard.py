"""
Clipboard connector: read what the user copied, put text on the clipboard.

Local Windows clipboard through pywin32 (win32clipboard, already installed).
Reading is risk "read" (BRAIN_PLAN tier A); it is only done when the user asks,
and what's read is not saved to memory. Writing replaces what the user had
copied, so it is risk "low" (runs at once, as in pc_actions; preview() words it if the lead makes it ask first), and the write is checked by
reading the clipboard back before MBUDDY says it's done.
"""
from __future__ import annotations

import time

from brain.connectors import Ability, fail
from brain.tools import ToolFailure

MAX_READ_CHARS = 3000   # the model runs with a 4,096-token window
MAX_WRITE_CHARS = 100_000

_win32 = None  # win32clipboard, loaded on first use; tests put a fake here


def _api():
    global _win32
    if _win32 is None:
        import win32clipboard
        _win32 = win32clipboard
    return _win32


def _open(api, tries: int = 10) -> None:
    """Another app can hold the clipboard for a moment; retry briefly."""
    for i in range(tries):
        try:
            api.OpenClipboard()
            return
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(0.05)


def _read_raw() -> tuple[str | None, list[str]]:
    """(text or None, other things on the clipboard: 'files: a, b' / 'an image')."""
    api = _api()
    _open(api)
    try:
        text = None
        other: list[str] = []
        if api.IsClipboardFormatAvailable(api.CF_UNICODETEXT):
            text = api.GetClipboardData(api.CF_UNICODETEXT)
        if api.IsClipboardFormatAvailable(api.CF_HDROP):
            files = list(api.GetClipboardData(api.CF_HDROP) or [])
            if files:
                other.append("files: " + ", ".join(files[:10]) + (f" (+{len(files) - 10} more)" if len(files) > 10 else ""))
        if api.IsClipboardFormatAvailable(api.CF_DIB):
            other.append("an image")
        return text, other
    finally:
        api.CloseClipboard()


def read_clipboard(args: dict) -> str:
    text, other = _read_raw()
    if text:
        text = text.replace("\x00", "")
        cut = len(text) > MAX_READ_CHARS
        body = text[:MAX_READ_CHARS]
        note = f"\n[cut: the clipboard has {len(text)} characters; only the first {MAX_READ_CHARS} are shown]" if cut else ""
        extra = f"\nAlso on the clipboard: {'; '.join(other)}." if other else ""
        return f"Clipboard text ({len(text)} characters):\n{body}{note}{extra}"
    if other:
        return f"The clipboard has no text; it holds {'; '.join(other)}."
    return fail("The clipboard is empty.")


def write_clipboard(args: dict) -> str:
    text = str(args.get("text", ""))
    if not text:
        return fail("There was no text to copy.")
    if len(text) > MAX_WRITE_CHARS:
        return fail(f"That's {len(text)} characters; the limit is {MAX_WRITE_CHARS}.")
    api = _api()
    _open(api)
    try:
        api.EmptyClipboard()
        api.SetClipboardText(text, api.CF_UNICODETEXT)
    finally:
        api.CloseClipboard()
    # Checked at the source before saying it happened.
    back, _other = _read_raw()
    if back != text:
        return fail("I tried to copy it, but the clipboard doesn't hold that text.")
    return f"Copied {len(text)} characters to the clipboard."


def _preview_write(args: dict) -> str:
    text = str(args.get("text", ""))
    shown = text if len(text) <= 80 else text[:77] + "..."
    return f"replace what you copied with: \"{shown}\""


ABILITIES = [
    Ability(
        name="clipboard_read",
        topic="clipboard",
        description="Read the text (or file list) the user has copied, only when they ask about it.",
        risk="read",
        execute=read_clipboard,
    ),
    Ability(
        name="clipboard_write",
        topic="clipboard",
        description="Put text on the clipboard so the user can paste it.",
        params={"text": {"type": "string", "description": "the exact text to copy"}}, required=["text"],
        risk="low",
        execute=write_clipboard,
        preview=_preview_write,
    ),
]
