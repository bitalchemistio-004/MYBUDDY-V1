"""
tts/expressions.py — Non-verbal expression, in both directions

**What it does:**
Edge-TTS reads exactly what it is given. That is why MBUDDY sounds robotic
around anything that is not a plain sentence:

- `*laughs*` is read out as the word "laughs", asterisks and all.
- `Mmmmm` with five m's is spelled letter by letter instead of hummed.
- `hahaha` becomes a flat recitation rather than a laugh.

Neural voices *can* produce a convincing hum, laugh, or hesitation — they just
need the conventional spelling for it. This module rewrites MBUDDY's replies
into spellings the synthesizer renders naturally, and removes stage directions
that would otherwise be read aloud.

It also works the other way. Whisper transcribes the user's own vocalizations
("Mm-hmm", "Haha", "Uh..."), and those carry real meaning that is thrown away
if treated as words. `detect_user_expressions()` picks them out so MBUDDY can
know you laughed or hesitated rather than thinking you said something odd.

**How to use:**
```python
from tts.expressions import voice_ready, detect_user_expressions

# Outgoing: just before synthesis
spoken = voice_ready("[HAPPY] *laughs* mmmmm, maybe you're right")
# -> "Haha. Hmm, maybe you're right"

# Incoming: on the transcript, before it reaches the brain
cues = detect_user_expressions("haha yeah mm-hmm")
# -> ["laughing", "agreeing"]
```
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ── Outgoing: spellings Edge-TTS renders as real vocalizations ────────
#
# Each entry maps a pattern MBUDDY might write to the spelling a neural
# voice actually performs. The replacements are deliberately conservative:
# two m's hum, five m's get spelled out; "Haha" laughs, "hahahaha" recites.
VOCALIZATIONS: List[Tuple[re.Pattern, str]] = [
    # Laughter of any length collapses to the two-syllable form that renders.
    (re.compile(r'\b(?:ha){3,}h?\b', re.IGNORECASE), 'Haha'),
    (re.compile(r'\bha[- ]?ha[- ]?ha\b', re.IGNORECASE), 'Haha'),
    (re.compile(r'\b(?:hehe|heh|hehehe)\b', re.IGNORECASE), 'Heh'),
    (re.compile(r'\b(?:lol|lmao|rofl)\b', re.IGNORECASE), 'Haha'),

    # Thinking hums. Long runs of m or h are the worst offenders.
    (re.compile(r'\bm{2,}\b', re.IGNORECASE), 'Hmm'),
    (re.compile(r'\bh?m{3,}\b', re.IGNORECASE), 'Hmm'),
    (re.compile(r'\bhm{2,}\b', re.IGNORECASE), 'Hmm'),
    (re.compile(r'\bhu?mm+\b', re.IGNORECASE), 'Hmm'),

    # Agreement and hesitation particles.
    (re.compile(r'\bmm+[- ]?hm+\b', re.IGNORECASE), 'Mm-hmm'),
    (re.compile(r'\buh+[- ]?huh\b', re.IGNORECASE), 'Uh-huh'),
    (re.compile(r'\bu+h+\b', re.IGNORECASE), 'Uh'),
    (re.compile(r'\ber+m+\b', re.IGNORECASE), 'Erm'),

    # Reactions. Trailing vowels get spelled out, so normalise the length.
    (re.compile(r'\bo{2,}h+\b', re.IGNORECASE), 'Ooh'),
    (re.compile(r'\ba+h{2,}\b', re.IGNORECASE), 'Ah'),
    (re.compile(r'\bwo{2,}w+\b', re.IGNORECASE), 'Wow'),
    (re.compile(r'\bye+a+h+\b', re.IGNORECASE), 'Yeah'),
    (re.compile(r'\bpf+t+\b', re.IGNORECASE), 'Pff'),
    (re.compile(r'\bug+h+\b', re.IGNORECASE), 'Ugh'),
]

# ── Stage directions ──────────────────────────────────────────────────
#
# Models like to write `*laughs*` or `(sighs)`. Some of these have a spoken
# equivalent; the rest are performance notes that must never be read aloud.
# A value of "" means remove it silently.
STAGE_DIRECTIONS: Dict[str, str] = {
    "laughs": "Haha.",
    "laughing": "Haha.",
    "laugh": "Haha.",
    "chuckles": "Heh.",
    "chuckling": "Heh.",
    "giggles": "Heh.",
    "snickers": "Heh.",
    "sighs": "Hh...",
    "sighing": "Hh...",
    "exhales": "Hh...",
    "groans": "Ugh.",
    "gasps": "Ah!",
    "hums": "Hmm.",
    "thinking": "Hmm...",
    "pauses": "...",
    "beat": "...",
    "clears throat": "Ahem.",
    # Everything below is a note to a performer, not a sound.
    "smiles": "",
    "smiling": "",
    "grins": "",
    "shrugs": "",
    "nods": "",
    "winks": "",
    "frowns": "",
    "raises eyebrow": "",
    "deadpan": "",
    "quietly": "",
    "softly": "",
}

# `*laughs*`, `(laughs)`, `[laughs]`, `<laughs>` — all four show up.
_DIRECTION_RE = re.compile(r'[\*\(\[\<]\s*([a-z][a-z \'-]{1,24}?)\s*[\*\)\]\>]', re.IGNORECASE)


def _replace_direction(match: re.Match) -> str:
    body = match.group(1).strip().lower()
    if body in STAGE_DIRECTIONS:
        return STAGE_DIRECTIONS[body]
    # An unknown direction is still safer removed than read aloud — a verb
    # in asterisks is never something MBUDDY meant to say.
    if len(body.split()) <= 3 and body.endswith(("s", "ing", "ed")):
        return ""
    return match.group(0)


def expand_stage_directions(text: str) -> str:
    """
    Turn `*laughs*` into a sound, and drop notes that are not sounds.

    Args:
        text: reply text, possibly containing bracketed directions

    Returns:
        Text with directions either voiced or removed.
    """
    return _DIRECTION_RE.sub(_replace_direction, text or "")


def normalize_vocalizations(text: str) -> str:
    """
    Respell hums, laughs, and hesitations so the voice performs them.

    `Mmmmm` becomes `Hmm`, `hahaha` becomes `Haha`. The synthesizer renders
    the short conventional forms as real vocalizations and spells out the
    stretched ones, so shortening them is what makes them sound human.
    """
    out = text or ""
    for pattern, replacement in VOCALIZATIONS:
        out = pattern.sub(replacement, out)
    return out


def add_breath_marks(text: str) -> str:
    """
    Give the synthesizer somewhere to breathe.

    Edge-TTS treats an ellipsis and an em-dash as genuine pauses, so a
    vocalization that runs straight into the next clause sounds rushed. This
    inserts the pause a person would naturally leave after one.
    """
    out = text or ""
    # A thinking sound is followed by a beat, not a comma.
    out = re.sub(r'\b(Hmm|Erm|Uh)\b\s*,\s*', r'\1... ', out)
    # And never runs directly into a word.
    out = re.sub(r'\b(Hmm|Erm)\b\s+(?=[A-Za-z])', r'\1... ', out)
    # Collapse any run of dots the model produced into a single ellipsis.
    out = re.sub(r'\.{4,}', '...', out)
    return out


def voice_ready(text: str) -> str:
    """
    Prepare a reply for synthesis so its expressions actually sound human.

    Runs the full outgoing pass: stage directions, then vocalization
    respelling, then breath marks. Safe to call on text that contains none
    of those — it returns ordinary sentences unchanged apart from whitespace.

    Args:
        text: MBUDDY's reply, after emotion tags have been stripped

    Returns:
        The text to hand to Edge-TTS.
    """
    out = expand_stage_directions(text or "")
    out = normalize_vocalizations(out)
    out = add_breath_marks(out)
    # Tidy the spacing the substitutions leave behind.
    out = re.sub(r'[ \t]{2,}', ' ', out)
    out = re.sub(r'\s+([,.!?;:])', r'\1', out)
    out = re.sub(r'^\s*[,.]\s*', '', out)
    # A voiced direction ends a sentence, so whatever follows starts one.
    out = re.sub(
        r'([.!?])\s+([a-z])',
        lambda m: m.group(1) + ' ' + m.group(2).upper(),
        out,
    )
    return out.strip()


# ── Expression to face ────────────────────────────────────────────────
#
# A synthesizer can only go so far: Edge-TTS will produce a passable hum or
# "Haha", but it cannot really laugh. Rather than accept a flat reading, the
# expression is also routed to the OLED, so the body shows what the voice
# cannot carry. Every value here is a real emotion from FaceEmotions.hpp.
EXPRESSION_EMOTION: Dict[str, str] = {
    "laughs": "Glee",
    "laughing": "Glee",
    "laugh": "Glee",
    "chuckles": "Happy",
    "chuckling": "Happy",
    "giggles": "Glee",
    "snickers": "Skeptic",
    "sighs": "Frustrated",
    "sighing": "Frustrated",
    "exhales": "Sleepy",
    "groans": "Annoyed",
    "gasps": "Surprised",
    "hums": "Focused",
    "thinking": "Focused",
    "pauses": "Focused",
    "smiles": "Happy",
    "smiling": "Happy",
    "grins": "Glee",
    "shrugs": "Unimpressed",
    "winks": "Skeptic",
    "frowns": "Worried",
    "raises eyebrow": "Skeptic",
    "deadpan": "Unimpressed",
}

# The same routing for vocalizations written inline rather than as directions.
VOCALIZATION_EMOTION: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\b(?:ha){2,}h?\b|\bhaha\b|\blol\b|\blmao\b', re.IGNORECASE), "Glee"),
    (re.compile(r'\b(?:hehe|heh)\b', re.IGNORECASE), "Happy"),
    (re.compile(r'\bh?m{2,}\b|\bhmm\b|\berm\b', re.IGNORECASE), "Focused"),
    (re.compile(r'\bug+h+\b|\bpf+t+\b', re.IGNORECASE), "Annoyed"),
    (re.compile(r'\bwo{1,}w+\b|\bwhoa\b|\bo{2,}h\b', re.IGNORECASE), "Surprised"),
    (re.compile(r'\bah+\b', re.IGNORECASE), "Awe"),
]


def expression_emotion(text: str) -> str | None:
    """
    The face MBUDDY should wear for an expression in this text.

    Use it to override the emotion sent to the display for one chunk, so a
    laugh reads as a laugh even though the synthesizer can only approximate
    the sound. Stage directions are checked first because they are explicit;
    inline vocalizations are the fallback.

    Args:
        text: a reply chunk, before or after `voice_ready`

    Returns:
        An emotion name from the firmware's set, or None when the text
        carries no expression and the current mood should stand.
    """
    raw = text or ""
    for match in _DIRECTION_RE.finditer(raw):
        body = match.group(1).strip().lower()
        if body in EXPRESSION_EMOTION:
            return EXPRESSION_EMOTION[body]
    for pattern, emotion in VOCALIZATION_EMOTION:
        if pattern.search(raw):
            return emotion
    return None


# ── Incoming: what the user's own noises mean ─────────────────────────
#
# Whisper transcribes vocalizations as words. Read literally they are
# nonsense; read as paralinguistic cues they tell MBUDDY how the user is
# reacting, which is exactly what a person listens for.
USER_CUES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\b(?:ha){2,}|haha|hehe|lol|lmao\b', re.IGNORECASE), "laughing"),
    (re.compile(r'\bmm+[- ]?hm+\b|\buh+[- ]?huh\b|\byeah yeah\b', re.IGNORECASE), "agreeing"),
    (re.compile(r'\bu+h+\b|\berm+\b|\bu+m+\b', re.IGNORECASE), "hesitating"),
    (re.compile(r'\bh?m{2,}\b', re.IGNORECASE), "thinking"),
    (re.compile(r'\bug+h+\b|\bpf+t+\b|\btsk\b', re.IGNORECASE), "exasperated"),
    (re.compile(r'\bwo+w+\b|\bwhoa\b|\bo{2,}h\b', re.IGNORECASE), "surprised"),
    (re.compile(r'\bsigh(?:s|ing)?\b', re.IGNORECASE), "sighing"),
    # Whisper's own annotations for non-speech audio.
    (re.compile(r'\[\s*(?:laughter|laughs|chuckles)\s*\]', re.IGNORECASE), "laughing"),
    (re.compile(r'\[\s*(?:sighs?|breath)\s*\]', re.IGNORECASE), "sighing"),
]

# Arabic-script equivalents, since the user speaks both languages.
USER_CUES_AR: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'ههه+|هاها'), "laughing"),
    (re.compile(r'ممم+|مهم'), "thinking"),
    (re.compile(r'\bآه+\b|اوف'), "exasperated"),
]


def detect_user_expressions(text: str) -> List[str]:
    """
    Find the non-verbal cues in what the user just said.

    Args:
        text: the transcript

    Returns:
        Cue names such as `["laughing", "agreeing"]`, in a stable order and
        without duplicates. Empty when the utterance is plain speech.
    """
    found: List[str] = []
    for pattern, cue in USER_CUES + USER_CUES_AR:
        if pattern.search(text or "") and cue not in found:
            found.append(cue)
    # "Mm-hmm" is one gesture, not two. Its trailing hum matches the thinking
    # pattern as well, so agreement wins and the duplicate is dropped.
    if "agreeing" in found and "thinking" in found:
        found.remove("thinking")
    return found


def strip_user_expressions(text: str) -> str:
    """
    Remove vocalizations from a transcript, leaving the actual words.

    Useful when the cue has already been captured separately and the words
    alone should drive intent matching.
    """
    out = text or ""
    for pattern, _cue in USER_CUES + USER_CUES_AR:
        out = pattern.sub(' ', out)
    return re.sub(r'\s{2,}', ' ', out).strip()


def describe_user_expressions(cues: List[str]) -> str:
    """
    Render detected cues as one line for the prompt.

    Kept deliberately short — this is context for how the user sounded, and
    it competes for room against memory and the world model.
    """
    if not cues:
        return ""
    if len(cues) == 1:
        return f"(USER_NAME sounded {cues[0]} as he said this.)"
    return f"(USER_NAME sounded {', '.join(cues[:-1])} and {cues[-1]} as he said this.)"
