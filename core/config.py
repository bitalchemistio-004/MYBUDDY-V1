"""
core/config.py — Global configuration and constants.
Single source of truth for all tunable parameters.

Note: API key validation and startup prints live in core/main.py, NOT here,
so this file is safe to import in tests and sub-modules without side-effects.
"""
import os
import re
from dotenv import load_dotenv

load_dotenv()


def prefer_ipv4_loopback(environ=os.environ) -> None:
    """
    "localhost" resolves to IPv6 first on Windows, Ollama listens on IPv4
    only, and Python then waits ~2 s per connection before falling back
    (measured 2.2 s vs 0.17 s per model call). Talk to local services by
    address.
    """
    for key in ("OLLAMA_BASE_URL", "OLLAMA_HOST_URL", "LLM_LOCAL_ENDPOINT"):
        if "://localhost" in environ.get(key, ""):
            environ[key] = environ[key].replace("://localhost", "://127.0.0.1")


prefer_ipv4_loopback()


# ── LLM / API ──────────────────────────────────────────────
def _get_env_fuzzy(target_key: str) -> str | None:
    """Robust env-var reader that handles BOM/whitespace-corrupted .env files."""
    val = os.getenv(target_key)
    if val and val.strip():
        return val.strip()
    for k, v in os.environ.items():
        if k.strip() == target_key and v and v.strip():
            return v.strip()
    env_path = ".env"
    if os.path.exists(env_path):
        try:
            raw = open(env_path, "r", encoding="utf-8").read()
            escaped = re.escape(target_key)
            pattern_full = r"(?:^|(?<=[\r\n]))" + escaped + r"=([^\r\n]+)"
            pattern_bare = escaped + r"=([^\r\n]+)"
            match = re.search(pattern_full, raw) or re.search(pattern_bare, raw)
            if match:
                raw_value = match.group(1).strip()
                next_key = re.search(r"[A-Z0-9_]+=", raw_value)
                if next_key:
                    raw_value = raw_value[:next_key.start()].strip()
                if raw_value:
                    return raw_value
        except OSError:
            pass
    return None


GROQ_API_KEY       = _get_env_fuzzy("GROQ_API_KEY")
# Real conversational models, not the compound family. groq/compound and
# groq/compound-mini are tool-orchestration models Groq itself documents as
# "designed for batch and latency-tolerant workloads rather than real-time
# applications" -- and both are capped at 250 requests/day, shared across
# every live reply plus six background utility tasks (mood, inner-life
# thoughts, timeline, facts, emotional tagging, identity compression). That
# combination is why the budget kept running out mid-conversation, and why
# replies read like a search-result synthesizer rather than a companion:
# compound is built to decide when to call tools and summarize results, not
# to hold a warm personality.
GROQ_CHAT_MODEL    = os.getenv("GROQ_CHAT_MODEL",    "llama-3.3-70b-versatile")
GROQ_UTILITY_MODEL = os.getenv("GROQ_UTILITY_MODEL", "llama-3.1-8b-instant")

BRAIN_PROVIDER     = os.getenv("BRAIN_PROVIDER", "ollama") # local by default; cloud e.g. 'groq' or 'ollama,groq'

# ── Device auth (Phase 1B) ───────────────────────────────────
# Shared secret the ESP32 sends in its HELLO handshake before /ws will let it
# become ONLINE (api/websocket_server.py). Same convention as the firmware's
# MBUDDY_OTA_PASSWORD: one value kept identical on both sides, not a per-device
# credential system. The default below is a real value (so a fresh checkout
# works locally without extra setup), not an empty string — an empty secret
# would make every HELLO "match" by comparing "" == "", silently defeating
# the whole point. Rotate this (and the firmware's matching define) together
# before ever exposing /ws outside a trusted LAN — see
# docs/reports/SECURITY_REPORT.md SEC-001/SEC-007.
#
# MBUDDY_DEVICE_TOKEN_DEFAULT is named separately (not just inlined below) so
# api/auth.py can compare "is this still the shipped default?" without
# hardcoding the literal a second time. api/auth.py:validate_device_token()
# refuses to authenticate ANY device against this exact value while a tunnel
# is active (api/tunnel_manager.py sets MBUDDY_TUNNEL_ACTIVE when a tunnel
# actually starts) — the default is fine on a trusted LAN, but must never be
# what a remotely-reachable deployment is still running on.
MBUDDY_DEVICE_TOKEN_DEFAULT = "mbuddy-device-token"
MBUDDY_DEVICE_TOKEN = os.getenv("MBUDDY_DEVICE_TOKEN", MBUDDY_DEVICE_TOKEN_DEFAULT)
# How long /ws waits for the first message (must be HELLO) before rejecting.
HELLO_TIMEOUT_S = 5.0

# ── Audio ───────────────────────────────────────────────────
SAMPLE_RATE            = 16000
CHUNK_SAMPLES          = 256        # 16ms at 16kHz
# INMP441 @ 12x firmware gain → typical speech RMS ~0.010–0.030 float.
# Old values (0.042/0.038) pre-screened chunks BEFORE WebRTC VAD ran,
# causing zero VAD triggers. New values let real speech through all gates.
SILENCE_THRESHOLD      = 0.018      # RMS to count as non-silence for fallback gate
NOISE_FLOOR            = 0.012      # Pre-VAD energy gate: skip VAD only if RMS < 0.012*0.8=0.0096
SILENCE_DURATION       = 1.0        # end of speech; was 2.2 (inherited), which added 1.2 s to every turn
MAX_UTTERANCE_DURATION = 15.0
MIN_SPEECH_DURATION    = 0.35       # Quick commands; sleeping uses SLEEP_MIN_SPEECH_DURATION
SLEEP_MIN_SPEECH_DURATION = 0.55  # Ignore micro-bursts while asleep (Whisper → "hey")
MIN_FINALIZE_RMS       = 0.015      # Reject low-level floor hiss and micro noise
POST_TTS_STT_COOLDOWN  = 0.20      # Echo guard after full reply only (not per sentence)
# ── Barge-in ──────────────────────────────────────────────────
# Lets the user interrupt MBUDDY mid-reply by speaking over it.
BARGE_IN_ENABLED       = True       # Master switch; False makes replies uninterruptible
# MBUDDY's own speaker onset is loudest just as a reply starts, which is
# when an energy-based detector is most likely to mistake it for the user.
# Hold detection off until that settles, so MBUDDY cannot cut itself off.
BARGE_IN_GRACE_PERIOD_MS = 500      # Ignore mic energy for this long after playback starts
# Server-side fallback only if firmware lacks [BARGE] (peak int16 scale, ~0.22 normalized)
BARGE_IN_PEAK_INT16    = 7200
BARGE_IN_RMS_RATIO     = 1.40      # User barge RMS must be 40% above learned playback/echo RMS
BARGE_IN_MIN_RMS       = 0.018     # Absolute floor so tiny noise does not interrupt TTS
BARGE_IN_CONSECUTIVE   = 5          # ~80ms @ 256 samples/chunk
STT_MIN_AVG_LOGPROB    = -0.75     # Whisper segment confidence floor (tightened from -0.85)
# Below the floor above but above this one, a transcript is "uncertain": it
# goes to the brain with a note (the model can often recover a misheard word
# from context, e.g. "can you law?" -> "laugh") instead of being dropped.
# Below this it is treated as garbage. Tuned for local faster-whisper small.
STT_GARBAGE_AVG_LOGPROB = -1.2
# Words Whisper should expect in conversation with MBUDDY (faster-whisper
# `hotwords`); short, plain, no instructions, so nothing leaks into output.
STT_HOTWORDS = "Hey buddy, MBUDDY, USER_NAME, timer, remind me, reminder, task list, weather, volume, joke, laugh"
STT_MAX_NO_SPEECH_PROB = 0.45      # Reject when Whisper thinks it's non-speech (tightened from 0.55)
# Stricter no-speech threshold used only for very short transcripts (<=3
# words), where a couple of misheard words are more likely to be noise than
# genuine short speech. Named separately from STT_MAX_NO_SPEECH_PROB rather
# than left as a bare literal so both thresholds get tuned deliberately.
STT_MAX_NO_SPEECH_PROB_SHORT = 0.35
TARGET_AUDIO_RMS       = 0.08
MAX_GAIN               = 12.0       # INCREASED from 8.0 to handle cleaner, quieter firmware signal
PRE_ROLL_CHUNKS        = 22         # INCREASED from 15 to capture sharp "Hey" starts
INNER_THOUGHT_VOLUME   = 0.35       # Audible private thoughts should sit below normal reply volume

# ── VAD ─────────────────────────────────────────────────────
VAD_FRAME_SAMPLES  = 320
VAD_AGGRESSIVENESS = 2              # 2 = balanced; 3 was clipping quiet phrases
VAD_SPEECH_FRAMES  = 5              # Increased from 2 to reject transient noise bursts (~80ms)

# ── DSP ─────────────────────────────────────────────────────
HPF_CUTOFF_HZ       = 80            # High-pass cutoff: removes HVAC/desk rumble
SILENCE_TRIM_RMS    = 0.006         # Avoid trimming real speech but clear floor-hiss
SILENCE_TRIM_WIN_MS = 10            # RMS window width for silence trimming (ms)

# ── Memory / session ────────────────────────────────────────
MEMORY_FILE              = "mbuddy_memory.json"
MAX_MEMORY               = 200
AWAKE_IDLE_TIMEOUT       = 90.0
STT_DEDUP_CACHE_SIZE     = 20
STT_ECHO_DEBOUNCE_S      = 1.0   # Only block identical STT within 1s (echo), not user repeats
# Bias Whisper toward local names only. Instruction-like prompt text can leak
# into low-confidence transcripts, so keep this short and non-command-like.
STT_WHISPER_PROMPT       = (
    "USER_NAME, MBUDDY"
)
LLM_TIMEOUT_SECONDS      = 30.0   # a retry of a wrong-language reply
# A turn gives up only when NOTHING has been said after FIRST_TEXT_TIMEOUT_S, or when it runs past TURN_TIMEOUT_S in all.
# The flat 30 s for the whole turn (tools, a busy cloud queue, the reply) dropped replies whose tools had already run
# (the field test, 2026-10-19: 10 replies lost, one after its reminder was already set).
FIRST_TEXT_TIMEOUT_S     = 120.0
TURN_TIMEOUT_S           = 300.0
POSTPROCESS_MAX_RETRIES  = 3

# ── Wake / goodbye phrases ──────────────────────────────────
WAKE_PHRASES = (
    "hey buddy",
    "hi buddy",
    "\u064a\u0627 \u0628\u0627\u062f\u064a",      # يا بادي
    "\u0647\u0627\u064a \u0628\u0627\u062f\u064a", # هاي بادي
)

SOFT_GOODBYE_PHRASES = (
    "bye", "goodbye", "see you", "see you later", "see ya", "later",
    "talk later", "catch you later",
    "i'm going", "i have to go", "gotta go",
    "bye bye", "مع السلامة",
)
FINAL_GOODBYE_PHRASES = (
    "bye buddy", "go to sleep", "good night", "sleep now",
    "i have to go", "gotta go", "i really have to go",
    "i'm leaving", "مع السلامة",
    "تصبح على خير", "تصبحي على خير",
)
GOODBYE_CONFIRM_PHRASES = (
    "yes", "yeah", "yep", "sure", "okay", "ok",
    "bye", "goodbye", "good night", "sleep now",
    "aywa", "eh", "أكيد", "ايوه", "نعم",
)

# ── Eye emotion set (for ESP32 display protocol) ────────────
EYE_EMOTIONS = {
    "Normal", "Angry", "Glee", "Happy", "Sad", "Worried",
    "Focused", "Annoyed", "Surprised", "Skeptic", "Frustrated",
    "Unimpressed", "Sleepy", "Suspicious", "Squint", "Furious",
    "Scared", "Awe",
}

# ── STT hallucination / noise filters ───────────────────────
# Phrases to block ONLY if they are the entire transcript (or nearly entire)
STT_HALLUCINATIONS = {
    "thank you", "thanks for watching",
    "thank you for watching", "see you next time",
    "bye for now", "you for watching",
    "thanks for for watching",
    "thank you.", "thanks for watching.",
    "please subscribe", "subscribe to the channel",
    "watching!", "thanks for watching!",
}

# Patterns to block if they appear ANYWHERE in the transcript
STT_SUBSTRING_HALLUCINATIONS = {
    "amara org", "amaraorg", "subtitles by", "community subtitles",
    "amara.org", "amara dot org",
}

HALLUCINATION_SHORT_PHRASES = {
    "okay", "ok", "yeah", "yes", "no", "hm", "hmm", "uh", "um",
    "right", "sure", "so", "well", "now", "hey", "hi", "hello",
    "the", "a", "and", "in", "on", "at", "to", "is", "it", "i",
    "you", "me", "we", "he", "she", "they", "that", "this",
    "يلا", "اه", "اوك", "نعم", "لا", "هاي", "هلا", "wait",
    "eh", "goose", "wuddy", "wudy", "woody", "good", "great",
}

# Single-word junk often heard from speaker bleed / noise (not wake phrases)
STT_ECHO_GARBAGE_WORDS = frozenset({
    "eh", "ah", "oh", "um", "hmm", "hm",
    "goose", "wuddy", "wudy", "woody", "body", "bloody",
    "good", "great", "nice", "cool", "yeah", "yes", "no",
    "the", "and", "but", "so", "well", "okay", "ok",
})

# ── Feature Toggles ─────────────────────────────────────────
MBUDDY_ENABLE_WEB_SEARCH = os.getenv("MBUDDY_ENABLE_WEB_SEARCH", "1").lower() in {"1", "true", "yes"}
