"""
stt/language_detect.py — Language & text utilities.

Provides:
  - Script-level language detectors (Arabic / English / mixed)
  - Whisper language-code normaliser
  - Text normalisation, fingerprinting, and similarity helpers
  - Wake-phrase and goodbye-phrase matchers
  - Hallucination filter
"""
import hashlib
import re

from core.config import (
    STT_HALLUCINATIONS, STT_SUBSTRING_HALLUCINATIONS,
    FINAL_GOODBYE_PHRASES, GOODBYE_CONFIRM_PHRASES,
    SOFT_GOODBYE_PHRASES, WAKE_PHRASES, HALLUCINATION_SHORT_PHRASES,
    STT_ECHO_GARBAGE_WORDS, STT_MIN_AVG_LOGPROB, STT_MAX_NO_SPEECH_PROB,
    STT_MAX_NO_SPEECH_PROB_SHORT, STT_GARBAGE_AVG_LOGPROB,
)


# ── Script detectors ──────────────────────────────────────────

def is_arabic(text: str) -> bool:
    """Returns True if text contains Arabic Unicode characters."""
    return any('\u0600' <= c <= '\u06FF' for c in text)


def is_english(text: str) -> bool:
    """Returns True if text contains any Latin alphabetic characters."""
    return any(c.isalpha() and c.isascii() for c in text)


def is_mixed(text: str) -> bool:
    """Returns True if text contains both Arabic and Latin characters."""
    return is_arabic(text) and is_english(text)


# ── Arabizi / Hybrid detection ──────────────────────────────

ARABIZI_KEYWORDS = frozenset({
    "kifak", "kifik", "kifmik", "keefak", "keefick", "keefuck", "mnih", "mni7", 
    "shou", "shu", "lesh", "leish", "liom", "lyom", "habibi", "yalla", "wen", "wayn", 
    "marhaba", "hala", "ahla", "merci", "shukran", "baddi", "badde", "ma3", "bass", 
    "mni7a", "mniha", "ktir", "ktayr", "ya", "3ayb", "msh", "shay", "b7alik",
    "keefah", "keefik", "ana", "fi", "wa", "jayyik", "7alik", "ta7ta9a",
})

ARABIZI_WEAK_KEYWORDS = frozenset({"ya", "fi", "wa", "eh", "la"})
ARABIZI_STRONG_KEYWORDS = ARABIZI_KEYWORDS - ARABIZI_WEAK_KEYWORDS
def _latin_tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def contains_arabizi(text: str) -> bool:
    """True when any Latin-script portion is transliterated Arabic."""
    tokens = _latin_tokens(text)
    if not tokens:
        return False
    if any(t in ARABIZI_STRONG_KEYWORDS for t in tokens):
        return True
    weak_hits = sum(1 for t in tokens if t in ARABIZI_WEAK_KEYWORDS)
    return weak_hits >= 2


def is_arabizi(text: str) -> bool:
    """Returns True if English-script text contains Lebanese Arabizi keywords."""
    if is_arabic(text):
        return False
    return contains_arabizi(text)


# ── Text normalisation helpers ────────────────────────────────

def normalize_text(text: str) -> str:
    return " ".join(text.lower().strip().split())


def text_fingerprint(text: str) -> str:
    return hashlib.md5(normalize_text(text).encode()).hexdigest()


def text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa = set(normalize_text(a).split())
    sb = set(normalize_text(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def tokenize_text(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


# ── Phrase matchers ───────────────────────────────────────────

def has_phrase(text: str, phrases: tuple, max_extra_tokens: int = 2) -> bool:
    """Return True if *text* contains any phrase from *phrases* with at most
    *max_extra_tokens* surrounding tokens."""
    text_tokens = tokenize_text(text)
    if not text_tokens:
        return False
    for phrase in phrases:
        phrase_tokens = tokenize_text(phrase)
        if not phrase_tokens or len(phrase_tokens) > len(text_tokens):
            continue
        for idx in range(len(text_tokens) - len(phrase_tokens) + 1):
            if text_tokens[idx : idx + len(phrase_tokens)] != phrase_tokens:
                continue
            if len(text_tokens) - len(phrase_tokens) <= max_extra_tokens:
                return True
    return False


GOODBYE_FILLER_WORDS = {
    "ok", "okay", "alright", "aight", "yeah", "yep", "yes", "please",
    "now", "then", "buddy", "mbuddy", "m", "hey", "yo", "yalla",
    "habibi", "bro", "man",
    "\u064a\u0644\u0627", "\u062d\u0628\u064a\u0628\u064a",
}


def _is_command_phrase(text: str, phrases: tuple, max_extra_tokens: int = 3) -> bool:
    """Match short command-like phrases without triggering on arbitrary sentences."""
    words = _wake_words(text)
    if not words:
        return False

    for phrase in phrases:
        phrase_words = tokenize_text(phrase)
        if not phrase_words or len(phrase_words) > len(words):
            continue
        for idx in range(len(words) - len(phrase_words) + 1):
            if words[idx : idx + len(phrase_words)] != phrase_words:
                continue
            before = words[:idx]
            after = words[idx + len(phrase_words) :]
            if len(before) + len(after) > max_extra_tokens:
                continue
            if all(w in GOODBYE_FILLER_WORDS for w in before + after):
                return True
    return False


def is_final_goodbye(text: str) -> bool:
    """True when the user gives a direct command to sleep/end the session."""
    return _is_command_phrase(text, FINAL_GOODBYE_PHRASES, max_extra_tokens=3)


def is_soft_goodbye(text: str) -> bool:
    """True when the user may be leaving, but MBUDDY should confirm first."""
    return _is_command_phrase(text, SOFT_GOODBYE_PHRASES, max_extra_tokens=3)


def is_goodbye_confirmation(text: str) -> bool:
    """True when a short reply confirms a pending goodbye."""
    return _is_command_phrase(text, GOODBYE_CONFIRM_PHRASES, max_extra_tokens=1)


def _wake_words(text: str) -> list[str]:
    """Tokenize and strip trailing punctuation (hey. → hey)."""
    return [re.sub(r"[^\w]", "", w, flags=re.UNICODE) for w in tokenize_text(text) if w]


def should_wake(text: str) -> bool:
    """Return True if *text* is a recognised wake phrase."""
    words = _wake_words(text)
    if not words:
        return False

    wake_phrases = tuple(tokenize_text(phrase) for phrase in WAKE_PHRASES)
    for phrase_words in wake_phrases:
        if not phrase_words or len(words) < len(phrase_words):
            continue
        for idx in range(len(words) - len(phrase_words) + 1):
            if words[idx : idx + len(phrase_words)] == phrase_words:
                return True
    return False


def is_wake_only(text: str) -> bool:
    """True if utterance is only a wake phrase (no real question) — skip when already awake."""
    if not should_wake(text):
        return False
    words = _wake_words(text)
    if len(words) > 3:
        return False
    # Question words → real intent, not wake-only
    question_hints = {
        "who", "what", "why", "how", "when", "where", "do", "does", "did",
        "are", "is", "can", "you", "i", "me", "my", "like", "know", "tell",
    }
    return not (set(words) & question_hints)


INCOMPLETE_TRAILING_WORDS = {
    "the", "a", "an", "and", "or", "but",
    "by",
}


def is_incomplete_transcript(text: str) -> bool:
    """True when STT returned a visibly cut-off fragment."""
    norm = normalize_text(text)
    if not norm:
        return True
    if re.search(r"(\.\.\.|\u2026)\s*$", text.strip()):
        return True
    words = norm.rstrip(".?!,").split()
    return bool(words and words[-1] in INCOMPLETE_TRAILING_WORDS)


# ── Hallucination filter ──────────────────────────────────────

def is_excessively_repetitive(text: str) -> bool:
    """
    True when one word dominates the transcript in the shape Whisper
    hallucination actually takes — not just "repeated," but repeated in
    separate, scattered spots with other (often garbled) words woven
    between them, as opposed to someone genuinely saying a word several
    times in a row for emphasis ("no no no I don't want that").

    'اهلا وسهلا اهلا كيف حالكم اهلا ايدين 25 اهلا سهلا اهلا اردنا' — "hello"
    five separate times, each isolated by other words, in twelve words
    total — was a real capture from this device, not a hypothetical, and
    is exactly what this catches. "لا لا لا ما بدي هيك" ("no no no, I don't
    want it") has the same 50% dominant-word ratio but all three "no"s
    consecutive, and must NOT be flagged — that's genuine speech.

    A second real capture — 'ماذا تريد أن تغير جدتك؟ أفيوني نعم نحن نحن نحن
    نحن نحن نحن ن' ("...yes we we we we we we w") — showed a single word
    repeated six times *consecutively* isn't safe to wave through either:
    genuine emphatic repetition tops out around two or three ("no no no"),
    never six in a row. A long enough single run is caught on its own,
    separately from the scattered-runs check above, rather than folding it
    into the ratio threshold — the six "نحن" were only 43% of this
    transcript's words, well under the 60% a pure-ratio rule would need.

    Splits on whitespace directly rather than Latin-stripping first, so
    this applies the same way to Arabic, English, or anything else — unlike
    the two callers' own early-return for Arabic script, which used to skip
    hallucination screening for it entirely.
    """
    words = text.strip().split()
    if len(words) < 5:
        return False
    counts: dict[str, int] = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    dominant_word, dominant = max(counts.items(), key=lambda kv: kv[1])
    if dominant < 3 or (dominant / len(words)) < 0.35:
        return False

    # Runs the dominant word forms — "no no no X Y" is one run of length 3;
    # "X no Y no Z no" is three runs of length 1 each. Multiple separate
    # runs is the scattered hallucination shape; a single run reads as
    # normal emphatic repetition unless it's implausibly long on its own
    # (5+) or the word is so overwhelming (60%+) that even one run with
    # nothing else isn't plausible as real speech.
    runs = 0
    longest_run = 0
    current_run = 0
    in_run = False
    for w in words:
        if w == dominant_word:
            current_run = current_run + 1 if in_run else 1
            if not in_run:
                runs += 1
            in_run = True
            longest_run = max(longest_run, current_run)
        else:
            in_run = False
            current_run = 0
    return runs >= 2 or longest_run >= 5 or (dominant / len(words)) >= 0.6


def is_probable_hallucination(text: str, *, approved_langs: set[str] | None = None) -> bool:
    """Detect Whisper noise hallucinations — never block real non-English speech."""
    approved = approved_langs or {"en", "ar", "mixed"}

    # Real wake calls must never be treated as YouTube-style hallucinations
    if should_wake(text):
        return False

    # Runs before any language-based exemption below — a word looping
    # through most of the transcript is Whisper failing, not real speech,
    # in any language.
    if is_excessively_repetitive(text):
        return True

    # Allow Arabic script - it's real speech, not hallucination
    # The language policy layer will handle approval/switching
    if is_arabic(text):
        return False

    # Arabic / mixed utterances are real speech, not YouTube hallucinations
    if is_arabic(text) and ("ar" in approved or "mixed" in approved):
        return False
    if is_mixed(text) and ("mixed" in approved or "ar" in approved):
        return False

    raw_cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", text.strip().lower())
    cleaned = " ".join(raw_cleaned.split())

    # Non-Latin-only content: only block if empty after stripping Latin
    if not cleaned and is_arabic(text):
        return False

    # 1. Broad substring match for high-confidence "dangerous" hallucination patterns
    if any(h in cleaned for h in STT_SUBSTRING_HALLUCINATIONS):
        return True

    if re.match(r"^(transcription|translation|subtitle|subtitles) by\b", cleaned):
        return True

    if "transcribe spoken english accurately" in cleaned:
        return True
    if "english language only" in cleaned:
        return True

    # 2. Exact or very short match for common phrases (like 'thank you')
    if cleaned in STT_HALLUCINATIONS:
        return True

    if cleaned.startswith("thank you") and len(cleaned.split()) <= 4:
        return True

    if "amara" in cleaned or "subtitles by" in cleaned:
        return True

    words = cleaned.split()
    if not words:
        return not is_arabic(text)

    if len(words) <= 3 and all(w in HALLUCINATION_SHORT_PHRASES for w in words):
        # Allow short wake-shaped phrases (hey / hi / hey buddy)
        if should_wake(text):
            return False
        return True

    return False


def transcript_confidence(
    text: str,
    *,
    duration_s: float,
    rms: float,
    avg_logprob: float | None = None,
    no_speech_prob: float | None = None,
    approved_langs: set[str] | None = None,
) -> str:
    """
    "ok", "uncertain" or "garbage".

    Garbage fails a check other than the confidence floor (cut off, looping,
    mostly non-speech, ...) or sits below STT_GARBAGE_AVG_LOGPROB. Uncertain
    only misses the STT_MIN_AVG_LOGPROB floor: real speech that Whisper was
    unsure about, which the brain gets with a note rather than dropping it.
    """
    kwargs = dict(duration_s=duration_s, rms=rms, no_speech_prob=no_speech_prob, approved_langs=approved_langs)
    if not is_unreliable_transcript(text, avg_logprob=avg_logprob, **kwargs):
        return "ok"
    if is_unreliable_transcript(text, avg_logprob=None, **kwargs):
        return "garbage"
    if avg_logprob is not None and avg_logprob < STT_GARBAGE_AVG_LOGPROB:
        return "garbage"
    return "uncertain"


def settle_confidence(confidence: str, *, detected_lang: str, conversation_lang: str) -> str:
    """
    Unclear Arabic in a conversation that is not in Arabic is treated as
    noise. Arabic is optional for MBUDDY, and local Whisper turns room noise
    into Arabic-looking text; passing it on made the model reply in Arabic.
    """
    if confidence == "uncertain" and detected_lang == "ar" and conversation_lang != "ar":
        return "garbage"
    return confidence


def is_unreliable_transcript(
    text: str,
    *,
    duration_s: float,
    rms: float,
    avg_logprob: float | None = None,
    no_speech_prob: float | None = None,
    approved_langs: set[str] | None = None,
) -> bool:
    """
    Reject likely mis-hears: speaker echo, noise, or ultra-low Whisper confidence.
    Wake phrases are never rejected here.
    """
    if should_wake(text):
        return False

    if is_incomplete_transcript(text):
        return True

    # Before any language-based exemption below: a word looping through
    # most of the transcript is Whisper failing, not real speech, in any
    # language — Arabic transcripts used to skip straight past this
    # function via the exemption below with no hallucination screening at
    # all, which is what let a real repeated-word hallucination through.
    if is_excessively_repetitive(text):
        return True

    short_ok_names = {
        "taha", "taher", "buddy",
        "mhm", "yes", "no", "ok", "okay",
    }
    raw_words = text.strip().split()
    is_short_known_word = len(raw_words) <= 2 and all(
        w.strip(".,!?").lower() in short_ok_names for w in raw_words
    )

    # Whisper's own confidence signals, checked regardless of language —
    # also before the Arabic/mixed exemption below. A real capture from
    # this device had Arabic text hallucinated at avg_logprob -1.18 sail
    # through to a full reply because Arabic skipped straight past every
    # check below, while an equally low-confidence Japanese hallucination
    # was correctly caught by these same thresholds moments later. The
    # checks work; they just weren't being applied to Arabic at all.
    if no_speech_prob is not None and no_speech_prob > STT_MAX_NO_SPEECH_PROB_SHORT and len(raw_words) <= 3:
        return True
    if avg_logprob is not None and avg_logprob < STT_MIN_AVG_LOGPROB:
        if not (is_short_known_word and duration_s >= 0.3):
            return True
    if no_speech_prob is not None and no_speech_prob > STT_MAX_NO_SPEECH_PROB:
        return True

    # Allow Arabic and mixed past here — language policy will handle
    # approval. Repetition and Whisper-confidence screening above both
    # already ran regardless of language.
    if is_arabic(text):
        return False
    if is_mixed(text):
        return False

    norm = normalize_text(text)
    words = norm.split()
    if not words:
        return True

    if len(words) <= 2 and all(w.strip(".,!?") in short_ok_names for w in words):
        return False

    if len(words) == 1:
        w = words[0].strip(".,!?")
        if w in STT_ECHO_GARBAGE_WORDS:
            return True
        if duration_s < 0.9 and len(w) <= 6:
            return True

    if len(words) <= 2 and duration_s < 0.45:
        return True

    return False
