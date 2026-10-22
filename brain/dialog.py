"""Short conversations that code owns, so nothing is promised that was not done.

Dictation: "write something" / "take a note" starts it. MBUDDY says it is
listening; what the user says next is kept (every line gets a short
acknowledgement from code, no model call); "done" saves it and checks the
save; "cancel" drops it. A note said in one breath ("take a note: buy milk")
is saved at once. Live (2026-10-08) the 4B said "I've got that in my notes"
for a note it had not saved: here the save is read back before it is claimed.

Why code and not the model: measured on 41 spoken requests, the local 4B
recognised "wait for the content" in 3 of 5 and even the big cloud model in 4
of 5 (tools/bench_router_intents.py). A state the user is inside of must not
depend on a guess; only the start of it is recognised from the words, and
anything not recognised goes to the model as before.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import utils.logger as log

IDLE_S = 180            # a dictation left this long is dropped; the next words are an ordinary turn
MAX_CHARS = 8000

_lock = threading.Lock()


@dataclass
class Capture:
    kind: str                       # "note" | "document"
    started: float = field(default_factory=time.time)
    last: float = field(default_factory=time.time)
    parts: list = field(default_factory=list)


@dataclass
class Turn:
    say: str
    tag: str = "NORMAL"
    state: str = ""                 # "listening" while a dictation is open


_state: Capture | None = None
_ACKS = ("Go on.", "Got it.", "Mm-hm.", "Okay, keep going.")

_VERB = r"(?:write|jot|put|take|make|keep|save|type|dictate|start|begin)"
_START = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|hey|please|so)[,\s]+)*"
    r"(?:i\s+(?:want|need|would\s+like)\s+you\s+to\s+|i'?m\s+going\s+to\s+|i\s+will\s+|can\s+you\s+|could\s+you\s+|let'?s\s+|will\s+you\s+)?"
    r"(?P<verb>" + _VERB + r")\b(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_DOC_WORDS = re.compile(r"\b(document|doc|letter|essay|report|word)\b", re.IGNORECASE)
_EMAIL_WORDS = re.compile(r"\b(e-?mail|message|text|reply|draft)\b", re.IGNORECASE)
# what may follow the verb with no content in it: "something", "this down", "a note", "a new document"
_EMPTY_REST = re.compile(
    r"^[\s,.:;-]*(?:(?:something|anything|this|that|it|a|an|the|new|quick|short|little|some|note|memo|document|doc|letter|essay|report|"
    r"e-?mail|message|text|reply|draft|down|for|me|out|please|in|word|of|my|to|myself|dictate|dictation|now)\b[\s,.:;-]*)*$", re.IGNORECASE)
FINISH = re.compile(r"^\W*(?:ok(?:ay)?\W+)?(?:that'?s\s+(?:it|all)|that\s+is\s+(?:it|all)|done|finish(?:ed)?|save\s+(?:it|that|this)|"
                    r"end\s+of\s+(?:the\s+)?(?:note|document)|stop\s+dictating|i'?m\s+done)\W*$", re.IGNORECASE)
CANCEL = re.compile(r"^\W*(?:cancel|never\s*mind|forget\s+it|scratch\s+that|discard\s+(?:it|that)|drop\s+it)\W*$", re.IGNORECASE)


_CONTENT_FORMS = (
    # "take a note: buy milk", "make a quick note that I called Sara", "jot a note to myself: ..."
    re.compile(r"^\s*(?:(?:ok(?:ay)?|hey|please|so)[,\s]+)*(?:can\s+you\s+|could\s+you\s+|let'?s\s+)?(?:take|make|jot|keep|put|write)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:quick\s+|short\s+|little\s+)?"
               r"(?:note|memo)\s*(?::|-|,|that|saying|says|to\s+(?:myself|self)\s*:?)\s*(?P<c>.+)$", re.IGNORECASE | re.DOTALL),
    # "write this down: buy milk", "jot down buy milk", "write down that I owe Sara"
    re.compile(r"^\s*(?:(?:ok(?:ay)?|hey|please|so)[,\s]+)*(?:can\s+you\s+|could\s+you\s+|let'?s\s+)?(?:write|jot|put|type|note)\s+(?:this\s+|it\s+|that\s+)?down\s*(?::|-|,|that)?\s*(?P<c>.+)$",
               re.IGNORECASE | re.DOTALL),
)


def detect_start(text: str) -> tuple[str, str] | None:
    """(kind, content-said-in-the-same-breath) when the words open a dictation; None otherwise.
    Only two shapes open one: the bare request ("write something", "take a note", "I'm going to dictate")
    and a note with its content after a marker ("take a note: buy milk", "write this down: ..."). A request to
    compose ("write me a poem", "write a thank you note to my teacher") is not dictation: the model writes it."""
    text = text or ""
    for form in _CONTENT_FORMS:
        m = form.match(text)
        if m and len(m.group("c").split()) >= 2:
            return "note", m.group("c").strip()
    m = _START.match(text)
    if not m:
        return None
    verb, rest = m.group("verb").lower(), m.group("rest")
    low = rest.lower()
    opens = (
        verb == "dictate"
        or (verb in ("start", "begin") and re.search(r"(note|memo|document|doc|letter)", low))
        or (verb in ("take", "make", "keep", "jot", "put", "save") and re.search(r"\b(note|memo)\b|\bdown\b", low))
        or (verb in ("write", "type") and (re.search(r"\b(something|anything|this|that|it|a|an|some|down|new)\b", low) or not low.strip()))
    )
    if opens and _EMPTY_REST.match(rest):
        kind = "document" if _DOC_WORDS.search(low) and not re.search(r"\b(note|memo)\b", low) else "note"
        return kind, ""
    return None


def _title(text: str) -> str:
    words = re.sub(r"[^\w\s'-]", " ", text).split()[:6]
    head = " ".join(words).strip()
    head = (head[:1].upper() + head[1:]) or "Note"
    return f"{head} {datetime.now():%Y-%m-%d}"[:70]


def _save_note(text: str) -> tuple[bool, str]:
    """Write the note into the vault and read it back: (ok, words for the user)."""
    from brain import brain_store
    vault = brain_store.get_vault()
    if vault is None:
        return False, "I couldn't save it: your notes folder isn't linked yet. Link it in Settings."
    try:
        path = vault.upsert("Knowledge", _title(text), text.strip(), source="user", tags=["mbuddy/dictated", "note"])
        _meta, body, _ = vault.read(path)
    except Exception as exc:  # noqa: BLE001
        log.warn(f"[Dialog] note not saved: {type(exc).__name__}: {exc}")
        return False, "I couldn't save that note. Nothing was written."
    if text.strip()[:30] not in body:
        return False, "I wrote the note but couldn't read it back, so I can't promise it saved."
    return True, f"Saved to your notes as “{path.stem}”."


def _save_document(text: str) -> tuple[bool, str]:
    from brain import connectors
    ability = connectors.get("word_write")
    if ability is None:
        return False, "Word isn't available, so I saved nothing. I can keep it as a note instead."
    try:
        result = ability.execute({"source": "text", "text": text.strip()[:6000], "save_as": _title(text)[:50]})
    except Exception as exc:  # noqa: BLE001
        log.warn(f"[Dialog] document not written: {type(exc).__name__}: {exc}")
        return False, "I couldn't write it into Word. Nothing was saved."
    from brain.tools import ToolFailure
    if isinstance(result, ToolFailure):
        return False, str(result).removeprefix("FAILED: ")
    return True, str(result)


def _finish(cap: Capture) -> Turn:
    text = " ".join(cap.parts).strip()
    if not text:
        return Turn("There was nothing to save, so I didn't write anything.")
    ok, said = (_save_document(text) if cap.kind == "document" else _save_note(text))
    return Turn(said, "HAPPY" if ok else "SAD")


def active() -> dict | None:
    """What the UI shows while a dictation is open."""
    with _lock:
        if _state is None or time.time() - _state.last > IDLE_S:
            return None
        return {"kind": _state.kind, "words": sum(len(p.split()) for p in _state.parts), "since": _state.started}


def handle(user_text: str) -> Turn | None:
    """The turn for this message when a dictation owns it (or opens one); None: an ordinary turn."""
    global _state
    text = (user_text or "").strip()
    if not text:
        return None
    with _lock:
        if _state is not None and time.time() - _state.last > IDLE_S:
            _state = None                                   # left open too long: these words are a normal turn
        if _state is not None:
            cap = _state
            if CANCEL.match(text):
                _state = None
                return Turn("Okay, I dropped it. Nothing was saved.")
            if FINISH.match(text):
                _state = None
                return _finish(cap)
            if sum(len(p) for p in cap.parts) + len(text) > MAX_CHARS:
                _state = None
                done = _finish(cap)
                return Turn("That's a lot, so I stopped there. " + done.say, done.tag)
            cap.parts.append(text)
            cap.last = time.time()
            return Turn(_ACKS[len(cap.parts) % len(_ACKS)], "FOCUSED", "listening")
        opened = detect_start(text)
        if opened is None:
            return None
        kind, content = opened
        if content:
            return _finish(Capture(kind, parts=[content]))
        _state = Capture(kind)
        what = "the document" if kind == "document" else "it"
        return Turn(f"I'm listening. Tell me what to write, then say done.", "FOCUSED", "listening")


def reset() -> None:
    global _state
    with _lock:
        _state = None
