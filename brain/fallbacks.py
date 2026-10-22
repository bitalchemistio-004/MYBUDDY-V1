"""Deterministic fallback replies for degraded mode."""
from __future__ import annotations

from brain.personality import STATE_TO_TAG


def fallback_reply(reason: str, emotion: str = "Normal", lang: str = "en") -> str:
    tag = STATE_TO_TAG.get(emotion, "[NORMAL]")
    if lang == "ar":
        replies = {
            "stt_empty": f"{tag} ما لقطت عليك. عيدها؟",
            "stt_low_confidence": f"{tag} الصوت وصل مش واضح. عيدها بهدوء؟",
            "stt_language_uncertain": f"{tag} سامحني، عيدها؟",
            "llm_timeout": f"{tag} علقت شوي. عيدلي الطلب.",
            "llm_error": f"{tag} صار خلل عندي. جرب مرة تانية.",
            "tts_error": f"{tag} عندي الجواب، بس صوتي علّق.",
            "reply_language_mismatch": f"{tag} سامحني، جرب اسألني مرة كمان.",
            "search_unavailable": f"{tag} البحث المباشر مش متاح هلأ.",
        }
        return replies.get(reason, f"{tag} أنا بوضع محدود، بس معك.")

    replies = {
        "stt_empty": f"{tag} I didn't catch that. Say it again?",
        "stt_low_confidence": f"{tag} that came in messy. Repeat it clean?",
        "stt_language_uncertain": f"{tag} Sorry, say that again?",
        "llm_timeout": f"{tag} my brain lagged. Give me that again.",
        "llm_error": f"{tag} something glitched in my brain. Try once more.",
        "tts_error": f"{tag} I have the answer, but my voice glitched.",
        "reply_language_mismatch": f"{tag} Sorry, could you ask me that once more?",
        "search_unavailable": f"{tag} live search is not available right now.",
    }
    return replies.get(reason, f"{tag} I'm in limited mode, but I'm still here.")
