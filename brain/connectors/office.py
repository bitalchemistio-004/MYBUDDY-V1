"""
Office connector: writing into Word, through Word itself (COM automation).

Live 2026-09-28: "write the story in the Word you just opened" -> MBUDDY
opened Word twice and then said "*types gently*" with no way to type (S92).

- Word is driven through its own COM interface (pywin32), the same way a
  macro would: nothing is simulated with keystrokes.
- It never touches a document that is saved on disk: it writes into the
  document in front only when that one was never saved ("Document2"), and
  otherwise starts a new document. Saving (save_as) makes a new file in
  Documents\\MBUDDY and never overwrites.
- The result is checked in the document itself before it says it wrote.
- "Write down what you just said": source "your_last_reply" takes MBUDDY's
  previous reply (runtime_status.last_reply), which the tool-picking step
  doesn't see.
"""
from __future__ import annotations

import re

from brain.connectors import Ability, fail
from brain.tools import ToolFailure

MAX_CHARS = 20000


def _com():
    """(win32com.client, pythoncom); tests replace this."""
    import pythoncom
    import win32com.client
    return win32com.client, pythoncom


def _last_reply() -> str:
    from core.status import runtime_status
    return str(getattr(runtime_status, "last_reply", "") or "")


def _base_dir():
    from brain import pc_actions
    return pc_actions._base_dir(create=True)


def _word_app(client):
    try:
        return client.GetActiveObject("Word.Application")
    except Exception:
        app = client.Dispatch("Word.Application")
        app.Visible = True
        return app


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r", "\n")).strip()


def _text_for(args: dict) -> str:
    source = str(args.get("source", "")).lower()
    if source == "write_new":
        from brain import composer
        return composer.compose(str(args.get("write_about") or args.get("text") or ""))
    if source == "your_last_reply":
        text = _last_reply()
        # Stage directions and the mood tag are not part of what was said.
        from brain.personality import strip_emotion_tags   # only real tags; the user's own [S1] labels stay
        text = re.sub(r"\*(?!\s)[^*\n]{1,40}?(?<!\s)\*", "", strip_emotion_tags(text))
    else:
        text = str(args.get("text", ""))
    return text.strip()[:MAX_CHARS]


def write_in_word(args: dict) -> str:
    text = _text_for(args)
    if isinstance(text, ToolFailure):
        return text
    if not text:
        return fail("There was no text to write (nothing given, and no earlier reply to copy).")
    client, pythoncom = _com()
    pythoncom.CoInitialize()
    try:
        try:
            app = _word_app(client)
        except Exception as exc:
            return fail(f"Word couldn't be started ({type(exc).__name__}).")
        doc = app.ActiveDocument if app.Documents.Count else None
        if doc is None or str(doc.Path or ""):
            doc = app.Documents.Add()  # a saved file is never written into
        existing = _norm(str(doc.Content.Text))
        doc.Content.InsertAfter(("\r" if existing else "") + text.replace("\n", "\r"))
        app.Visible = True
        try:
            doc.Activate()
        except Exception:
            pass
        # Checked in the document itself.
        if _norm(text)[-60:] not in _norm(str(doc.Content.Text)):
            return fail(f"I sent the text to \"{doc.Name}\" in Word, but it isn't in the document.")
        saved = ""
        name = re.sub(r'[<>:"/\\|?*]+', "", str(args.get("save_as", "")).strip()).strip(". ")
        if name:
            folder = _base_dir()
            path = folder / (name if name.lower().endswith(".docx") else name + ".docx")
            n = 2
            while path.exists():
                path = folder / f"{path.stem.rsplit(' (', 1)[0]} ({n}).docx"
                n += 1
            doc.SaveAs2(str(path), 16)  # 16 = .docx
            saved = f" Saved as {path}."
        words = len(text.split())
        return (f"Wrote {words} words into \"{doc.Name}\" in Word (checked in the document)."
                + (saved or " It isn't saved yet."))
    finally:
        pythoncom.CoUninitialize()


def _preview(args: dict) -> str:
    source = str(args.get("source", "")).lower()
    what = {"your_last_reply": "your last reply", "write_new": f"new text ({args.get('write_about', '')})"}.get(
        source, "the text")
    return f"write {what} into Word"


ABILITIES = [
    Ability(
        name="word_write",
        topic="office",
        description="Write into Word and optionally save it: what you just said, exact short text, or something new.",
        params={
            "source": {"type": "string", "enum": ["your_last_reply", "text", "write_new"],
                       "description": "your_last_reply = what you just told the user; text = the exact short text "
                                      "below; write_new = something new to write (a story, a letter, an essay)"},
            "text": {"type": "string", "description": "the exact words, when source is text (short)"},
            "write_about": {"type": "string",
                            "description": "when source is write_new: what to write, in a few words; it is written for you"},
            "save_as": {"type": "string", "description": "only when the user asks to save it: the file name"},
        },
        required=["source"],
        risk="low",
        execute=write_in_word,
        preview=_preview,
    ),
]
