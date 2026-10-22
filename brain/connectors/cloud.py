"""The bigger model in the cloud, as two abilities.

- ask_bigger_model: the whole request is the user's own words (a hard
  question, exact reasoning, a long piece of writing). Nothing from the PC is
  attached, so it runs at once; the reply says which model answered.
- ask_bigger_model_about: it needs something FROM the PC (the clipboard, a
  file, the screen). It is "critical": MBUDDY asks first, naming exactly what
  would be sent and where, and only the user's yes sends it.

Both answer in full by themselves (says_all): the small local model adds
nothing, so the bigger model's answer is not reworded or shortened. When the
cloud is off, unreachable or over its limit, the ability fails in plain words
and the local model answers as usual.
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path

from brain.connectors import Ability, fail
from core import cloud

SYSTEM = ("You are the bigger brain of a small desktop assistant called MBUDDY. Answer the request directly and "
          "accurately, with no preamble. It will be spoken aloud, so keep it under 120 words unless the user "
          "clearly asked for something longer, and use plain sentences, not tables or markdown.")
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json", ".py", ".html", ".xml", ".docx", ".rtf"}
MAX_FILE_BYTES = 2_000_000
MAX_SOURCE_CHARS = 60_000


def _answer(messages: list[dict], kind: str, purpose: str, max_tokens: int = 900):
    try:
        reply = cloud.chat(messages, kind=kind, purpose=purpose, max_tokens=max_tokens)
    except cloud.CloudError as exc:
        return fail(f"{exc} I'll answer with my own model instead.")
    return reply.text


def ask_bigger_model(args: dict) -> str:
    task = str(args.get("task", "")).strip()
    if not task:
        return fail("there was nothing to ask the bigger model.")
    return _answer([{"role": "system", "content": SYSTEM}, {"role": "user", "content": task[:12_000]}], "main", "ask")


# ── what can be sent, and how it is read ─────────────────────────────────
def _what(source: str) -> str:
    s = str(source or "").strip()
    low = s.lower()
    if low in ("clipboard", "the clipboard", "what i copied"):
        return "what's on your clipboard"
    if low in ("screen", "screenshot", "my screen", "the screen"):
        return "a screenshot of your screen"
    return f"the file {Path(s).name or s}"


def _preview(args: dict) -> str:
    kind = "vision" if _what(args.get("source", "")).startswith("a screenshot") else "main"
    return f"send {_what(args.get('source', ''))} to the bigger model ({cloud.model_for(kind)}) in the cloud"


def _read_file(source: str):
    path = Path(os.path.expanduser(source.strip().strip('"')))
    try:
        path = path.resolve()
    except OSError:
        return fail("I couldn't find that file.")
    home = Path.home().resolve()
    if home not in path.parents and path != home:
        return fail("I only read files inside your user folder.")
    if not path.is_file():
        return fail(f"I couldn't find the file {path.name}.")
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return fail(f"I can read text files and Word documents, not {path.suffix or 'this kind of file'} yet.")
    if path.stat().st_size > MAX_FILE_BYTES:
        return fail(f"{path.name} is too big for me to send (over 2 MB).")
    if path.suffix.lower() == ".docx":
        try:
            import docx
            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
        except Exception:  # noqa: BLE001
            return fail(f"I couldn't open {path.name} as a Word document.")
    return path.read_text(encoding="utf-8", errors="replace")


def _screenshot_b64() -> str | None:
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.thumbnail((1400, 1400))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


def ask_bigger_model_about(args: dict) -> str:
    task = str(args.get("task", "")).strip() or "Summarise this."
    source = str(args.get("source", "")).strip()
    what = _what(source)
    if what.startswith("a screenshot"):
        image = _screenshot_b64()
        if image is None:
            return fail("I couldn't take a screenshot.")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task[:2000]}]
        reply = _answer_images(messages, [image])
        return reply
    if what.startswith("what's on your clipboard"):
        from brain.connectors import clipboard
        try:
            text, other = clipboard._read_raw()
        except Exception:  # noqa: BLE001
            return fail("I couldn't read the clipboard.")
        if not (text or "").strip():
            return fail("the clipboard has no text on it" + (f" (it has {other[0]})" if other else "") + ".")
    else:
        text = _read_file(source)
        if not isinstance(text, str) or text.startswith("FAILED"):
            return text
    note = ""
    if len(text) > MAX_SOURCE_CHARS:
        text, note = text[:MAX_SOURCE_CHARS], " (I sent only the first part: it was very long.)"
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"{task[:2000]}\n\n--- {what} ---\n{text}"}]
    out = _answer(messages, "main", "about")
    return out if str(out).startswith("FAILED") else str(out) + note


def _answer_images(messages: list[dict], images: list[str]):
    try:
        return cloud.chat(messages, kind="vision", purpose="screen", images=images, max_tokens=900).text
    except cloud.CloudError as exc:
        return fail(f"{exc} I can't look at your screen without it.")


ABILITIES = [
    Ability(
        name="ask_bigger_model",
        topic="cloud",
        description="A hard question for a much bigger cloud model: exact reasoning or long writing. Not for chat, advice or ideas.",
        params={"task": {"type": "string", "description": "the whole question or request, in the user's words, with any text to work on included"}},
        required=["task"],
        risk="low",
        execute=ask_bigger_model,
        preview=lambda args: f"ask the bigger model in the cloud: {str(args.get('task', ''))[:60]}",
        says_all=True,
    ),
    Ability(
        name="ask_bigger_model_about",
        topic="cloud",
        description="Send the clipboard, a file or the screen to the cloud model to read or explain. It asks first.",
        params={"task": {"type": "string", "description": "what to do with it: summarise, explain, find, translate"},
                "source": {"type": "string", "description": "clipboard, screen, or the file's path"}},
        required=["task", "source"],
        risk="critical",
        execute=ask_bigger_model_about,
        preview=_preview,
        says_all=True,
    ),
]
