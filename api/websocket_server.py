"""
api/websocket_server.py — aiohttp WebSocket server + MBUDDYSession.

MBUDDYSession encapsulates the per-connection VAD pipeline, conversation
state, deduplication logic, TTS playback, and goodbye flow.

Handlers:
  health_handler    — GET /  → plain-text health probe
  websocket_handler — GET /ws → main ESP32 connection
"""
import asyncio
from collections import deque
from datetime import datetime, timezone
import hashlib
import re
import time
import traceback

import aiohttp
import numpy as np
from aiohttp import web

from api.device_control import ACTION_THOUGHTS, clear_active_session, set_active_session
from api.device_protocol import DeviceProtocol, hello_ack, hello_reject, parse_hello
from api.auth import validate_device_token
from audio.session_state import AudioSessionState
from brain.cognitive_state import build_cognitive_state, decide_behavior
from brain.emotion_authority import validate_emotion_decision
from brain.fallbacks import fallback_reply
from brain.world_model import build_world_model
from brain.llm_router import ask_mbuddy_stream, llm_chat_create
from brain.personality import (
    FINAL_GOODBYE_TABLE, SOFT_GOODBYE_TABLE, WAKE_GREETINGS,
    extract_emotion_state, strip_emotion_tags, strip_for_tts,
)
from core.config import (
    AWAKE_IDLE_TIMEOUT,
    GROQ_UTILITY_MODEL, LLM_TIMEOUT_SECONDS, FIRST_TEXT_TIMEOUT_S, TURN_TIMEOUT_S, MAX_UTTERANCE_DURATION, MIN_SPEECH_DURATION,
    SLEEP_MIN_SPEECH_DURATION, POST_TTS_STT_COOLDOWN,
    POSTPROCESS_MAX_RETRIES,
    HELLO_TIMEOUT_S,
    SAMPLE_RATE,
    STT_DEDUP_CACHE_SIZE, STT_ECHO_DEBOUNCE_S,
    BARGE_IN_MIN_RMS, BARGE_IN_RMS_RATIO,
    BARGE_IN_ENABLED, BARGE_IN_GRACE_PERIOD_MS,
)
from core.status import runtime_status
from core.config import SILENCE_DURATION
from core.state_manager import inner_life_loop
from memory.long_term import (
    extract_and_save_facts, load_memory, save_memory, tag_emotional_memory,
    compress_identity_snapshot,
)
from memory.companion_memory import (
    build_daily_stats,
    maybe_capture_reminder,
    maybe_update_project_thread,
    maybe_write_daily_summary,
)
from memory.timeline import extract_timeline_event
from audio.preprocessing import preprocess_audio
from stt.transcriber import transcribe_audio
from utils.diagnostics import audio_rms
from core.audio_settings import get_audio_settings
from stt.language_detect import (
    is_arabic,
    is_probable_hallucination, transcript_confidence, settle_confidence,
    is_final_goodbye, is_goodbye_confirmation, is_soft_goodbye,
    is_wake_only, normalize_text, should_wake,
)
from stt.language_policy import (
    approval_ack,
    can_adopt_language,
    decline_ack,
    infer_language,
    is_lang_approval,
    is_lang_decline,
    offer_message,
    resolve_conversation_lang,
    reply_matches_language,
    asks_for_other_language,
    has_non_english_latin_marker,
    should_offer_language,
    unsupported_language_ack,
    scrub_arabizi_output,
)
from tts.speaker import stream_tts_pcm, synthesize_tts_pcm
from tts.speaker import FIRST_LEADER_MS, JOIN_LEADER_MS, TRAILER_MS, pause_after
from tts.expressions import expression_emotion
from tts.sfx import synthesize_sfx_pcm
import utils.logger as log



def _done_this_turn(since: float) -> str:
    """The results of the tools that finished since [since], in their own words ("" if none ran)."""
    try:
        from brain import outcomes
        done = [r for r in outcomes.recent(30) if "asked" in r and r.get("t", 0) >= since - 0.5 and r.get("verdict") != "fail"]
        return " ".join(str(r.get("result", "")).strip().rstrip(".") + "." for r in done[-3:] if r.get("result"))[:400]
    except Exception:  # noqa: BLE001
        return ""

class MBUDDYSession:
    """One per WebSocket connection. Owns the full audio-to-response pipeline."""

    def __init__(self, ws: web.WebSocketResponse, memory: dict):
        self.ws     = ws
        self.memory = memory
        self.history: list = []
        self.reply_count = 0  # finished replies; the chat endpoint checks a new one arrived
        self.protocol = DeviceProtocol()
        self.audio = AudioSessionState()

        # Shared with the Runtime, not per-session. A session-local state
        # meant the headless session and the device session each had their own
        # mood and energy, both internally consistent and disagreeing with each
        # other. How MBUDDY feels belongs to MBUDDY, not to a connection.
        from core.runtime import get_runtime

        runtime = get_runtime()
        runtime.set_memory(memory)
        self.state = runtime.state

        # Hydrate LLM context with past exchange pairs (filtering out non-English entries)
        # Reduced from 24 to 6 to prevent payload bloat - full memory is in system prompt anyway
        for entry in self.memory.get("conversation_log", [])[-3:]:
            u_text = entry.get("user", "")
            m_text = strip_emotion_tags(entry.get("mbuddy", ""))
            # Skip past entries with Arabic script to prevent language leakage
            if is_arabic(u_text) or is_arabic(m_text):
                continue
            # with its time, so an old conversation is not taken as this one (F46)
            try:
                at = datetime.strptime(str(entry.get("time", "")), "%Y-%m-%d %H:%M").timestamp()
            except ValueError:
                at = 0.0
            self.history.append({"role": "user",      "content": u_text, "at": at})
            self.history.append({"role": "assistant", "content": m_text, "at": at})

        # ── VAD state ──────────────────────────────────────────
        # ── Wake / sleep ───────────────────────────────────────
        self.is_awake:             bool  = False
        self.awake_flag:           list  = [False]
        self.last_activity       = asyncio.get_event_loop().time()
        self.tts_task            = None  # Track active TTS voice stream task (single sentence)
        self.response_task       = None  # Track the entire response generation/streaming task
        self.playback_done_event = asyncio.Event()
        # When the displayed expression was last set or refreshed. The
        # decay loop measures the hold from here, and resets it while MBUDDY
        # is mid-interaction.
        self.last_expression_at: float = 0.0
        # When the current expression first went up. Never reset by activity,
        # so a face that has been stuck through a long stall is still visible
        # as stuck.
        self.expression_set_at: float = 0.0
        # The emotion the decay loop last observed, so it can notice a change
        # made anywhere else without every call site stamping a timestamp.
        self._last_seen_emotion: str = ""
        self.playback_started:    bool  = False
        # Last spoken reply, so the text-chat endpoint can return what
        # MBUDDY said rather than scraping it out of the status object.
        self.last_reply_text:     str   = ""
        # Set for a single turn when a caller wants the reply routed to
        # memory and the display but not voiced.
        self._suppress_speech:    bool  = False
        self._silent_texts:       set   = set()   # typed turns asked to stay silent, kept until each one has run (a queued one too)
        # Monotonic time playback last began, used for the barge-in grace
        # period. 0.0 means nothing is playing.
        self.playback_started_at: float = 0.0
        self.barge_cancelled:     bool  = False

        # ── Goodbye flow ───────────────────────────────────────
        self.goodbye_pending:      bool  = False

        # ── Locks and flight-guard ─────────────────────────────
        self.response_lock:        asyncio.Lock = asyncio.Lock()
        self.speech_lock:          asyncio.Lock = asyncio.Lock()
        self.llm_in_flight:        bool  = False
        self.is_outputting_audio:  bool  = False
        self.reply_speaking:       bool  = False  # True for full multi-sentence reply
        self.barge_armed:          bool  = False  # ESP32 [BARGE] or loud peak — accept mic during TTS

        # ── Input deduplication ────────────────────────────────
        self.stt_cache:            list[tuple[str, float]] = []
        self.finalize_lock:        asyncio.Lock = asyncio.Lock()
        # Turns waiting while a reply runs: (text, language, typed). Typed ones
        # first, and a later voice fragment never replaces or cancels them
        # (live: room audio knocked out a typed request, S116 P3.1).
        self.turn_queue:           list[tuple[str, str, bool]] = []
        self._current_typed:       bool  = False

        # ── LLM-flight audio buffer (Bug 5 fix) ───────────────
        # Audio arriving while llm_in_flight is True is buffered here
        # (max ~3 s worth) rather than silently dropped. After the
        # response pipeline finishes we drain it so the user's next
        # utterance is never lost.
        self._inflight_audio_buf:  deque[bytes] = deque(maxlen=190)  # ~3 s @ 16 ms chunks

        # ── Background tasks ───────────────────────────────────
        self.inner_task:           asyncio.Task | None = None
        self.postprocess_task:     asyncio.Task | None = None
        self._postprocess_queue:   deque[tuple[str, str, bool]] = deque()
        self._tracked_tasks:       set[asyncio.Task] = set()
        self.closing:              bool = False
        self.current_inner_thought: str = ""
        self.auto_visuals_enabled: bool = False

        # ── Mood context cache ─────────────────────────────────
        # Init to current log length so the cache is valid from the first call,
        # not perpetually stale (old init=-1 caused a Groq call every turn).
        self._mood_cache_at:       int   = len(self.memory.get("conversation_log", []))
        self._mood_last_called_at: float = 0.0
        self.last_audio_output_at: float = 0.0

        # ── Multilingual policy (English Only) ─────────────────
        self.approved_languages: set[str] = {"en"}
        self.active_language: str = "en"
        self.pending_lang_offer: str | None = None
        initial_world = build_world_model(
            self.state,
            session=self,
            runtime=runtime_status,
            memory=self.memory,
            event="connected",
        )
        runtime_status.update(
            mode="normal",
            mode_reason="connected",
            awake=self.is_awake,
            emotion=self.state.current,
            energy=self.state.energy,
            needs=self.state.needs,
            auto_visuals=self.auto_visuals_enabled,
            content_awareness_enabled=self.auto_visuals_enabled,
            world_model=initial_world.to_dict(),
            capability_registry=initial_world.capability_registry,
            disconnected_features=initial_world.disconnected_features,
            memory_count=len(self.memory.get("conversation_log", [])),
            inner_count=len(self.memory.get("inner_log", [])),
            daily_stats=build_daily_stats(self.memory),
            active_project_thread=(self.memory.get("project_threads") or [{}])[-1].get("title", ""),
            pending_reminders_count=len([r for r in self.memory.get("reminders", []) if r.get("status") == "open"]),
        )

    async def _persist_languages(self) -> None:
        self.memory["approved_languages"] = sorted(self.approved_languages)
        self.memory["active_language"]    = self.active_language
        await save_memory(self.memory)

    def _voice_volume(self) -> float:
        """
        Speaking level for this reply.

        Energy shapes the expression — a tired MBUDDY speaks more quietly —
        and the user's master volume then scales the whole curve, so the
        expression survives while the overall level stays under their control.
        """
        energy = self.state.energy
        if energy < 25:
            base = 0.72
        elif energy < 50:
            base = 0.86
        elif energy > 82:
            base = 1.06
        else:
            base = 1.0
        return get_audio_settings().apply(base)

    def _inner_voice_volume(self) -> float:
        """
        Level for a spoken private thought.

        Uses the dedicated inner-thought volume from settings rather than the
        speech level, so musings can sit quietly under conversation — or be
        silenced entirely — without affecting how loudly MBUDDY replies.
        """
        audio = get_audio_settings()
        base = audio.apply_inner()
        energy = self.state.energy
        if energy < 25:
            return base * 0.65
        if energy > 82:
            return min(base * 1.15, audio.master_volume)
        return base

    def _track_task(self, coro, label: str) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tracked_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            self._tracked_tasks.discard(done_task)
            if done_task.cancelled():
                return
            try:
                exc = done_task.exception()
            except Exception:
                return
            if exc:
                log.warn(f"{label} crashed: {exc}")

        task.add_done_callback(_done)
        return task

    async def _safe_send_str(self, payload: str) -> bool:
        if self.closing or self.ws.closed:
            return False
        try:
            await self.ws.send_str(payload)
            return True
        except Exception as exc:
            self.closing = True
            runtime_status.update(last_error=f"ws_send_failed:{type(exc).__name__}")
            return False

    async def close(self) -> None:
        self.closing = True
        self.playback_done_event.set()
        self.awake_flag[0] = False
        self.is_awake = False
        self.llm_in_flight = False
        self.reply_speaking = False
        self.is_outputting_audio = False
        runtime_status.update(thinking=False, speaking=False)

        current = asyncio.current_task()
        tasks = {
            self.inner_task,
            self.postprocess_task,
            self.response_task,
            self.tts_task,
            *self._tracked_tasks,
        }
        to_cancel = [task for task in tasks if task and task is not current and not task.done()]
        for task in to_cancel:
            task.cancel()
        if to_cancel:
            await asyncio.gather(*to_cancel, return_exceptions=True)
        self._tracked_tasks.clear()

    def _schedule_vector_memory(self, text: str, spoken: str, entry: dict) -> None:
        self._track_task(self._add_vector_memory(text, spoken, entry), "vector_memory")

    async def _add_vector_memory(self, text: str, spoken: str, entry: dict) -> None:
        """Add semantic memory without blocking the voice response pipeline."""
        try:
            from memory.vector_store import get_vector_store

            await asyncio.wait_for(
                asyncio.to_thread(
                    get_vector_store().add,
                    # Only the user's words: an invented detail in a reply
                    # must never become a searchable memory (audit 2.11).
                    f"User: {text}",
                    {"time": entry["time"], "lang": entry["lang"]},
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            log.warn("Vector memory add timed out - continuing without blocking conversation.")
        except Exception:
            pass

    def _update_relationship_state(self, text: str, _spoken: str) -> None:
        rel = self.memory.setdefault("relationship_state", {})
        rel["rapport"] = min(100, int(rel.get("rapport", 50)) + 1)
        rel["trust"] = min(100, int(rel.get("trust", 50)) + 1)
        patterns = rel.setdefault("history_of_mood_patterns", {})
        stressors = patterns.setdefault("tends_to_be_stressed_around", [])
        patterns.setdefault("responds_well_to", "direct answers first, then light jokes")
        patterns.setdefault("gets_annoyed_by", "over-explanation")

        low = text.lower()
        def note_stressor(label: str) -> None:
            if label not in stressors:
                stressors.append(label)
            patterns["tends_to_be_stressed_around"] = stressors[-6:]

        if any(w in low for w in ("bug", "fix", "code", "system", "issue", "error", "debug")):
            rel["current_vibe"] = "focused"
            rel["preferred_help_style"] = "direct"
            if any(w in low for w in ("stress", "annoying", "random", "lag", "sleep", "broken", "stuck", "crash")):
                note_stressor("software bugs")
        elif any(w in low for w in ("tired", "sleep", "exhausted", "lag", "slow")):
            rel["current_vibe"] = "tired"
            rel["preferred_help_style"] = "calm"
            note_stressor("fatigue")
        elif any(w in low for w in ("feel", "friend", "companion", "human", "lonely")):
            rel["current_vibe"] = "personal"
            rel["preferred_help_style"] = "present"
        elif "?" in text:
            rel["current_vibe"] = "curious"

        if any(w in low for w in ("hardware", "esp32", "mic", "speaker", "oled", "wifi", "i2s")):
            note_stressor("hardware bugs")
        if any(w in low for w in ("deadline", "late", "urgent", "tonight", "tomorrow")):
            note_stressor("deadlines")
        if any(w in low for w in ("don't over", "too much", "over-explain", "over explain", "spam", "generic")):
            patterns["gets_annoyed_by"] = "over-explanation, generic warmth, and repeated memory callbacks"
            rel["preferred_help_style"] = "direct"
        if any(w in low for w in ("joke", "banter", "friend", "companion")):
            patterns["responds_well_to"] = "direct answers first, then specific banter"
        if any(w in low for w in ("deep dive", "explain", "walk me through")):
            patterns["responds_well_to"] = "structured depth when he explicitly asks for it"

        if len(text.split()) >= 4:
            rel["last_meaningful_topic"] = text[:140]

    async def _handle_language_gate(self, text: str, whisper_raw: str) -> bool:
        """Language offer / yes-no. Returns True if this turn is fully handled."""
        if not self.is_awake:
            return False

        low = text.lower()
        if re.search(r"\b(speak|talk|reply|answer|switch|change)\s+(in\s+)?arabic\b", low) or "تكلم عربي" in text or "احكي عربي" in text:
            self.approved_languages.add("ar")
            self.approved_languages.add("mixed")
            self.active_language = "ar"
            self.pending_lang_offer = None
            await self._persist_languages()
            await self._send_checked("[HAPPY] تمام، من هلّق بحكي معك بالعربي.", "Happy", lang="ar")
            return True
        if re.search(r"\b(speak|talk|reply|answer|switch|change)\s+(in\s+)?english\b", low):
            self.active_language = "en"
            self.pending_lang_offer = None
            await self._persist_languages()
            await self._send_checked("[HAPPY] Done, I’ll answer in English.", "Happy", lang="en")
            return True

        detected = infer_language(text, whisper_raw)

        if detected == "arabizi":
            await self._send_checked(unsupported_language_ack(detected), "Normal", lang="en")
            return True

        if self.pending_lang_offer:
            offer = self.pending_lang_offer
            if is_lang_approval(text):
                if not can_adopt_language(offer):
                    self.pending_lang_offer = None
                    await self._send_checked(unsupported_language_ack(offer), "Normal", lang="en")
                    return True
                self.approved_languages.add(offer)
                if offer == "ar":
                    self.approved_languages.add("mixed")
                self.active_language = offer
                self.pending_lang_offer = None
                await self._persist_languages()
                await self._send_checked(approval_ack(offer), "Happy", lang="en")
                return True
            if is_lang_decline(text):
                self.pending_lang_offer = None
                await self._send_checked(decline_ack(), "Normal", lang="en")
                return True
            await self._send_checked(offer_message(offer), "Normal", lang="en")
            return True

        if should_offer_language(detected, self.approved_languages):
            self.pending_lang_offer = detected
            await self._send_checked(offer_message(detected), "Normal", lang="en")
            return True

        return False

    # ──────────────────────────────────────────────────────────
    # Mood context
    # ──────────────────────────────────────────────────────────
    async def get_mood_context(self) -> str:
        """Brief summary of the relationship vibe based on recent logs.

        Cache policy: only call Groq when BOTH conditions hold:
          1. At least 3 new conversation-log entries since last call.
          2. At least 30 seconds have elapsed since last call.
        This prevents the previous bug where Groq was called on every single turn.
        """
        recent  = self.memory["conversation_log"][-3:]
        log_len = len(self.memory["conversation_log"])
        now     = asyncio.get_event_loop().time()

        new_entries   = log_len - self._mood_cache_at
        time_elapsed  = now - self._mood_last_called_at

        if not recent or new_entries < 3 or time_elapsed < 30.0:
            return self.state.conversation_mood

        self._mood_cache_at       = log_len
        self._mood_last_called_at = now
        try:
            exchanges = "\n".join(
                f"USER_NAME: {e['user']} | MBUDDY: {e['mbuddy']}" for e in recent
            )
            res = await asyncio.wait_for(
                llm_chat_create(
                    priority="background",
                    model=GROQ_UTILITY_MODEL,
                    messages=[{"role": "user", "content": (
                        f"Based on these exchanges, give MBUDDY's emotional vibe toward USER_NAME "
                        f"in ONE first-person sentence.\n{exchanges}"
                    )}],
                    max_tokens=60,
                    temperature=0.45,
                ),
                timeout=5.0
            )
            if hasattr(res, "choices"):
                content = res.choices[0].message.content
                if content:
                    self.state.conversation_mood = content.strip()
        except (asyncio.TimeoutError, Exception):
            pass
        return self.state.conversation_mood

    # ──────────────────────────────────────────────────────────
    # VAD helpers
    # ──────────────────────────────────────────────────────────
    def feed_chunk(self, chunk: np.ndarray) -> bool:
        """
        Primary: WebRTC VAD.  Secondary guard: RMS vs. noise floor.
        Returns True when an end-of-utterance has been detected.
        """
        should_finalize = self.audio.feed_chunk(chunk)
        if self.is_awake and self.audio.is_speaking:
            self.last_activity = asyncio.get_event_loop().time()
        runtime_status.update(
            last_rms=self.audio.last_rms,
            listening=self.audio.is_speaking,
            awake=self.is_awake,
            emotion=self.state.current,
            energy=self.state.energy,
            needs=self.state.needs,
        )
        return should_finalize

    def drain_buffer(self) -> np.ndarray:
        return self.audio.drain_buffer()

    async def _sync_energy(self) -> None:
        """Send the current energy level as a protocol tag to the ESP32 OLED."""
        await self._safe_send_str(self.protocol.energy_update(self.state.energy))
        runtime_status.update(energy=self.state.energy, emotion=self.state.current, needs=self.state.needs)

    async def handle_audio(self, data: bytes) -> None:
        """Process a chunk of binary audio from the ESP32."""
        if len(data) % 2 != 0:
            data = data[:-1]
        if not data:
            return
        # Microphone switched off in the app: the bridge stops capturing, and
        # anything still arriving is not heard (S55).
        from core import ui_settings
        if not ui_settings.get("microphone_on"):
            return

        chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        chunk = np.clip(chunk, -1.0, 1.0)

        should_finalize = self.feed_chunk(chunk)

        async def safe_finalize() -> None:
            try:
                await self.finalize_utterance()
            except Exception:
                log.warn(f"finalize_utterance crashed: {traceback.format_exc()}")

        if should_finalize:
            self._track_task(safe_finalize(), "finalize_utterance")

        max_samples = int(MAX_UTTERANCE_DURATION * SAMPLE_RATE)
        if len(self.audio.audio_buffer) >= max_samples and not self.finalize_lock.locked():
            log.info("Max buffer reached — forcing finalize…")
            self._track_task(safe_finalize(), "finalize_utterance")

    # ──────────────────────────────────────────────────────────
    # Deduplication
    # ──────────────────────────────────────────────────────────
    def is_duplicate_stt(self, text: str, now: float) -> bool:
        """Block only rapid identical echo (same transcript <1s), not intentional repeats."""
        norm = normalize_text(text)
        fp   = hashlib.md5(norm.encode()).hexdigest()
        self.stt_cache = [(f, t) for f, t in self.stt_cache if now - t <= STT_ECHO_DEBOUNCE_S * 2][-STT_DEDUP_CACHE_SIZE:]
        return any(f == fp and (now - t) < STT_ECHO_DEBOUNCE_S for f, t in self.stt_cache)

    def register_stt(self, text: str, now: float) -> None:
        fp = hashlib.md5(normalize_text(text).encode()).hexdigest()
        if not any(f == fp for f, _ in self.stt_cache):
            self.stt_cache.append((fp, now))
        self.stt_cache = self.stt_cache[-STT_DEDUP_CACHE_SIZE:]

    # ──────────────────────────────────────────────────────────
    # Core pipeline
    # ──────────────────────────────────────────────────────────
    async def capture_next_utterance(self, seconds: float = 12.0) -> str:
        """The next thing said, as text, instead of a command (the notch's "speak it"). It is read by the same
        speech recognition as everything else and goes nowhere else."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._capture = future
        try:
            return await asyncio.wait_for(future, timeout=seconds)
        except asyncio.TimeoutError:
            return ""
        finally:
            self._capture = None

    async def finalize_utterance(self) -> None:
        if self.finalize_lock.locked():
            log.info("Finalize already running — skipping concurrent call")
            return

        async with self.finalize_lock:
            now      = asyncio.get_event_loop().time()
            audio_np = self.drain_buffer()

            duration = len(audio_np) / SAMPLE_RATE
            min_dur = SLEEP_MIN_SPEECH_DURATION if not self.is_awake else MIN_SPEECH_DURATION
            if duration < min_dur:
                log.info(f"Finalize skipped: utterance too short ({duration:.2f}s < {min_dur:.2f}s)")
                return

            audio_np = np.array(audio_np, dtype=np.float32)
            prepared = preprocess_audio(audio_np)
            rms      = audio_rms(prepared)
            # User-calibratable, not the fixed MIN_FINALIZE_RMS constant —
            # the right gate depends on the room and the mic, tuned live
            # from the Hardware tab's real device level meter, not guessed
            # once in code.
            min_rms = get_audio_settings().min_input_rms
            if rms < min_rms:
                log.info(f"Finalize skipped: RMS too low ({rms:.5f} < {min_rms:.5f}) — {duration:.2f}s buffered")
                runtime_status.update(last_stt_ok=False, last_error="finalize_rms_too_low")
                return

            text, whisper_raw, avg_lp, no_speech = await transcribe_audio(
                prepared,
                already_preprocessed=True,
                # English first (Arabic only once the user has switched to it):
                # no language detection and no second pass, ~45% less GPU per
                # clip with the same accuracy on the test set (audit 3.13).
                language_hint=None if "ar" in self.approved_languages else "en",
                context=getattr(self, "last_reply_text", None),
                asleep=not self.is_awake,
            )
            stt_done = asyncio.get_event_loop().time()
            if not text:
                log.info(f"Finalize skipped: STT returned empty ({duration:.2f}s, rms={rms:.5f})")
                runtime_status.update(last_stt_ok=False, last_error="stt_empty")
                return
            capture = getattr(self, "_capture", None)
            if capture is not None and not capture.done():
                capture.set_result(text.strip())      # "speak it": this is the answer, not a command and not the wake word
                return
            norm = normalize_text(text)
            wake_phrase = should_wake(text)
            if not self.is_awake and not wake_phrase:
                if len(norm) >= 4:
                    log.info(f"Sleeping - ignored non-wake STT: '{text}'")
                runtime_status.update(last_stt_ok=False, last_error="sleep_ignored_non_wake")
                return
            if len(norm) < 2:
                return
            if is_probable_hallucination(text, approved_langs=self.approved_languages):
                log.info(f"Hallucination blocked: '{text}'")
                self.last_activity = asyncio.get_event_loop().time()
                runtime_status.update(last_stt_ok=False, last_error="stt_hallucination")
                return
            inferred_lang = infer_language(text, whisper_raw or "en")
            if (
                inferred_lang not in ("en", "ar", "mixed", "arabizi")
                and avg_lp is not None
                and avg_lp < -0.75
                and not has_non_english_latin_marker(text)
            ):
                log.info(f"Low-confidence unsupported-language STT dropped: '{text}' [{whisper_raw}]")
                self.last_activity = asyncio.get_event_loop().time()
                runtime_status.update(last_stt_ok=False, last_error="stt_unsupported_low_confidence")
                # Silent, like the garbage branch below: with local Whisper
                # these are almost always noise or hallucinations, not the
                # user speaking another language.
                return
            confidence = transcript_confidence(
                text,
                duration_s=duration,
                rms=rms,
                avg_logprob=avg_lp,
                no_speech_prob=no_speech,
                approved_langs=self.approved_languages,
            )
            confidence = settle_confidence(
                confidence,
                detected_lang=whisper_raw or "en",
                conversation_lang=self.active_language,
            )
            # Uncertain speech goes to the brain with a note; the model can
            # often recover a misheard word from context ("can you law?" ->
            # "laugh"). Only garbage is dropped.
            self._heard_unclearly = confidence == "uncertain"
            if self._heard_unclearly:
                log.info(f"Unclear STT passed to brain: '{text}' (lp={avg_lp})")
            if confidence == "garbage":
                log.info(f"Low-confidence STT dropped: '{text}'")
                self.last_activity = asyncio.get_event_loop().time()
                runtime_status.update(last_stt_ok=False, last_error="stt_low_confidence")
                # Stay silent. Unclear speech already goes to the brain; what
                # lands here is noise, an echo of MBUDDY's own voice or a
                # hallucination, and answering it with "that came in messy.
                # Repeat it clean?" made MBUDDY talk to nobody.
                return
            if self.is_duplicate_stt(text, now):
                log.info(f"Duplicate STT ignored: '{text}'")
                return

            runtime_status.update(last_stt_ok=True, last_heard=text, last_error=None)
            self.register_stt(text, now)
            # Timed only for a transcript that becomes a turn: set on every clip,
            # a typed turn measured from the last noise clip (L1).
            self._turn_t = {"end": now, "stt": stt_done - now}
            await self._route_intent(text, whisper_raw or "en")

    def _queue_turn(self, text: str, lang: str, typed: bool) -> None:
        if typed:
            at = next((i for i, t in enumerate(self.turn_queue) if not t[2]), len(self.turn_queue))
            self.turn_queue.insert(at, (text, lang, True))
        else:  # a newer voice utterance replaces an older queued one, as before
            self.turn_queue = [t for t in self.turn_queue if t[2]] + [(text, lang, False)]
        log.info(f"LLM busy — queued ({'typed' if typed else 'voice'}): '{text[:40]}'")

    def is_queued(self, text: str) -> bool:
        return any(t[0] == text for t in self.turn_queue)

    def _clear_voice_turns(self) -> None:
        self.turn_queue = [t for t in self.turn_queue if t[2]]

    async def _route_intent(self, text: str, detected_lang: str, typed: bool = False) -> None:
        if not self.is_awake:
            if should_wake(text):
                log.info(f"🔔 Wake phrase: '{text}'")
                self._set_awake(True)
                self._woken_by_voice = True
                self.goodbye_pending = False
                self._cancel_inner()
                self.state.boost(35)
                await self._sync_energy()
                await self._send_wake_greeting()
            elif len(text.strip()) > 6:
                log.info("💤 Sleeping…")
            return

        # Manual language switch commands
        text_lower = text.lower().strip()
        if text_lower in ("switch to arabic", "speak arabic", "reply in arabic", "arabic mode"):
            self.approved_languages.add("ar")
            self.active_language = "ar"
            await self._persist_languages()
            await self._send_checked("[HAPPY] تمام، خلاص عربي.", "Happy", lang="ar")
            log.info("🌐 Manually switched to Arabic mode")
            return

        if text_lower in ("switch to english", "speak english", "english mode", "english only"):
            self.active_language = "en"
            await self._send_checked("[NORMAL] Got it - English mode.", "Normal", lang="en")
            log.info("🌐 Manually switched to English mode")
            return

        if await self._handle_language_gate(text, detected_lang):
            return

        conv_lang = resolve_conversation_lang(
            text, detected_lang, self.approved_languages, self.active_language,
        )
        self.active_language = conv_lang

        # How the user sounded is state, not just context. A companion that hears
        # you laugh and stays flat is the thing that breaks the illusion.
        try:
            from tts.expressions import detect_user_expressions
            cue_to_stimulus = {
                "laughing": ("humour", 1.0),
                "surprised": ("excitement", 1.0),
                "agreeing": ("praise", 0.5),
                "exasperated": ("stress", 1.0),
                "sighing": ("stress", 0.8),
                "hesitating": ("interest", 0.4),
            }
            for cue in detect_user_expressions(text):
                if cue in cue_to_stimulus:
                    kind, strength = cue_to_stimulus[cue]
                    mood = self.state.stimulate(kind, strength)
                    log.info(f"🫀 Heard {cue} -> {kind}, mood now {mood}")
                    break
        except Exception:
            pass

        if is_wake_only(text):
            log.info(f"🔔 Call while awake: '{text}'")
            await self._send_checked(
                "[NORMAL] yeah, I'm here.",
                "Normal",
                lang=conv_lang,
            )
            return

        log.info(f"👂 [{conv_lang}] '{text}'")
        await self._safe_send_str(self.protocol.heard(text))
        _, listening_decision = self._decide_behavior("listening", text)
        await self._apply_behavior_decision(listening_decision, allow_sfx=False)
        runtime_status.update(
            awake=True,
            listening=True,
            thinking=False,
            speaking=False,
            last_heard=text,
            emotion=self.state.current,
            energy=self.state.energy,
        )
        self.last_activity = asyncio.get_event_loop().time()

        if is_final_goodbye(text) or (
            self.goodbye_pending and is_goodbye_confirmation(text)
        ):
            await self._handle_final_goodbye()
            return

        if is_soft_goodbye(text):
            self.goodbye_pending = True
            await self._handle_soft_goodbye()
            return

        self.goodbye_pending = False
        self._start_or_queue(text, conv_lang, typed)

    def _start_or_queue(self, text: str, lang: str, typed: bool) -> None:
        # Manage the whole response as one task so we can cancel all sentences at once
        if self.response_task and not self.response_task.done():
            if typed or self._current_typed:
                # A typed turn is never cancelled by a later one, and a typed
                # turn waits its turn instead of cancelling a running reply.
                self._queue_turn(text, lang, typed)
                return
            self.response_task.cancel()
        self.response_task = self._track_task(self._respond(text, lang, typed), "response")

    async def _send_wake_greeting(self) -> None:
        reply = WAKE_GREETINGS.get(self.state.current, "[NORMAL] hey, what's up?")
        log.info(f"🤖 {reply}")
        await self._send_checked(reply, self.state.current, lang="en")

    async def _respond(self, text: str, detected_lang: str, typed: bool = False) -> None:
        silent = text in getattr(self, "_silent_texts", ())
        if silent:
            self._suppress_speech = True
            self._silent_turn = text          # this turn owns the silence (a queued turn starts before the last one's cleanup)
        try:
            await self._respond_turn(text, detected_lang, typed)
        finally:
            if silent and not self.is_queued(text):
                self._silent_texts.discard(text)
                if getattr(self, "_silent_turn", None) == text:
                    self._silent_turn = None
                    self._suppress_speech = False
            # However this turn ended (a reconnect or a barge-in cancelled it in the middle of a tool, an error before
            # the model was asked), "thinking" must not stay on. Not when a newer turn has taken over.
            task = asyncio.current_task()
            if self.response_task is None or self.response_task is task or self.response_task.done():
                runtime_status.update(thinking=False)

    async def _respond_turn(self, text: str, detected_lang: str, typed: bool = False) -> None:
        if self.llm_in_flight:
            self._queue_turn(text, detected_lang, typed)
            return
        self._current_typed = typed
        turn = getattr(self, "_turn_t", None) or {"end": asyncio.get_event_loop().time(), "stt": 0.0}  # typed turns
        self._turn_t = None
        turn["respond"] = asyncio.get_event_loop().time()
        from core import timeline  # one line per turn with every stage (S6c)
        timeline.begin(turn["end"])
        timeline.mark("routed")
        if await self._wake_before_turn():
            turn["respond"] = asyncio.get_event_loop().time()  # the stage timers start after the warm-up
            timeline.mark("model warmed")

        tts_worker_task: asyncio.Task | None = None
        turn_started_at = time.time()

        async with self.response_lock:
            self.llm_in_flight = True
            self.reply_speaking = True
            self.last_activity = asyncio.get_event_loop().time()
            from core import ui_settings
            speak_this_reply = bool(ui_settings.get("speak_replies"))
            if speak_this_reply:
                self.playback_done_event.clear()
            else:
                self.playback_done_event.set()  # nothing will play: don't wait for it
            self.playback_started = False
            self.barge_cancelled = False
            cognitive_state, behavior_decision = self._decide_behavior("thinking", text)

            # NEW: Content-aware animation selection
            from brain.content_analyzer import analyze_content_for_animation
            content_animation = analyze_content_for_animation(
                user_text=text,
                emotion=self.state.current,
                intent=getattr(behavior_decision, "intent", "")
            )

            # NEW: Emotion intensity calculation
            from brain.emotion_intensity import build_emotion_intensity, count_context_triggers
            triggers = count_context_triggers(text)
            emotion_intensity = build_emotion_intensity(
                emotion=self.state.current,
                context_triggers=triggers,
                energy_level=self.state.energy,
                recent_intensity=50
            )

            runtime_status.update(
                thinking=True,
                speaking=False,
                last_error=None,
                emotion=behavior_decision.emotion,
                cognitive_state=cognitive_state.to_dict(),
                behavior_decision=behavior_decision.to_dict(),
                emotion_intensity=emotion_intensity.to_dict() if emotion_intensity else {}
            )
            if behavior_decision.emotion:
                self.state.apply_emotion(f"[{behavior_decision.emotion.upper()}]")

            # Use content-aware animation if available
            if content_animation and getattr(self, "auto_visuals_enabled", True):
                await self._send_action(
                    content_animation,
                    int(2200 * emotion_intensity.visual_modifier if emotion_intensity else 2200),
                    reason=f"Content-aware: responding to '{text[:30]}'",
                    priority="normal"
                )
            elif behavior_decision.animation:
                await self._safe_send_str(self.protocol.THINKING)

            await self._apply_behavior_decision(behavior_decision)

            try:
                # The cached mood: refreshing it is a model call, and awaited here it
                # held the turn and competed with the reply (U3); it's refreshed
                # after the reply instead.
                mood_ctx = self.state.conversation_mood
                full_reply = ""
                tts_buffer = ""
                first_tts_chunk = True
                tts_queue: asyncio.Queue[tuple[str, str, bool] | None] = asyncio.Queue()
                authoritative_emotion = (
                    getattr(behavior_decision, "emotion", "")
                    or getattr(cognitive_state, "emotion", "")
                    or self.state.current
                )
                last_emotion_signature: tuple[str, str, str, bool] | None = None

                def validate_reply_emotion():
                    nonlocal last_emotion_signature
                    llm_suggestion = extract_emotion_state(full_reply)
                    emotion_decision = validate_emotion_decision(
                        authoritative_emotion,
                        llm_suggestion,
                    )
                    signature = (
                        emotion_decision.cognitive_emotion,
                        emotion_decision.llm_suggested_emotion,
                        emotion_decision.final_emotion,
                        emotion_decision.accepted,
                    )
                    if signature != last_emotion_signature:
                        runtime_status.record_emotion_decision(
                            emotion_decision.to_dict(),
                            goal=getattr(behavior_decision, "goal", "answer_user"),
                            intent="emotion_validation",
                            priority=getattr(behavior_decision, "priority", "normal"),
                            tool_used=getattr(behavior_decision, "tool", ""),
                            behavior_decision=behavior_decision.to_dict() if hasattr(behavior_decision, "to_dict") else {},
                            competing_behaviors=getattr(behavior_decision, "competing_behaviors", []),
                            conflicts=getattr(behavior_decision, "conflicts", []),
                        )
                        last_emotion_signature = signature
                    return emotion_decision

                async def enqueue_tts_chunk(chunk_text: str, is_first: bool) -> None:
                    clean_chunk = scrub_arabizi_output(strip_for_tts(chunk_text).strip())
                    if not any(c.isalnum() for c in clean_chunk):
                        return
                    if not speak_this_reply:
                        # "Speak replies" is off: the reply is shown, not spoken.
                        self.last_reply_text = (clean_chunk if is_first
                                                else f"{self.last_reply_text} {clean_chunk}".strip())
                        runtime_status.update(last_reply=self.last_reply_text, thinking=False)
                        if is_first and "shown" not in turn:
                            timeline.mark("first text")
                            turn["shown"] = asyncio.get_event_loop().time()
                            log.info(f"[Turn] first text shown {turn['shown'] - turn['end']:.2f}s after the turn began "
                                     f"(routing {turn['respond'] - turn['end'] - turn['stt']:.2f}s, "
                                     f"model {turn.get('first_text', turn['shown']) - turn['respond']:.2f}s to first text)")
                        return
                    # No per-chunk language check here on purpose. A short
                    # sentence fragment (a number, a name, a one-word clause)
                    # routinely has a low Arabic-character ratio even inside
                    # an entirely correct Arabic reply, so this used to
                    # silently drop individual chunks mid-reply — the user
                    # heard MBUDDY cut itself off mid-sentence with no
                    # fallback spoken, which read as a glitch rather than
                    # anything language-related. The full assembled reply is
                    # checked once, reliably, at the end of the stream below
                    # (reply_matches_language(full_reply, ...)), which is
                    # long enough for the ratio check to mean something and
                    # already has a real fallback wired to it.
                    reply_state = validate_reply_emotion().final_emotion
                    self.state.apply_emotion(f"[{reply_state.upper()}]")
                    await self._sync_energy()
                    await tts_queue.put((clean_chunk, reply_state, is_first))

                async def tts_worker() -> None:
                    while True:
                        item = await tts_queue.get()
                        try:
                            if item is None:
                                return
                            clean_chunk, chunk_state, is_first = item
                            # Edge-TTS can approximate a hum but cannot really
                            # laugh. When a chunk carries an expression, let the
                            # face carry it instead, so the moment still reads
                            # as a laugh or a sigh on the display.
                            face_state = expression_emotion(clean_chunk)
                            if face_state:
                                chunk_state = face_state
                            # Record the reply here, before any branch that can
                            # skip ahead — a silent turn, a TTS failure, or empty
                            # audio must still leave the text retrievable.
                            self.last_reply_text = (
                                clean_chunk if is_first
                                else f"{self.last_reply_text} {clean_chunk}".strip()
                            )
                            # The whole reply so far, spoken or not: the app and the
                            # buddy show it (it used to be set only when audio played,
                            # and only to the last chunk).
                            runtime_status.update(last_reply=self.last_reply_text)
                            try:
                                # A silent turn still updates the display and memory; only the
                                # audio is skipped.
                                if self._suppress_speech:
                                    await self._safe_send_str(self.protocol.speaking(chunk_state, clean_chunk))
                                    if is_first and "shown" not in turn:
                                        timeline.mark("first text")
                                        turn["shown"] = asyncio.get_event_loop().time()
                                        log.info(f"[Turn] first text shown {turn['shown'] - turn['end']:.2f}s after the turn began "
                                                 f"(routing {turn['respond'] - turn['end'] - turn['stt']:.2f}s, "
                                                 f"model {turn.get('first_text', turn['shown']) - turn['respond']:.2f}s to first text)")
                                    continue
                                if is_first:
                                    timeline.mark("speech synthesis>")
                                pcm_bytes = await synthesize_tts_pcm(
                                    clean_chunk,
                                    emotion=chunk_state,
                                    lang=detected_lang,
                                    fade_in=False,
                                    fade_out=False,
                                    leader_ms=FIRST_LEADER_MS if is_first else JOIN_LEADER_MS,
                                    trailer_ms=pause_after(clean_chunk, chunk_state),
                                    volume=self._voice_volume(),
                                )
                                if is_first:
                                    timeline.mark("speech synthesis<")
                            except Exception as exc:
                                runtime_status.update(
                                    last_tts_ok=False,
                                    last_error=f"tts_error:{type(exc).__name__}",
                                )
                                runtime_status.mark_mode("limited", "tts_error")
                                log.error("TTS synthesis failed for reply chunk", exc)
                                continue
                            if not pcm_bytes:
                                runtime_status.update(last_tts_ok=False, last_error="tts_empty")
                                continue
                            clean_chunk = strip_for_tts(clean_chunk)
                            if not clean_chunk:
                                continue
                            runtime_status.update(
                                thinking=False,
                                speaking=True,
                                emotion=chunk_state,
                                energy=self.state.energy,
                                last_reply=self.last_reply_text,
                            )
                            self.last_activity = asyncio.get_event_loop().time()
                            if not await self._safe_send_str(self.protocol.speaking(chunk_state, clean_chunk)):
                                return
                            self.playback_started = True
                            self.playback_started_at = asyncio.get_event_loop().time()
                            if is_first and "audio" not in turn:
                                timeline.mark("first audio")
                                turn["audio"] = self.playback_started_at
                                t = turn
                                log.info(
                                    f"[Turn] first audio {t['audio'] - t['end']:.2f}s after speech end: "
                                    f"STT {t['stt']:.2f}s, routing {t['respond'] - t['end'] - t['stt']:.2f}s, "
                                    f"first text {t.get('first_text', t['audio']) - t['respond']:.2f}s, "
                                    f"voice {t['audio'] - t.get('first_text', t['audio']):.2f}s "
                                    f"(plus {SILENCE_DURATION:.1f}s waiting for silence)"
                                )
                            _is_out = [False]
                            self.barge_armed = False
                            self.audio.reset_barge()
                            await stream_tts_pcm(
                                self.ws,
                                self.speech_lock,
                                pcm_bytes,
                                _is_out,
                                session=self,
                            )
                        finally:
                            tts_queue.task_done()

                tts_worker_task = self._track_task(tts_worker(), "tts_worker")

                def should_flush_tts(buffer: str) -> bool:
                    if detected_lang == "ar":
                        return False
                    spoken_chunk = strip_emotion_tags(buffer).strip()
                    if len(spoken_chunk) < 32:
                        return False
                    if re.search(r"[.!?;:]\s*$", spoken_chunk):
                        return True
                    if first_tts_chunk and re.search(r",\s*$", spoken_chunk):
                        return True
                    if len(spoken_chunk) >= 55 and re.search(r",\s*$", spoken_chunk):
                        return True
                    return len(spoken_chunk) >= 110

                async def consume_stream():
                    nonlocal tts_buffer, first_tts_chunk, full_reply
                    async for chunk in ask_mbuddy_stream(
                        self.state,
                        text,
                        self.history,
                        self.memory,
                        detected_lang,
                        mood_ctx,
                        cognitive_state=cognitive_state,
                        behavior_decision=behavior_decision,
                        heard_unclearly=getattr(self, "_heard_unclearly", False),
                    ):
                        turn.setdefault("first_text", asyncio.get_event_loop().time())
                        full_reply += chunk
                        tts_buffer += chunk

                        if should_flush_tts(tts_buffer):
                            await enqueue_tts_chunk(tts_buffer, first_tts_chunk)
                            first_tts_chunk = False
                            tts_buffer = ""

                # A turn may take as long as its tools and a busy cloud need, as long as it is getting somewhere:
                # it gives up only when nothing has been said after FIRST_TEXT_TIMEOUT_S, or past TURN_TIMEOUT_S.
                stream_task = asyncio.ensure_future(consume_stream())
                started = asyncio.get_event_loop().time()
                try:
                    while True:
                        done, _pending = await asyncio.wait({stream_task}, timeout=1.0)
                        if done:
                            stream_task.result()
                            break
                        waited = asyncio.get_event_loop().time() - started
                        if (not full_reply.strip() and waited > FIRST_TEXT_TIMEOUT_S) or waited > TURN_TIMEOUT_S:
                            stream_task.cancel()
                            raise asyncio.TimeoutError()
                except asyncio.TimeoutError:
                    if not full_reply.strip():
                        raise
                    log.warn(f"Stream consumption timed out after partial reply for '{text[:20]}…'")

                if full_reply.strip() and not asks_for_other_language(text) and not reply_matches_language(full_reply, detected_lang):
                    log.warn(f"Wrong-language full reply blocked for lang={detected_lang}: {full_reply[:120]}")
                    # A wrong-language full reply is the model's mistake, not
                    # the user's, so ask them to repeat themselves only as a
                    # last resort. When nothing has been spoken yet this turn
                    # (first_tts_chunk still True — always true for Arabic,
                    # since should_flush_tts never flushes mid-stream for it,
                    # and often true for a short English reply too), silently
                    # retry the same request once before giving up. If audio
                    # already started playing, a silent retry would talk over
                    # or double up on what the user just heard, so that case
                    # still falls back to asking them to repeat.
                    retried_ok = False
                    if first_tts_chunk:
                        full_reply = ""
                        tts_buffer = ""
                        try:
                            await asyncio.wait_for(consume_stream(), timeout=LLM_TIMEOUT_SECONDS)
                        except asyncio.TimeoutError:
                            full_reply = ""
                        if full_reply.strip() and reply_matches_language(full_reply, detected_lang):
                            retried_ok = True
                            log.info("Wrong-language reply retried successfully.")
                        else:
                            log.warn(f"Retry also wrong-language for lang={detected_lang}: {full_reply[:120]}")

                    if not retried_ok:
                        full_reply = fallback_reply(
                            "reply_language_mismatch",
                            self.state.current,
                            lang=detected_lang,
                        )
                        tts_buffer = full_reply
                        first_tts_chunk = True

                self.llm_in_flight = False

                if strip_emotion_tags(tts_buffer).strip():
                    await enqueue_tts_chunk(tts_buffer, first_tts_chunk)

                await tts_queue.put(None)
                await tts_worker_task

                spoken = strip_emotion_tags(full_reply).strip()
                if spoken:
                    reply_state = validate_reply_emotion().final_emotion
                    self.state.apply_emotion(f"[{reply_state.upper()}]")
                    await self._sync_energy()
                    log.info(f"🤖 {full_reply[:120]}{'…' if len(full_reply) > 120 else ''}")

                log.info(f"🤖 Full reply: {full_reply}")
                runtime_status.update(last_llm_ok=True, last_error=None)
                runtime_status.mark_mode("normal", "ok")
                self.last_activity = asyncio.get_event_loop().time()

                # Append exchange to conversation log immediately (Bug: was never saved!)
                if spoken:
                    entry = {
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "user": text,
                        "mbuddy": spoken,
                        "lang": detected_lang,
                    }
                    self.memory.setdefault("conversation_log", []).append(entry)
                    self.reply_count += 1
                    self.memory["conversation_log"] = self.memory["conversation_log"][-200:]
                    self._update_relationship_state(text, spoken)
                    maybe_capture_reminder(text, self.memory)
                    maybe_update_project_thread(text, spoken, self.memory)
                    maybe_write_daily_summary(self.memory, self.state)
                    self._schedule_vector_memory(text, spoken, entry)

                include_compress = len(self.memory["conversation_log"]) % 10 == 0
                self._schedule_postprocess(text, full_reply, include_compress=include_compress)
                # Save memory every turn to prevent context loss on crashes/reconnects
                await save_memory(self.memory)

            except asyncio.TimeoutError:
                log.warn(f"Brain timeout: no reply after {FIRST_TEXT_TIMEOUT_S:.0f}s")
                runtime_status.update(last_llm_ok=False, last_error="llm_timeout")
                runtime_status.mark_mode("limited", "llm_timeout")
                done_now = _done_this_turn(turn_started_at)
                if done_now:
                    fallback = f"[NORMAL] Sorry, my reply took too long. What I did: {done_now}"
                else:
                    fallback = fallback_reply("llm_timeout", self.state.current, lang=detected_lang)
                await self._send_checked(fallback, self.state.current, lang=detected_lang)
                said = strip_emotion_tags(fallback).strip()
                if said:
                    self.memory.setdefault("conversation_log", []).append(
                        {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "user": text, "mbuddy": said, "lang": detected_lang})
                    self.reply_count += 1
            except Exception as e:
                log.error(f"Brain error ({type(e).__name__})", e)
                runtime_status.update(last_llm_ok=False, last_error=f"llm_error:{type(e).__name__}")
                runtime_status.mark_mode("limited", "llm_error")
                fallback = fallback_reply("llm_error", self.state.current, lang=detected_lang)
                await self._send_checked(fallback, self.state.current, lang=detected_lang)
            finally:
                if "tts_worker_task" in locals() and tts_worker_task and not tts_worker_task.done():
                    tts_worker_task.cancel()
                self.llm_in_flight = False
                self.reply_speaking = False
                skip_ready = self.barge_cancelled
                self.barge_armed = False
                self.audio.reset_barge()
                self.last_audio_output_at = asyncio.get_event_loop().time()
                runtime_status.update(
                    thinking=False,
                    speaking=False,
                    awake=self.is_awake,
                    emotion=self.state.current,
                    energy=self.state.energy,
                    memory_count=len(self.memory.get("conversation_log", [])),
                    inner_count=len(self.memory.get("inner_log", [])),
                    daily_stats=build_daily_stats(self.memory),
                    active_project_thread=(self.memory.get("project_threads") or [{}])[-1].get("title", ""),
                    pending_reminders_count=len([r for r in self.memory.get("reminders", []) if r.get("status") == "open"]),
                )
                if not self.ws.closed and self.playback_started and not skip_ready:
                    await self._wait_playback_done()
                    await self._safe_send_str(self.protocol.QUIET)
                    await self._safe_send_str(self.protocol.heard("ready"))
                self.playback_started = False
                if skip_ready:
                    self.barge_cancelled = False
                self._current_typed = False
                tl = timeline.current()
                if tl is not None and tl.marks:
                    log.info(f"[Turn] timeline (s from the turn's start): {tl.line()}")
                # After the reply, off the turn: the mood summary (it throttles itself).
                self._track_task(self.get_mood_context(), "mood_refresh")
                if self.turn_queue and (self.is_awake or self.turn_queue[0][2]):
                    qt, ql, qtyped = self.turn_queue.pop(0)
                    self.response_task = self._track_task(self._respond(qt, ql, qtyped), "queued_response")

                # Bug 5 fix: drain audio that arrived while the LLM was in flight.
                # These chunks are the user's *next* utterance, buffered rather than
                # dropped while we were generating the current reply.
                if self._inflight_audio_buf and self.is_awake and not self.ws.closed:
                    buffered = list(self._inflight_audio_buf)
                    self._inflight_audio_buf.clear()
                    for chunk_data in buffered:
                        await self.handle_audio(chunk_data)

    def _decide_behavior(self, event: str, text: str = ""):
        world_model = build_world_model(
            self.state,
            session=self,
            runtime=runtime_status,
            memory=self.memory,
            event=event,
            text=text,
        )
        cognitive_state = build_cognitive_state(
            self.state,
            session=self,
            runtime=runtime_status,
            memory=self.memory,
            world_model=world_model,
        )
        decision = decide_behavior(cognitive_state, event=event, text=text)
        self._record_behavior_trace(decision)
        runtime_status.update(
            world_model=world_model.to_dict(),
            capability_registry=world_model.capability_registry,
            disconnected_features=world_model.disconnected_features,
            cognitive_state=cognitive_state.to_dict(),
            behavior_decision=decision.to_dict(),
        )
        return cognitive_state, decision

    def _record_behavior_trace(self, decision, *, source: str = "cognitive") -> None:
        trace = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "goal": getattr(decision, "goal", ""),
            "intent": getattr(decision, "intent", ""),
            "emotion": getattr(decision, "emotion", ""),
            "action": getattr(decision, "action", "") or getattr(decision, "animation", ""),
            "animation": getattr(decision, "animation", ""),
            "sfx": getattr(decision, "sfx", ""),
            "tool_used": getattr(decision, "tool", ""),
            "reason": getattr(decision, "reason", ""),
            "world_state_snapshot": getattr(decision, "world_state_snapshot", {}),
            "need": getattr(decision, "need", ""),
            "emotion_reason": getattr(decision, "emotion_reason", ""),
            "tool_selected": getattr(decision, "tool", ""),
            "tool_reason": getattr(decision, "tool_reason", ""),
            "animation_selected": getattr(decision, "animation", ""),
            "animation_reason": getattr(decision, "animation_reason", ""),
            "sfx_selected": getattr(decision, "sfx", ""),
            "sfx_reason": getattr(decision, "sfx_reason", ""),
            "reply_reason": getattr(decision, "reply_reason", ""),
            "priority": getattr(decision, "priority", "normal"),
            "source": source,
            "decision": decision.to_dict() if hasattr(decision, "to_dict") else {},
            "competing_behaviors": getattr(decision, "competing_behaviors", []),
            "conflicts": getattr(decision, "conflicts", []),
        }
        runtime_status.record_behavior_trace(trace)

    async def _apply_behavior_decision(self, decision, *, allow_sfx: bool = True) -> None:
        if not decision:
            return
        if getattr(decision, "animation", ""):
            await self._send_action(
                decision.animation,
                decision.animation_ms or 1500,
                reason=decision.reason,
                priority=decision.priority,
                record_trace=False,
            )
        if allow_sfx and getattr(decision, "sfx", ""):
            await self._play_sfx(decision.sfx, force=True, reason=decision.reason, priority=decision.priority, record_trace=False)

    async def _send_action(
        self,
        name: str,
        duration_ms: int = 1500,
        *,
        reason: str = "",
        priority: str = "normal",
        record_trace: bool = True,
    ) -> None:
        """Trigger a temporary ESP32 visual action without changing base mood."""
        if self.ws.closed:
            return
        if not getattr(self, "auto_visuals_enabled", True):
            return
        if name == "clock_timer":
            await self._safe_send_str(self.protocol.time_update())
        if await self._safe_send_str(self.protocol.action(name, duration_ms)):
            thought = ACTION_THOUGHTS.get(name, "I am showing this as a quiet visual state.")
            await self._safe_send_str(self.protocol.inner_thought(thought))
            runtime_status.update(current_action=name, current_action_thought=thought)
            if record_trace:
                runtime_status.record_behavior_trace({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "goal": "show_visual_action",
                    "intent": "render_animation",
                    "emotion": self.state.current,
                    "action": name,
                    "animation": name,
                    "sfx": "",
                    "tool_used": "",
                    "reason": reason or thought,
                    "priority": priority,
                    "source": "direct_action",
                    "decision": {},
                    "competing_behaviors": [],
                    "conflicts": [],
                })

    async def _play_sfx(
        self,
        name: str,
        *,
        force: bool = False,
        reason: str = "",
        priority: str = "normal",
        record_trace: bool = True,
    ) -> None:
        """Play a short generated companion sound effect over the existing PCM path."""
        if self.ws.closed or (self.is_awake and not force):
            return
        volume = 1.0 if force else min(0.85, max(0.35, self._inner_voice_volume() * 2.0))
        pcm_bytes = synthesize_sfx_pcm(name, volume=volume)
        if not pcm_bytes:
            return
        if not await self._safe_send_str(self.protocol.sfx(name)):
            return
        runtime_status.update(last_sfx=name, speaking=False, thinking=False)
        if record_trace:
            runtime_status.record_behavior_trace({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "goal": "play_sound_effect",
                "intent": "sound_feedback",
                "emotion": self.state.current,
                "action": "",
                "animation": "",
                "sfx": name,
                "tool_used": "",
                "reason": reason or f"Sound effect {name} was requested by the active behavior path.",
                "priority": priority,
                "source": "direct_sfx",
                "decision": {},
                "competing_behaviors": [],
                "conflicts": [],
            })
        _is_out = [False]
        self.playback_done_event.clear()
        self.playback_started = True
        self.playback_started_at = asyncio.get_event_loop().time()
        self.barge_cancelled = False
        self.barge_armed = False
        self.audio.reset_barge()
        try:
            await stream_tts_pcm(
                self.ws,
                self.speech_lock,
                pcm_bytes,
                _is_out,
                session=self,
            )
            if not self.ws.closed:
                await self._wait_playback_done(timeout=1.2)
                await self._safe_send_str(self.protocol.QUIET)
        finally:
            self.is_outputting_audio = _is_out[0]
            self.playback_started = False
            runtime_status.update(speaking=False, awake=self.is_awake)


    async def _send_checked(
        self,
        reply: str,
        state: str,
        lang: str | None = None,
        *,
        reopen_mic: bool = True,
        fade_in: bool = False,
        fade_out: bool = True,
        leader_ms: int = FIRST_LEADER_MS,
        trailer_ms: int = TRAILER_MS,
    ) -> None:
        target_lang = lang or self.active_language
        if reply.strip().startswith("[") and not reply_matches_language(reply, target_lang):
            log.warn(f"Blocked wrong-language direct reply for lang={target_lang}: {reply[:80]}")
            reply = fallback_reply("reply_language_mismatch", state, lang=target_lang)

        clean = strip_for_tts(reply) or strip_emotion_tags(reply) or reply

        # Record what was said before any early return, so the text-chat
        # endpoint reports the reply even when synthesis is skipped.
        self.last_reply_text = clean

        if not any(c.isalnum() for c in clean):
            log.info(f"Skipping TTS for non-verbal chunk: '{clean}'")
            return

        if self._suppress_speech:
            # Caller asked for a silent turn. The display still shows the
            # emotion and memory still records the exchange; only the audio
            # is skipped.
            await self._safe_send_str(self.protocol.speaking(state, clean))
            return

        self.playback_done_event.clear()
        self.playback_started = False
        self.barge_cancelled = False

        # Signal to ESP32 that we're alive and working during the edge-tts HTTP call (~1-2s).
        # Without this, the ESP32 gets radio silence during synthesis and may spuriously disconnect.
        if not await self._safe_send_str(self.protocol.THINKING):
            return

        try:
            pcm_bytes = await synthesize_tts_pcm(
                clean,
                emotion=state,
                lang=target_lang,
                fade_in=fade_in,
                fade_out=fade_out,
                leader_ms=leader_ms,
                trailer_ms=trailer_ms,
                volume=self._voice_volume(),
            )
        except Exception as exc:
            runtime_status.update(last_tts_ok=False, last_error=f"tts_error:{type(exc).__name__}")
            runtime_status.mark_mode("limited", "tts_error")
            log.error("TTS synthesis error", exc)
            return
        if not pcm_bytes:
            runtime_status.update(last_tts_ok=False, last_error="tts_empty")
            return

        runtime_status.update(
            speaking=True,
            thinking=False,
            emotion=state,
            last_reply=clean,
            awake=self.is_awake,
        )
        runtime_status.record_behavior_trace({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "goal": "direct_speech",
            "intent": "speak_prebuilt_reply",
            "emotion": state,
            "action": "speak",
            "animation": "",
            "sfx": "",
            "tool_used": "",
            "reason": f"Direct speech path selected emotion {state} for a prebuilt reply.",
            "priority": "normal",
            "source": "direct_speech",
            "decision": {},
            "competing_behaviors": [],
            "conflicts": [],
        })
        if not await self._safe_send_str(self.protocol.speaking(state, clean)):
            return
        self.playback_started = True
        self.playback_started_at = asyncio.get_event_loop().time()

        _is_out = [False]
        self.barge_armed = False
        self.audio.reset_barge()
        self.tts_task = self._track_task(
            stream_tts_pcm(
                self.ws,
                self.speech_lock,
                pcm_bytes,
                _is_out,
                session=self,
            ),
            "tts_stream",
        )

        try:
            await self.tts_task
        except asyncio.CancelledError:
            log.warn(f"TTS Interrupted: '{clean[:20]}…'")
        finally:
            self.is_outputting_audio = _is_out[0]
            skip_ready = self.barge_cancelled
            self.barge_armed = False
            self.audio.reset_barge()
            if reopen_mic:
                self.last_audio_output_at = asyncio.get_event_loop().time()
                runtime_status.update(speaking=False, thinking=False, awake=self.is_awake)
                if not self.ws.closed and self.playback_started and not skip_ready:
                    await self._wait_playback_done()
                    await self._safe_send_str(self.protocol.QUIET)
                    await self._safe_send_str(self.protocol.heard("ready"))
                self.playback_started = False
                if skip_ready:
                    self.barge_cancelled = False

    async def _speak_inner_thought(self, thought: str, state: str) -> None:
        """Speak private thoughts quietly without treating them as a full reply."""
        clean = strip_for_tts(thought).strip() or strip_emotion_tags(thought).strip() or thought.strip()
        if self.ws.closed or self.is_awake or not any(c.isalnum() for c in clean):
            return

        self.current_inner_thought = clean
        self.memory["last_inner_thought"] = clean

        try:
            pcm_bytes = await synthesize_tts_pcm(
                clean,
                emotion=state,
                lang=self.active_language or "en",
                fade_in=True,
                fade_out=True,
                leader_ms=80,
                trailer_ms=20,
                volume=self._inner_voice_volume(),
            )
        except Exception as exc:
            runtime_status.update(last_tts_ok=False, last_error=f"inner_tts_error:{type(exc).__name__}")
            log.error("Inner thought TTS synthesis error", exc)
            return

        if not pcm_bytes or self.ws.closed or self.is_awake:
            if not pcm_bytes:
                runtime_status.update(last_tts_ok=False, last_error="inner_tts_empty")
            return

        runtime_status.update(
            speaking=True,
            thinking=False,
            emotion=state,
            last_reply=clean,
            awake=self.is_awake,
            inner_count=len(self.memory.get("inner_log", [])),
        )

        _is_out = [False]
        self.playback_done_event.clear()
        self.playback_started = True
        self.playback_started_at = asyncio.get_event_loop().time()
        self.barge_cancelled = False
        self.barge_armed = False
        self.audio.reset_barge()
        self.tts_task = self._track_task(
            stream_tts_pcm(
                self.ws,
                self.speech_lock,
                pcm_bytes,
                _is_out,
                session=self,
            ),
            "inner_tts_stream",
        )

        interrupted = False
        try:
            await self.tts_task
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            outer_cancelled = bool(
                current_task is not None
                and getattr(current_task, "cancelling", lambda: 0)()
            )
            interrupted = True
            self.memory["interrupted_inner_thought"] = clean
            self.state.unresolved_thought = clean[:100]
            log.info(f"Inner thought interrupted: '{clean[:80]}'")
            if outer_cancelled:
                raise
        finally:
            self.is_outputting_audio = _is_out[0]
            self.barge_armed = False
            self.audio.reset_barge()
            self.last_audio_output_at = asyncio.get_event_loop().time()
            runtime_status.update(speaking=False, thinking=False, awake=self.is_awake)
            if not self.ws.closed and self.playback_started and not interrupted:
                await self._wait_playback_done(timeout=2.5)
                await self._safe_send_str(self.protocol.QUIET)
            self.playback_started = False
            if self.current_inner_thought == clean:
                self.current_inner_thought = ""
            if interrupted:
                self.barge_cancelled = False

    async def _wait_playback_done(self, timeout: float = 4.5) -> None:
        """Wait for firmware playback drain (Bug 6 fix: raised from 2.2s to 4.5s).

        2.2 s was too short for long multi-sentence replies whose last PCM chunk
        can take up to ~2 s to stream plus TAIL_WAIT + DRAIN_WAIT on the server
        side. Sending [QUIET] too early re-opens the mic while the speaker is
        still playing, causing echo that re-triggers the lockout loop.
        """
        try:
            await asyncio.wait_for(self.playback_done_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.playback_done_event.set()

    def barge_in_ready(self) -> bool:
        """
        Whether a mic chunk right now is allowed to interrupt the reply.

        False while barge-in is disabled outright, and during the grace
        period just after playback starts. MBUDDY's own speaker onset is
        loudest then, so an energy-based detector would otherwise have a
        good chance of interrupting MBUDDY with MBUDDY.
        """
        if not BARGE_IN_ENABLED:
            return False
        if not self.playback_started_at:
            return True
        elapsed_ms = (
            asyncio.get_event_loop().time() - self.playback_started_at
        ) * 1000.0
        return elapsed_ms >= BARGE_IN_GRACE_PERIOD_MS

    def _note_loud_chunk(self, data: bytes) -> bool:
        """True after enough consecutive very loud chunks (fallback if no [BARGE] from ESP32)."""
        if not self.barge_in_ready():
            # Still let the detector learn the room and echo level, but do not
            # let it fire — otherwise the noise floor never adapts to playback.
            self.audio.note_loud_chunk(data)
            self.audio.reset_barge()
            return False
        return self.audio.note_loud_chunk(data)

    async def _handle_barge_in(self) -> None:
        """Stop playback; mic packets after this are treated as user speech."""
        if not self.is_outputting_audio and not self.reply_speaking:
            return
        self.barge_armed = True
        self.barge_cancelled = True
        if self.current_inner_thought:
            self.memory["interrupted_inner_thought"] = self.current_inner_thought
            self.state.unresolved_thought = self.current_inner_thought[:100]
        self.playback_done_event.set()
        self.playback_started = False
        self.playback_started_at = 0.0
        # Clear any audio buffered during LLM flight — it predates the barge-in
        # and replaying it after cancel would trigger a stale utterance.
        self._inflight_audio_buf.clear()
        runtime_status.update(barge_armed=True, speaking=False, thinking=False)
        self.reply_speaking = False
        if self.tts_task and not self.tts_task.done():
            self.tts_task.cancel()
        self.is_outputting_audio = False
        # Talking over a typed turn's reply stops the speech, not the work:
        # the typed request still gets its answer (S116 P3.1).
        if self.response_task and not self.response_task.done() and not self._current_typed:
            self.response_task.cancel()
        if not self.ws.closed:
            try:
                await self._safe_send_str(self.protocol.STOP)
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────
    # Goodbye flows
    # ──────────────────────────────────────────────────────────
    async def _handle_soft_goodbye(self) -> None:
        reply = SOFT_GOODBYE_TABLE.get(self.state.current, "[SKEPTIC] you actually leaving?")
        log.info(f"🤖 {reply}")
        await self._send_checked(reply, self.state.current)

    async def _handle_final_goodbye(self) -> None:
        reply = FINAL_GOODBYE_TABLE.get(self.state.current, "[SKEPTIC] I'll be around.")
        log.info(f"🤖 {reply}")
        await self._send_checked(reply, self.state.current)
        self._set_awake(False)
        self.goodbye_pending = False
        self._clear_voice_turns()  # a typed request still gets its answer
        await save_memory(self.memory)
        self._start_inner()

    # ──────────────────────────────────────────────────────────
    # Wake / sleep helpers
    # ──────────────────────────────────────────────────────────
    def _set_awake(self, awake: bool) -> None:
        self.is_awake      = awake
        self.awake_flag[0] = awake
        runtime_status.update(awake=awake, emotion=self.state.current, energy=self.state.energy, needs=self.state.needs)
        self._track_task(self._sync_energy(), "energy_sync")
        if awake:
            self.last_activity = asyncio.get_event_loop().time()
            # Load the voice now, alongside hearing and thinking, so the first
            # reply doesn't wait ~5 s for it (it stays unloaded while asleep).
            from tts import local_tts
            self._track_task(asyncio.to_thread(local_tts.warm), "tts_warm")
            # Warm the model the moment MBUDDY wakes (the wake word, or a typed
            # turn), before the user finishes the sentence, if it isn't loaded.
            self._brain_warm_task()
        else:
            self._clear_voice_turns()

    def _cancel_inner(self) -> None:
        if self.inner_task and not self.inner_task.done():
            self.inner_task.cancel()
        self.inner_task = None

    def _start_inner(self) -> None:
        self._cancel_inner()
        self.inner_task = self._track_task(
            inner_life_loop(
                self.ws,
                self.memory,
                self.awake_flag,
                self.state,
                on_thought=self._speak_inner_thought,
                on_action=self._send_action,
                on_sfx=self._play_sfx,
                can_run=lambda: getattr(self, "auto_visuals_enabled", True) and time.time() >= getattr(self, "manual_visual_until", 0.0),
            ),
            "inner_life",
        )

    # ──────────────────────────────────────────────────────────
    # Post-process (fact extraction, emotional tagging)
    # ──────────────────────────────────────────────────────────
    def _schedule_postprocess(self, text: str, reply: str, include_compress: bool = False) -> None:
        self._postprocess_queue.append((text, reply, include_compress))
        if not self.postprocess_task or self.postprocess_task.done():
            self.postprocess_task = self._track_task(self._run_postprocess_queue(), "postprocess")

    async def _run_postprocess_queue(self) -> None:
        while self._postprocess_queue and not self.ws.closed:
            text, reply, include_compress = self._postprocess_queue.popleft()
            await asyncio.sleep(15.0)
            retries_left = POSTPROCESS_MAX_RETRIES
            while (self.llm_in_flight or self.audio.is_speaking) and retries_left > 0:
                retries_left -= 1
                log.info(f"Postprocess deferred - retries left: {retries_left}")
                await asyncio.sleep(15.0)
            if self.llm_in_flight or self.audio.is_speaking:
                log.warn("Postprocess gave up after max retries.")
                continue
            try:
                await extract_and_save_facts(text, reply, self.memory)
                await asyncio.sleep(1.0)
                await extract_timeline_event(text, reply, self.memory)
                maybe_capture_reminder(text, self.memory)
                maybe_update_project_thread(text, strip_emotion_tags(reply), self.memory)
                maybe_write_daily_summary(self.memory, self.state)
                await asyncio.sleep(1.0)
                await tag_emotional_memory(text, reply, self.memory)
                if include_compress:
                    await compress_identity_snapshot(self.memory)
                    await asyncio.sleep(1.0)
                await save_memory(self.memory)
            except Exception:
                log.warn(f"Postprocess error: {traceback.format_exc()}")

    # ──────────────────────────────────────────────────────────
    # Expression decay
    # ──────────────────────────────────────────────────────────
    async def handle_expression_decay(self) -> None:
        """
        Return the face to a resting mood once a reply's emotion has passed.

        `tick()` only ran at the idle timeout, so an emotion set by a reply
        stayed on the OLED until MBUDDY fell asleep. An expression should
        outlive the sentence that caused it by a few seconds, not by minutes.

        Nothing here overrides a live moment: while MBUDDY is thinking,
        speaking or listening, the current expression stands.
        """
        while not self.ws.closed and not self.closing:
            await asyncio.sleep(1.5)

            # Deliberately not gated on `is_awake`. Asleep is when the face
            # most needs to be at rest, and skipping it there left the last
            # expression frozen on the panel for as long as MBUDDY dozed.
            now = asyncio.get_event_loop().time()

            # Notice a change from any source — a reply, the inner life, or a
            # dashboard command — and restart the clocks from it. Detecting it
            # here means no call site has to remember to stamp them.
            if self.state.current != self._last_seen_emotion:
                self._last_seen_emotion = self.state.current
                self.last_expression_at = now
                self.expression_set_at = now
                continue

            settle = self.state.settle_seconds()
            held = now - self.last_expression_at
            shown = now - self.expression_set_at

            # An expression during speech or thought belongs to that moment.
            # `barge_armed` is deliberately not part of this: it can be set by
            # ambient room noise while MBUDDY is idle, and treating that as
            # activity kept resetting the hold so the face never settled.
            busy = (self.llm_in_flight or self.reply_speaking
                    or self.is_outputting_audio or self.audio.is_speaking)

            # Busy normally defers the change — but not forever. A provider
            # that is rate-limited retries for minutes with `llm_in_flight`
            # set, and a face frozen on the last reply's emotion for that long
            # is exactly the stuck expression this loop exists to prevent.
            if busy and shown < settle * 3:
                self.last_expression_at = now
                continue

            if held < settle:
                continue

            resting = self.state.resting_mood()
            if resting == self.state.current:
                # Already at rest: nothing to settle, so restart both clocks.
                self.last_expression_at = now
                self.expression_set_at = now
                continue

            previous = self.state.current
            # Clear the inertia lock: the settle period has already served
            # that purpose, and leaving it set makes tick() a no-op here.
            self.state._emotion_lock = 0
            self.state.tick()
            self.last_expression_at = now
            self.expression_set_at = now

            log.info(f"😌 Expression settled: {previous} → {self.state.current}")
            runtime_status.update(emotion=self.state.current, energy=self.state.energy)
            await self._safe_send_str(self.protocol.state_quiet(self.state.current))

    # ──────────────────────────────────────────────────────────
    # Idle-timeout background task
    # ──────────────────────────────────────────────────────────
    async def handle_awake_timeout(self) -> None:
        while not self.ws.closed:
            await asyncio.sleep(5)
            if not self.is_awake:
                continue
            if self.llm_in_flight or self.reply_speaking or self.is_outputting_audio or self.audio.is_speaking:
                self.last_activity = asyncio.get_event_loop().time()
                continue
            now = asyncio.get_event_loop().time()
            if now - self.last_activity < AWAKE_IDLE_TIMEOUT:
                continue
            await self.go_to_sleep()

    async def go_to_sleep(self) -> None:
        """Back to listening only for "Hey Buddy"."""
        self._set_awake(False)
        self._woken_by_voice = False
        self.goodbye_pending = False
        self.state.tick()
        runtime_status.update(awake=False, emotion=self.state.current, energy=self.state.energy, needs=self.state.needs)
        self._track_task(self._sync_energy(), "energy_sync")
        await self._safe_send_str(self.protocol.state_quiet(self.state.current))
        self._start_inner()
        # One timer: every sleep used to add another (live, a dozen "unloaded" lines).
        previous = getattr(self, "_unload_timer", None)
        if previous is not None and not previous.done():
            previous.cancel()
        self._unload_timer = self._track_task(self._unload_after_sleep(), "model_unload")

    async def _unload_after_sleep(self) -> None:
        """Free the model's GPU memory after the user's chosen minutes asleep
        (setting unload_model_after_min, 0 = never, the default; U1)."""
        from core import ui_settings
        minutes = int(ui_settings.get("unload_model_after_min") or 0)
        if minutes <= 0:
            return
        await asyncio.sleep(minutes * 60)
        from brain import ollama_service
        from core import jobs
        # Background work needs the model: MBUDDY may look asleep, but the
        # model stays until the jobs end (live, it was unloaded mid-job).
        while jobs.running() and not self.is_awake:
            await asyncio.sleep(30)
        if self.is_awake or not await asyncio.to_thread(ollama_service.model_loaded):
            return  # awake again, or already unloaded
        await asyncio.to_thread(ollama_service.unload)
        self._model_unloaded = True
        log.info(f"[Brain] unloaded after {minutes} min asleep")

    def _model_load_task(self) -> "asyncio.Future":
        """One shared model load: the wake word and the turn that follows wait
        on the same one instead of loading the model twice."""
        task = getattr(self, "_loading", None)
        if task is None or task.done():
            task = self._loading = asyncio.ensure_future(self._load_if_unloaded())
        return task

    async def _load_if_unloaded(self) -> bool:
        """Put the model in memory if it isn't (asked of Ollama, not a flag).
        True when it had to."""
        from brain import brain_adapters, ollama_service
        if "ollama" not in brain_adapters.get_available_providers():
            return False
        if await asyncio.to_thread(ollama_service.model_loaded):
            return False
        await asyncio.to_thread(self._reload_brain)
        self._model_unloaded = False
        return True

    def _brain_warm_task(self) -> "asyncio.Future":
        """Waking up ahead of a turn (the wake word, the user coming back): load
        the model, then read its prompts in the background. A turn that arrives
        meanwhile only waits for the load: the prompt reading is skipped, and the
        turn's own prompts fill the cache (live, reading them first cost the
        turn 33 s and its first token still took 22 s, U1c)."""
        task = getattr(self, "_warming", None)
        if task is None or task.done():
            task = self._warming = asyncio.ensure_future(self._warm_in_background())
        return task

    async def _warm_in_background(self) -> None:
        from brain import ollama_service
        if not await self._model_load_task():
            return
        busy = lambda: bool(getattr(self, "_turn_waiting", False) or getattr(self, "llm_in_flight", False))  # noqa: E731
        try:
            parts = await asyncio.to_thread(ollama_service.warm_prompts, busy)
            log.info("[Brain] warmed after waking: " + ", ".join(parts))
        except Exception as exc:
            log.warn(f"[Brain] prompt warm-up failed: {type(exc).__name__}: {exc}"[:200])

    async def _wake_before_turn(self) -> bool:
        """Before a turn's stages: if the model was unloaded (asleep), say so and
        load it first, the model only, so the turn's timers don't run out on a
        cold load (U1b) and the turn isn't held for prompt warm-ups (U1c). True
        if it had to load."""
        from brain import brain_adapters, ollama_service
        if "ollama" not in brain_adapters.get_available_providers():
            return False
        if await asyncio.to_thread(ollama_service.model_loaded):
            return False
        self._turn_waiting = True
        try:
            runtime_status.update(thinking=True)
            from core import ui_settings
            if ui_settings.get("speak_replies") and not getattr(self, "_suppress_speech", False):
                self._track_task(self._send_checked("[NORMAL] Waking up…", self.state.current, lang="en"), "waking")
            await self._model_load_task()
        finally:
            self._turn_waiting = False
        return True

    def _reload_brain(self) -> None:
        """Blocking: put the model back in memory after it was unloaded."""
        import time
        from brain import ollama_service
        started = time.monotonic()
        try:
            note = ollama_service.load_model()
        except Exception as exc:
            note = f"failed: {type(exc).__name__}"
        log.info(f"[Brain] reloaded on wake in {time.monotonic() - started:.1f} s ({note})")

    async def sleep_after_typed_turn(self, limit_s: float = 90.0) -> None:
        """
        A typed message doesn't wake the voice (live: after a typed chat, room
        talk and TV were answered for minutes). Once the typed reply has been
        said, go back to sleep, unless the user woke the voice meanwhile.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + limit_s
        while loop.time() < deadline and (self.llm_in_flight or self.reply_speaking or self.is_outputting_audio):
            await asyncio.sleep(0.25)
        if self.is_awake and not getattr(self, "_woken_by_voice", False):
            await self.go_to_sleep()


# ──────────────────────────────────────────────────────────────
# aiohttp route handlers
# ──────────────────────────────────────────────────────────────

async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="MBUDDY v4 — stable deterministic pipeline.")


# One live connection per device id. Two bridges as "pc-shell" (the app's
# hidden window and a Chrome tab left open) were both served: two mics fed
# one brain. The app's own bridge owns the mic; a browser page is turned
# away while it is connected; otherwise the newest connection replaces the
# old one.
_DEVICE_CONNECTIONS: dict[str, tuple] = {}


def _is_app_bridge(firmware: str) -> bool:
    return str(firmware).startswith("shell-app")


def claim_device(device_id: str, firmware: str, ws) -> str | None:
    """Register `ws` for `device_id`; a reject reason, or None when accepted."""
    current = _DEVICE_CONNECTIONS.get(device_id)
    if current is not None and not current[0].closed:
        old_ws, old_firmware = current
        if _is_app_bridge(old_firmware) and not _is_app_bridge(firmware):
            return "superseded"
        log.info(f"Device {device_id!r} reconnected ({firmware}); closing its previous connection")
        asyncio.ensure_future(old_ws.close(code=4001, message=b"superseded"))
    _DEVICE_CONNECTIONS[device_id] = (ws, firmware)
    return None


def release_device(device_id: str, ws) -> bool:
    """Forget `ws` if it is still the device's connection; True if it was."""
    current = _DEVICE_CONNECTIONS.get(device_id)
    if current is not None and current[0] is ws:
        del _DEVICE_CONNECTIONS[device_id]
        return True
    return False


async def websocket_handler(request: web.Request) -> web.StreamResponse:
    log.info(f"Incoming connection: {request.remote}")
    ws = web.WebSocketResponse(protocols=["arduino"])
    if not ws.can_prepare(request):
        return web.Response(status=400, text="WebSocket required")

    await ws.prepare(request)

    # ── Phase 1B: HELLO handshake, required before ONLINE ───────────────
    # The device's first message must be a versioned JSON HELLO carrying its
    # identity and the shared device token (api/device_protocol.py:parse_hello,
    # api/auth.py:validate_device_token). Nothing else happens until it's
    # accepted: no session, no device_connect(), no background tasks — a
    # rejected/timed-out handshake leaves runtime_status exactly as it was,
    # so it can never mark an unauthenticated attempt as ONLINE.
    hello, reject_reason = None, None
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=HELLO_TIMEOUT_S)
    except asyncio.TimeoutError:
        reject_reason = "hello_timeout"
    else:
        if msg.type == aiohttp.WSMsgType.TEXT:
            hello = parse_hello(msg.data)
            if hello is None:
                reject_reason = "invalid_hello"
        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            # Peer dropped before completing the handshake — nothing to reject-send.
            log.info(f"Disconnected before HELLO: {request.remote}")
            return ws
        else:
            reject_reason = "hello_required"  # binary/other before HELLO violates the handshake order

    if hello is not None and not validate_device_token(hello.get("auth_token")):
        reject_reason = "unauthorized"
        hello = None

    if hello is not None:
        reject_reason = claim_device(hello["device_id"], hello.get("firmware_version", ""), ws)

    if reject_reason is not None:
        # A browser tab left open retries every 2 s while the app has the
        # mic; that is expected, not a warning.
        (log.info if reject_reason == "superseded" else log.warn)(f"HELLO rejected from {request.remote}: {reject_reason}")
        try:
            await ws.send_str(hello_reject(reject_reason))
        except Exception:
            pass
        await ws.close()
        return ws

    log.ok(f"Connected: {request.remote} (device_id={hello['device_id']!r})")
    runtime_status.device_connect(
        remote=str(request.remote),
        device_id=hello["device_id"],
        session_id=hello["session_id"],
        firmware_version=hello["firmware_version"],
        capabilities=hello.get("capabilities") or {},
    )
    await ws.send_str(hello_ack(hello["session_id"]))
    runtime_status.mark_mode("normal", "device_connected")

    memory  = load_memory()
    session = MBUDDYSession(ws, memory)
    set_active_session(session)

    # Clear stale ESP32 audio/lockout state immediately on every new connection.
    # Without this, speakerLockout persists from [SPEAKING] that was never cleared
    # by [QUIET] (connection was lost mid-TTS), locking the mic for ~40 s per cycle.
    await session._safe_send_str(session.protocol.STOP)

    session._start_inner()
    timeout_task = asyncio.create_task(session.handle_awake_timeout())
    decay_task = asyncio.create_task(session.handle_expression_decay())

    async def warm_vector_memory() -> None:
        try:
            from memory.vector_store import get_vector_store

            await asyncio.to_thread(get_vector_store().warmup)
        except Exception:
            pass

    vector_warm_task = asyncio.create_task(warm_vector_memory())

    async def keepalive() -> None:
        while not ws.closed and not session.closing:
            await asyncio.sleep(60)
            try:
                await ws.ping()
                log.info("💓")
            except Exception as e:
                log.warn(f"[Heartbeat] Ping failure: {e}")
                break

    ka_task = asyncio.create_task(keepalive())

    async def boot_notice() -> None:
        try:
            await asyncio.sleep(0)
            if ws.closed:
                return
            await session._safe_send_str(session.protocol.THINKING)
            # Only the device's display is told "thinking" here. The live state is NOT: nothing would ever clear it
            # if no turn follows (the buddy then showed "thinking" for ever, and the warm-up kept waiting for "a turn").
            runtime_status.update(thinking=False, speaking=False, awake=session.is_awake)
        except Exception:
            log.warn(f"Boot notice failed: {traceback.format_exc()}")

    boot_task = asyncio.create_task(boot_notice())

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                runtime_status.device_heartbeat()
                now = asyncio.get_event_loop().time()
 
                # During TTS: drop normal-level mic (echo). Only act on [BARGE] or very loud audio.
                if session.is_outputting_audio and not session.barge_armed:
                    if session._note_loud_chunk(msg.data):
                        log.info(
                            "Barge-in armed "
                            f"(rms={session.audio.last_barge_rms:.4f}, "
                            f"gate={session.audio.last_barge_gate:.4f}, "
                            f"baseline={max(BARGE_IN_MIN_RMS, session.audio.noise_floor * BARGE_IN_RMS_RATIO):.4f}, "
                            f"grace={BARGE_IN_GRACE_PERIOD_MS}ms)"
                        )
                        await session._handle_barge_in()
                        await session.handle_audio(msg.data)
                    continue
 
                if session.barge_armed:
                    await session.handle_audio(msg.data)
                    continue
 
                if session.reply_speaking or session.llm_in_flight:
                    # Bug 5 fix: buffer audio arriving during LLM flight instead of
                    # silently dropping it. The buffer is drained after _respond finishes
                    # so the user's next utterance is not lost if they speak while the
                    # LLM is still generating the current reply.
                    session._inflight_audio_buf.append(msg.data)
                    continue
 
                if now - session.last_audio_output_at < POST_TTS_STT_COOLDOWN:
                    continue
 
                await session.handle_audio(msg.data)
 
            elif msg.type == aiohttp.WSMsgType.TEXT:
                runtime_status.device_heartbeat()
                payload = msg.data
                if payload != "keep-alive":
                    log.info(f"📝 {payload}")
                if "[BARGE]" in payload:
                    await session._handle_barge_in()
                if "[PLAYBACK_DONE]" in payload:
                    session.playback_done_event.set()
                    
            elif msg.type == aiohttp.WSMsgType.ERROR:
                log.warn(f"WS error: {ws.exception()}")
                runtime_status.update(last_error=f"ws_error:{ws.exception()}")
    except Exception as e:
        log.error(f"RECEIVE LOOP EXCEPTION: {e}\n{traceback.format_exc()}")
        runtime_status.update(last_error=f"receive_loop_fatal:{e}")

    await session.close()
    clear_active_session(session)
    # A connection that was replaced by a newer one must not mark the
    # device offline under the connection that replaced it.
    if release_device(hello["device_id"], ws):
        runtime_status.device_disconnect()
    handler_tasks = [timeout_task, decay_task, ka_task, boot_task, vector_warm_task]
    for task in handler_tasks:
        if task and not task.done():
            task.cancel()
    await asyncio.gather(*handler_tasks, return_exceptions=True)
    await save_memory(memory)
    log.info(f"Disconnected: {request.remote}")
    return ws
