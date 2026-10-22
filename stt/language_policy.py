"""
stt/language_policy.py - English/Arabic listen/speak policy.

- Never treat non-English script as hallucination by default.
- English, Arabic, and Arabic+English mixed speech are the only response modes.
- Unsupported languages are acknowledged, but never adopted for TTS/LLM replies.
"""
from __future__ import annotations
import re

from stt.language_detect import (
    contains_arabizi,
    has_phrase,
    is_arabic,
    is_english,
    is_mixed,
)


EDGE_TTS_VOICES: dict[str, str] = {
    "en": "en-US-AvaNeural",
    "ar": "ar-BH-LailaNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "tr": "tr-TR-EmelNeural",
    "mixed": "en-US-AvaNeural",
}

EDGE_TTS_FALLBACK: dict[str, str] = {
    "en": "en-US-JennyNeural",
    "ar": "ar-SA-ZariyahNeural",
    "mixed": "en-US-JennyNeural",
}

LANG_LABELS: dict[str, str] = {
    "en": "English",
    "ar": "Arabic",
    "fr": "French",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "tr": "Turkish",
    "mixed": "Arabic and English",
    "arabizi": "Arabizi",
}

DEFAULT_APPROVED_LANGS = frozenset({"en"})
ALLOWED_RESPONSE_LANGS = frozenset({"en", "ar", "mixed"})
UNSUPPORTED_STT_LANGS = frozenset({"fr", "es", "de", "it", "pt", "tr"})

NON_ENGLISH_LATIN_MARKERS = frozenset({
    "hola", "vamos", "amigo", "amiga", "gracias", "bonjour", "merci",
    "salut", "oui", "non", "como", "estas", "estoy", "buenos", "dias",
})

LANG_APPROVE_PHRASES = (
    "yes", "yeah", "yep", "yup", "sure", "okay", "ok", "please",
    "go ahead", "do it", "switch", "absolutely", "definitely",
    "aywa", "eh", "aiwa", "tab", "tab3an", "meye", "mashy",
    "نعم", "ايوه", "أيوه", "اكيد", "أكيد", "يلا", "تمام", "اوك",
)

LANG_DECLINE_PHRASES = (
    "no", "nope", "nah", "don't", "do not", "keep english", "english only",
    "stay english", "la", "mish", "لا", "لأ",
)

WHISPER_TO_INTERNAL: dict[str, str] = {
    "en": "en", "english": "en",
    "ar": "ar", "arabic": "ar",
    "fr": "fr", "french": "fr",
    "es": "es", "spanish": "es",
    "de": "de", "german": "de",
    "it": "it", "italian": "it",
    "pt": "pt", "portuguese": "pt",
    "tr": "tr", "turkish": "tr",
}


def map_whisper_lang(whisper_lang: str) -> str:
    if not whisper_lang:
        return "en"
    key = whisper_lang.strip().lower()
    return WHISPER_TO_INTERNAL.get(key, key[:2] if len(key) >= 2 else "en")


def infer_language(text: str, whisper_lang: str) -> str:
    """Script in the transcript beats Whisper's language tag."""
    if contains_arabizi(text):
        return "arabizi"
    if is_mixed(text):
        return "mixed"
    if is_arabic(text):
        return "ar"
    whisper_detected = map_whisper_lang(whisper_lang)
    if whisper_detected in UNSUPPORTED_STT_LANGS and not is_clear_english_transcript(text):
        return whisper_detected
    if is_english(text):
        return "en"
    return whisper_detected


def is_clear_english_transcript(text: str) -> bool:
    """True for ordinary English text even when Whisper's language tag is wrong."""
    words = [w.strip(".,!?;:").lower() for w in text.split()]
    words = [w for w in words if w]
    if not words:
        return False

    wake_like = {"hey", "buddy", "hello", "hi"}
    if len(words) <= 2 and all(w in wake_like for w in words):
        return True

    common = {
        "i", "you", "me", "my", "your", "we", "the", "a", "an", "and", "or",
        "is", "are", "am", "was", "were", "do", "does", "did", "can", "could",
        "would", "should", "what", "why", "how", "when", "where", "who",
        "tell", "explain", "check", "this", "that", "song", "name", "time",
        "please", "hello", "hey", "thanks", "thank", "yes", "no", "okay", "ok",
    }
    hits = sum(1 for w in words if w in common)
    return hits >= 2 or (len(words) >= 4 and hits / len(words) >= 0.5)


def has_non_english_latin_marker(text: str) -> bool:
    tokens = set(re.findall(r"[a-zA-Z]+", text.lower()))
    return bool(tokens & NON_ENGLISH_LATIN_MARKERS)


def is_likely_english_misdetect(text: str) -> bool:
    """Allow tiny English control utterances when Whisper mislabeled them Arabic."""
    words = [w.strip(".,!?;:").lower() for w in text.split()]
    words = [w for w in words if w]
    if not words:
        return False

    wake_like = {"hey", "buddy", "hello", "hi"}
    if len(words) <= 2 and all(w in wake_like for w in words):
        return True

    return text.strip().lower() in {
        "check this song",
        "play this song",
        "what time is it",
    }


def is_lang_approval(text: str) -> bool:
    return has_phrase(text, LANG_APPROVE_PHRASES, max_extra_tokens=2)


def is_lang_decline(text: str) -> bool:
    return has_phrase(text, LANG_DECLINE_PHRASES, max_extra_tokens=2)


def lang_label(code: str) -> str:
    return LANG_LABELS.get(code, code.capitalize())


def offer_message(lang_code: str) -> str:
    name = lang_label(lang_code)
    if lang_code not in ALLOWED_RESPONSE_LANGS:
        return (
            f"[NORMAL] I heard {name}, but I only speak English or Arabic. "
            f"Switch to one of those and I will follow."
        )
    return (
        f"[NORMAL] You are talking in {name} language. "
        f"Do you want me to respond with it too?"
    )


def approval_ack(lang_code: str) -> str:
    name = lang_label(lang_code)
    if lang_code == "ar":
        return "[HAPPY] Okay, Arabic it is. I'm listening."
    return f"[HAPPY] Okay - we'll talk in {name}."


def decline_ack() -> str:
    return "[NORMAL] Got it - we'll stick with English."


def voice_for_lang(lang_code: str) -> str:
    return EDGE_TTS_VOICES.get(lang_code, EDGE_TTS_VOICES["en"])


def voice_fallback(lang_code: str) -> str | None:
    return EDGE_TTS_FALLBACK.get(lang_code)


def script_balance(text: str) -> tuple[float, int, int]:
    """
    Measure how much of an utterance is Arabic script versus Latin script.

    Returns:
        (arabic_share, arabic_letters, latin_letters) where arabic_share is
        0.0 for pure Latin and 1.0 for pure Arabic. Digits, punctuation and
        whitespace are ignored, so "عندي 3 bugs" is judged on its letters.
    """
    arabic = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    latin = sum(1 for c in text if c.isalpha() and c.isascii())
    total = arabic + latin
    if total == 0:
        return 0.0, 0, 0
    return arabic / total, arabic, latin


def resolve_conversation_lang(
    text: str,
    whisper_lang: str,
    approved: set[str],
    active: str,
) -> str:
    """
    Choose the reply language for one utterance, the way a listener would.

    MBUDDY supports English and Arabic only. Rather than latching onto the
    previous turn, this reads the script actually spoken and follows the user
    when they switch — while keeping enough inertia that one ambiguous word
    does not derail an established language.

    Args:
        text: the transcript
        whisper_lang: the language Whisper reported, used only as a tiebreak
        approved: languages the user has allowed (kept for compatibility)
        active: the language the conversation is currently in

    Returns:
        "en" or "ar".
    """
    stripped = (text or "").strip()
    if not stripped:
        return active if active in ("en", "ar") else "en"

    # Arabic written in Latin letters is not something MBUDDY replies in.
    # Answer in English and let the reply nudge the user back to script.
    if contains_arabizi(stripped):
        return "en"

    share, arabic_letters, latin_letters = script_balance(stripped)

    # Very short or letter-poor utterances ("ok", "hmm", a single name) carry
    # no real evidence. Stay in the language already in play rather than
    # snapping back to English on a two-letter reply. Checked first, because
    # such an utterance would otherwise look like confident pure-Latin input.
    if arabic_letters + latin_letters <= 3 and active in ("en", "ar"):
        return active

    # Unambiguous cases: one script is doing essentially all the work.
    if share >= 0.65:
        return "ar"
    if share <= 0.15 and latin_letters >= 2:
        # Whisper occasionally labels clear English as Arabic on short audio.
        # Trust the script we can actually see over the label.
        return "en"

    # Genuinely code-switched. A Lebanese speaker dropping English technical
    # words into an Arabic sentence is still speaking Arabic, so any real
    # Arabic content keeps the reply in Arabic.
    if arabic_letters >= 3:
        return "ar"

    # Last resort: Whisper's own label, then English.
    if map_whisper_lang(whisper_lang) == "ar":
        return "ar"
    return "en"


def should_offer_language(lang_code: str, approved: set[str]) -> bool:
    """
    Whether to ask the user before replying in a language.

    Always False. MBUDDY supports English and Arabic, and both are spoken
    freely without asking permission — a bilingual listener does not stop to
    negotiate which language to answer in. Anything outside the pair is
    handled as unsupported rather than offered.
    """
    return False


def can_adopt_language(lang_code: str) -> bool:
    return lang_code in ALLOWED_RESPONSE_LANGS


def unsupported_language_ack(lang_code: str) -> str:
    if lang_code == "arabizi":
        return (
            "[NORMAL] I heard Arabizi, but I only accept Arabic script, English, "
            "or real Arabic-English mixed text. Repeat it without Arabizi."
        )
    name = lang_label(lang_code)
    return f"[NORMAL] I cannot switch to {name}. English or Arabic only."


def arabic_char_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    ar = sum(1 for c in letters if "\u0600" <= c <= "\u06FF")
    return ar / len(letters)


# A quoted span is a name or a title (a song, a file, a page): data, not the
# language the reply is written in. Live, MBUDDY's own 'Playing "Bektoub Ismak
# Ya Habibi" by Fairuz' was blocked as the wrong language and replaced by
# "could you ask me that once more?", and speech dropped words from the title.
_QUOTED = re.compile(r'"[^"\n]{1,160}"|“[^”\n]{1,160}”|«[^»\n]{1,160}»|‘[^’\n]{1,160}’')


def outside_quotes(text: str) -> str:
    """The sentence itself, with quoted names and titles taken out."""
    return _QUOTED.sub(" ", text)


_OTHER_LANGUAGE = re.compile(
    r"\b(translat\w*|how (do|would) (you|i) say|say (it|that|this) in|in (french|arabic|spanish|german|italian|"
    r"portuguese|turkish|russian|chinese|japanese|korean|hindi|urdu|persian|farsi|dutch|greek|hebrew|english|lebanese))\b"
    r"|ترجم|بالإنجليزي|بالانجليزي|بالفرنسي|بالفرنساوي|بالعربي|بالإسباني|بالاسباني|باللغة",
    re.IGNORECASE)


def asks_for_other_language(user_text: str) -> bool:
    """The user asked for words in another language (a translation, "say it in French"): a reply in that language is
    the answer, not a wrong-language reply (the field test, 2026-10-19: correct French and Arabic translations were
    blocked and replaced by "could you ask me that once more?")."""
    return bool(_OTHER_LANGUAGE.search(user_text or ""))


def reply_matches_language(text: str, lang_code: str) -> bool:
    """True if spoken reply body matches the requested conversation language."""
    from brain.personality import strip_emotion_tags
    # Only the language is judged here. A missing emotion tag is a format
    # slip, not a wrong language: requiring one here made the local model's
    # untagged English replies all "fail" and get replaced with a fallback.
    body = strip_emotion_tags(text) or text
    if not body.strip():
        return True
    body = outside_quotes(body)  # judge the sentence, not the names and titles in it
    if not re.search(r"\w", body):
        return True
    if lang_code == "ar":
        # Dialect (Lebanese vs. MSA/Egyptian vocabulary) is a style choice,
        # not a language-correctness failure -- only check that the reply is
        # actually Arabic, not which Arabic dialect it leans toward.
        return arabic_char_ratio(body) >= 0.25 or is_arabic(body)
    if lang_code == "en":
        if contains_arabizi(body):
            return False
        if has_non_english_latin_marker(body):
            return False
        return arabic_char_ratio(body) < 0.15
    if lang_code == "mixed":
        if contains_arabizi(body):
            return False
        if has_non_english_latin_marker(body):
            return False
        return arabic_char_ratio(body) < 0.15
    return True


def llm_language_block(lang_code: str) -> str:
    """Hard language directive. Placed near the top and again near the bottom
    of build_conversation_system()'s prompt so it isn't buried under other
    English-language rule text on long-context turns."""
    blocks = {
        "ar": (
            "CRITICAL - LANGUAGE (overrides all examples):\n"
            "USER_NAME spoke Arabic. Your FULL reply after the emotion tag MUST be natural Lebanese Arabic.\n"
            "You MUST use Arabic script (Unicode) for all Arabic words. NEVER use Latin characters/Arabizi.\n"
            "Use one exact English emotion tag first, from the allowed list, like [NORMAL] or [FOCUSED].\n"
            "Never use Arabic bracket tags like [خلاص]. Never use Egyptian/Moroccan wording like بيكونش, عاوز, ازاي, بزاف.\n"
            "Silently infer the practical intent first; do not answer with vague filler. "
            "For food/advice questions, give a concrete direct suggestion.\n"
            "Keep it short and useful. Example: [FOCUSED] إذا قصدك الغدا، خليك على شي خفيف: دجاج أو تونة مع رز وخضرة."
        ),
        "fr": "CRITICAL - LANGUAGE: Reply entirely in French after the emotion tag. No English.",
        "es": "CRITICAL - LANGUAGE: Reply entirely in Spanish after the emotion tag. No English.",
        "de": "CRITICAL - LANGUAGE: Reply entirely in German after the emotion tag. No English.",
        "mixed": (
            "CRITICAL - LANGUAGE: USER_NAME mixed Arabic and English. Understand both, "
            "but reply ENTIRELY and ONLY in English after the emotion tag.\n"
            "NEVER mirror back Arabic words or Arabizi (like 'kifak' or 'habibi'). Respond as if the user spoke in English."
        ),
        "en": "LANGUAGE: Reply ENTIRELY in English after the emotion tag. NEVER use Arabic or Arabizi words.",
    }
    return blocks.get(lang_code, blocks["en"])


def user_message_for_lang(user_text: str, lang_code: str) -> str:
    """
    Return the user's turn exactly as transcribed.

    This used to prefix Arabic turns with a bracketed instruction block. That
    block was not something the user said, yet it was stored in conversation
    history and replayed on every later turn, both distorting the record and
    spending payload that the Groq size limit needs.

    Language direction now lives solely in the system prompt, via
    `llm_language_block()`, which is the single authority on reply language.
    The signature is kept so existing call sites do not need to change.

    Args:
        user_text: the transcript
        lang_code: retained for compatibility; no longer used

    Returns:
        The transcript, unchanged.
    """
    return user_text


def voice_for_reply(text: str, conv_lang: str) -> str:
    """TTS voice follows the words being spoken, not the previous turn's mode."""
    from brain.personality import strip_emotion_tags
    body = strip_emotion_tags(text) or text
    ar_ratio = arabic_char_ratio(body)
    if is_arabic(body) or ar_ratio >= 0.2:
        return voice_for_lang("ar")
    if is_english(body) and ar_ratio < 0.08:
        return voice_for_lang("en")
    return voice_for_lang(conv_lang if conv_lang in EDGE_TTS_VOICES else "en")


def scrub_arabizi_output(text: str) -> str:
    """
    Last-resort filter that removes Latin-script Arabic (Arabizi) from LLM output.
    Useful when the LLM mirrors the user despite system prompts.
    """
    from stt.language_detect import ARABIZI_KEYWORDS
    # Regex to catch Arabizi keywords while preserving punctuation
    # We look for word boundaries but allow for some trailing punctuation
    pattern = r"\b(" + "|".join(re.escape(k) for k in ARABIZI_KEYWORDS) + r")\b"
    
    # We replace with empty string or a placeholder if it's too much
    # But since MBUDDY's tone is informal, just removing them works.
    # Quoted names and titles are kept whole: only the sentence around them is cleaned.
    parts, last = [], 0
    for m in _QUOTED.finditer(text):
        parts.append(re.sub(pattern, "", text[last:m.start()], flags=re.IGNORECASE))
        parts.append(m.group(0))
        last = m.end()
    parts.append(re.sub(pattern, "", text[last:], flags=re.IGNORECASE))
    cleaned = "".join(parts)

    # Clean up double spaces resulting from removals
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
