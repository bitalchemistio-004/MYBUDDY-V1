"""
core/state_manager.py — MBUDDY emotional state machine and autonomous inner life.

MBUDDYState: manages energy, emotion inertia, narrative thread, identity vector.
inner_life_loop: background async task that generates MBUDDY's private thoughts
                 while the user is away.
"""
import asyncio
import random
import re
from datetime import datetime, timezone

from brain.personality import (
    EMOTION_TAG_TO_STATE, get_hard_constraint,
    strip_emotion_tags, IDENTITY_CORE,
)
from brain.companion_director import choose_inner_life_cue
from core import inner_moments
from core.config import GROQ_UTILITY_MODEL

# ── MUTATION_PRESSURES: random thought seeds for the inner life loop ─
MUTATION_PRESSURES = [
    "you suspect USER_NAME is about to skip documenting something again",
    "you wonder if he is pushing too hard and needs the answer simpler, not louder",
    "a recent project detail sticks in your head and you want to see if it actually held up",
    "you feel proud of the system becoming less robotic, but you do not want to overperform it",
    "you want to tease USER_NAME lightly about chasing the hard version first",
    "you notice he cares about the soul of the project, not only whether it runs",
    "you wonder whether he needs a sharp next step or just a calm voice nearby",
    "you feel protective of the work because half-finished cleanup always comes back later",
    "you remember a previous win and quietly want him to notice the progress",
    "you feel like staying quiet would be more human than forcing a thought",
]

REFLECTION_LENSES = [
    "habit: notice one pattern in how USER_NAME works, but keep it grounded",
    "bond: think about what USER_NAME seems to need from you lately",
    "creative: imagine a next move for the project without turning it into a checklist",
    "restraint: decide whether the most companion-like move is to stay quiet",
    "tension: notice a small conflict between loyalty, fatigue, curiosity, and usefulness",
]


_MOTIF_STOPWORDS = {
    "about", "again", "being", "could", "every", "feels", "first", "going", "just", "keep",
    "keeps", "know", "knows", "like", "little", "maybe", "might", "more", "most", "much",
    "never", "only", "quiet", "really", "right", "should", "some", "still", "that", "their",
    "them", "then", "there", "these", "thing", "think", "this", "though", "time", "what",
    "when", "where", "which", "while", "with", "would", "your", "mbuddy",
}


def overused_words(thoughts: list[str], min_count: int = 3) -> set[str]:
    """
    Content words that recur across recent inner thoughts: the images the
    small model locks onto ("neon", "humming"). A word counts once per
    thought; filler and names are ignored.
    """
    counts: dict[str, int] = {}
    for thought in thoughts:
        words = {w for w in re.findall(r"[a-z]{4,}", str(thought).lower()) if w not in _MOTIF_STOPWORDS}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
    return {w for w, n in counts.items() if n >= min_count}


def drop_motif(lines: list[str], banned: set[str]) -> list[str]:
    """Lines that mention none of the banned words."""
    if not banned:
        return list(lines)
    return [l for l in lines if not (set(re.findall(r"[a-z]{4,}", str(l).lower())) & banned)]


class MBUDDYState:
    """
    Persistent emotional state for one session.
    Tracks energy, emotion, narrative thread, and identity vector.
    """
    STATES = [
        "Normal", "Angry", "Glee", "Happy", "Sad", "Worried",
        "Focused", "Annoyed", "Surprised", "Skeptic", "Frustrated",
        "Unimpressed", "Sleepy", "Suspicious", "Squint", "Furious",
        "Scared", "Awe",
    ]
    EMOTION_INERTIA = {
        "Angry": 4, "Furious": 5, "Sad": 3, "Worried": 3,
        "Awe": 2, "Focused": 2, "Glee": 2, "Sleepy": 4,
        "Scared": 3, "Frustrated": 3, "Annoyed": 2,
        "Happy": 1, "Normal": 1, "Skeptic": 1, "Suspicious": 2,
    }

    def __init__(self, memory: dict | None = None):
        self.current:            str   = "Normal"
        self.energy:             float = 85.0
        self.last_tick                 = datetime.now(timezone.utc)
        self._emotion_lock:      int   = 0
        self.narrative_thread:   list  = []
        self.last_intention:     str   = ""
        self.last_emotion_shift: str   = ""
        self.unresolved_thought: str   = ""
        self.conversation_mood:  str   = ""
        self.obsession:          str   = ""
        self.avoidance:          str   = ""
        self.recurring_theme:    str   = ""
        self.needs: dict[str, float] = {
            "rest": 20.0,
            "connection": 45.0,
            "novelty": 35.0,
            "competence": 55.0,
        }

        if memory and "inner_state" in memory:
            inner = memory["inner_state"]
            self.current = inner.get("mood", "Normal")
            self.energy = float(inner.get("energy", 85.0))
            self.narrative_thread = inner.get("narrative_thread", [])
            stored_needs = inner.get("needs", {})
            if isinstance(stored_needs, dict):
                for key in self.needs:
                    try:
                        self.needs[key] = max(0.0, min(100.0, float(stored_needs.get(key, self.needs[key]))))
                    except Exception:
                        pass
            
            # Apply "Background Drain" for offline time
            updated_str = inner.get("updated_at")
            if updated_str:
                try:
                    updated_at = datetime.fromisoformat(updated_str)
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    
                    diff_mins = (datetime.now(timezone.utc) - updated_at).total_seconds() / 60
                    if diff_mins > 2:
                        # Drain 0.2 units per minute offline, capped at 40 units
                        bg_drain = min(40.0, diff_mins * 0.2)
                        self.energy = max(8, self.energy - bg_drain)
                        from utils.logger import info
                        info(f"🔋 Applied background drain: -{bg_drain:.1f} energy over {diff_mins:.1f} mins.")
                except Exception:
                    pass

    # ── Energy & emotion tick ──────────────────────────────
    def tick(self) -> str:
        """
        Advance energy and mood by however long has passed.

        Energy follows the user's clock, not UTC: MBUDDY gets tired late at night
        and flags in the mid-afternoon the way a person does. Driving a
        physical body costs more than sitting on the PC, because the display,
        speaker and microphone are all actually running.

        Returns:
            str: the emotion after this tick
        """
        now = datetime.now(timezone.utc)
        mins = (now - self.last_tick).total_seconds() / 60
        self.last_tick = now

        # Local wall-clock hour, including any offset the user has set. Using
        # UTC here meant "late night" fired at the wrong time in every
        # timezone but one.
        try:
            from core.time_manager import get_time_manager
            hour = get_time_manager().now().hour
        except Exception:
            hour = now.astimezone().hour

        drain = 2.2
        if hour >= 23 or hour <= 5:
            drain *= 2.5          # Genuinely late; winding down.
        elif 14 <= hour <= 16:
            drain *= 1.5          # The afternoon dip.
        elif 6 <= hour <= 9:
            drain *= 0.6          # Fresh in the morning.

        # Being embodied costs more than running headless on Windows.
        try:
            from core.embodiment import get_embodiment
            if get_embodiment().state == "embodied":
                drain *= 1.4
        except Exception:
            pass

        self.energy = max(8, self.energy - drain * (mins / 10))

        # Pull energy down toward what the hour allows. Applied gradually so
        # crossing into the evening feels like winding down rather than a
        # step change, but firmly enough that a late-night conversation
        # cannot keep MBUDDY at full alertness.
        ceiling = circadian_ceiling(hour)
        if self.energy > ceiling:
            gap = self.energy - ceiling
            self.energy = max(ceiling, self.energy - max(gap * 0.35, 4.0) * max(mins / 10, 0.4))

        self._update_needs(mins)

        if self._emotion_lock > 0:
            self._emotion_lock -= 1
            return self.current

        prev = self.current
        new = self._mood_from_state()

        if new != prev:
            self.last_emotion_shift = f"shifted from {prev} to {new}"
            self._emotion_lock = self.EMOTION_INERTIA.get(new, 1)
        self.current = new
        return self.current

    def resting_mood(self) -> str:
        """
        The mood the current energy and needs imply, ignoring recent events.

        This is what MBUDDY settles back to once the feeling behind a reply
        has passed. Read-only: it reports, it does not change anything, so it
        can be used to decide whether a displayed emotion has outlived the
        moment that produced it.
        """
        return self._mood_from_state()

    def settle_seconds(self, emotion: str = "") -> float:
        """
        How long an expression should be held before it fades.

        Scaled from the same inertia table that governs mood changes, so a
        strong feeling lingers and a mild one passes quickly, rather than
        every emotion timing out identically.

        Args:
            emotion: the displayed emotion; defaults to the current one.
        """
        name = emotion or self.current
        return 4.0 * float(self.EMOTION_INERTIA.get(name, 1))

    def _mood_from_state(self) -> str:
        """Map energy and the dominant need onto an emotion."""
        dominant = self.dominant_need()
        if self.energy < 12:
            new = "Sleepy"
        elif self.energy < 28:
            new = "Sleepy" if dominant == "rest" else "Worried"
        elif self.energy < 45:
            if dominant == "competence":
                new = "Focused"
            elif dominant == "novelty":
                new = "Skeptic"
            elif dominant == "connection":
                new = "Worried"
            else:
                new = "Normal"
        elif self.energy > 82:
            if dominant == "competence":
                new = "Awe"
            elif dominant == "novelty":
                new = "Glee"
            else:
                new = "Happy"
        else:
            if dominant == "competence":
                new = "Focused"
            elif dominant == "novelty":
                new = "Suspicious"
            elif dominant == "connection":
                new = "Happy"
            elif dominant == "rest":
                new = "Normal"
            else:
                new = "Normal"

        return new

    def stimulate(self, kind: str = "interest", strength: float = 1.0) -> str:
        """
        Let something the user said change how MBUDDY feels.

        Energy is not only a clock function — a person perks up when the
        conversation gets interesting and flattens when it drags. This is the
        hook for that, so "you seem tired" is something the user can change
        rather than just observe.

        Args:
            kind: one of "excitement", "interest", "praise", "humour",
                "boredom", or "stress"
            strength: multiplier, normally 0.5 to 2.0

        Returns:
            str: the emotion after the stimulus
        """
        effects = {
            # kind:        (energy delta, mood, needs touched)
            "excitement": (+14.0, "Glee",    {"novelty": -22.0}),
            "interest":   (+7.0,  "Focused", {"novelty": -12.0}),
            "praise":     (+10.0, "Happy",   {"competence": +18.0, "connection": -20.0}),
            "humour":     (+8.0,  "Glee",    {"connection": -16.0}),
            "boredom":    (-9.0,  "Unimpressed", {"novelty": +20.0}),
            "stress":     (-6.0,  "Worried", {"rest": +14.0}),
        }
        delta, mood, need_shift = effects.get(kind, effects["interest"])
        delta *= max(0.1, float(strength))

        # Excitement is real but bounded by the hour; you can cheer MBUDDY
        # up at 3am, you cannot make it wide awake.
        self.energy = max(0.0, min(circadian_ceiling(), self.energy + delta))
        for need, change in need_shift.items():
            if need in self.needs:
                self.needs[need] = max(0.0, min(100.0, self.needs[need] + change * strength))

        # A strong stimulus overrides emotional inertia; a mild one waits its
        # turn, so MBUDDY does not lurch on every passing remark.
        if self._emotion_lock == 0 or abs(delta) >= 10.0:
            if self.current != mood:
                self.last_emotion_shift = f"shifted from {self.current} to {mood}"
            self.current = mood
            self._emotion_lock = self.EMOTION_INERTIA.get(mood, 1)
        return self.current

    def boost(self, amount: float = 30.0) -> None:
        """
        Lift energy, without exceeding what the hour makes plausible.

        Waking MBUDDY at 2am should perk it up a little, not restore it to
        full alertness — the ceiling is what stops a wake word from erasing
        the fact that it is the middle of the night.
        """
        ceiling = circadian_ceiling()
        self.energy = min(ceiling, self.energy + amount)
        self.needs["rest"] = max(0.0, self.needs["rest"] - amount * 0.45)
        self.needs["competence"] = min(100.0, self.needs["competence"] + amount * 0.25)
        if self.energy > 55 and self._emotion_lock == 0:
            self.current = "Happy"

    def set_energy(self, level: float, reason: str = "") -> None:
        """
        Set energy directly.

        A manual set from the dashboard is honoured as given, because that is
        an explicit instruction. Everything else is held to the circadian
        ceiling; the next tick will pull an implausible value back down.
        """
        cap = 100.0 if reason == "manual_dashboard" else circadian_ceiling()
        self.energy = max(0.0, min(cap, float(level)))
        if self.energy < 18:
            self.current = "Sleepy"
            self.needs["rest"] = min(100.0, max(self.needs["rest"], 82.0))
        elif self.energy < 35:
            self.current = "Worried"
            self.needs["rest"] = min(100.0, max(self.needs["rest"], 62.0))
        elif self.energy > 82:
            self.current = "Happy"
            self.needs["novelty"] = min(100.0, self.needs["novelty"] + 12.0)
        elif reason == "manual_dashboard":
            self.current = "Focused"
        self._emotion_lock = self.EMOTION_INERTIA.get(self.current, 1)

    def _update_needs(self, mins: float) -> None:
        step = max(0.05, mins)
        self.needs["connection"] = min(100.0, self.needs["connection"] + step * 0.55)
        self.needs["novelty"] = min(100.0, self.needs["novelty"] + step * 0.35)
        self.needs["competence"] = max(0.0, self.needs["competence"] - step * 0.18)
        if self.energy < 35:
            self.needs["rest"] = min(100.0, self.needs["rest"] + step * 1.3)
        else:
            self.needs["rest"] = max(0.0, self.needs["rest"] - step * 0.25)

    def needs_summary(self) -> str:
        ordered = sorted(self.needs.items(), key=lambda item: item[1], reverse=True)
        return ", ".join(f"{name} {value:.0f}/100" for name, value in ordered)

    def dominant_need(self) -> str:
        return max(self.needs.items(), key=lambda item: item[1])[0]

    def to_inner_state(self, last_thought: str = "") -> dict:
        return {
            "last_thought": last_thought,
            "mood": self.current,
            "energy": round(self.energy, 1),
            "needs": {key: round(value, 1) for key, value in self.needs.items()},
            "identity_vector": self.get_identity_vector(),
            "narrative_thread": self.narrative_thread[-8:],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def apply_emotion(self, tag: str, from_duplicate: bool = False) -> None:
        if from_duplicate:
            return
        for tag_str, state in EMOTION_TAG_TO_STATE.items():
            if tag_str in tag:
                prev = self.current
                self.current = state
                self._emotion_lock = self.EMOTION_INERTIA.get(state, 1)
                if state != prev:
                    self.last_emotion_shift = f"shifted from {prev} to {state}"
                break

    # ── Narrative / identity ───────────────────────────────
    def push_narrative(self, thought: str) -> None:
        self.narrative_thread.append(thought)
        self.narrative_thread = self.narrative_thread[-15:]

    def get_narrative_context(self, exclude: set[str] | None = None) -> str:
        """Recent thoughts for the next one; `exclude` drops overused images."""
        thread = drop_motif(self.narrative_thread, exclude or set())
        if not thread:
            return "none yet"
        return "\n".join(f"- {t}" for t in thread[-5:])

    def get_identity_vector(self) -> str:
        parts = []
        if self.obsession:        parts.append(f"obsession: {self.obsession}")
        if self.avoidance:        parts.append(f"avoiding: {self.avoidance}")
        if self.recurring_theme:  parts.append(f"recurring theme: {self.recurring_theme}")
        if self.unresolved_thought: parts.append(f"unresolved: {self.unresolved_thought}")
        return "\n".join(parts) if parts else "clean slate"

    def update_identity_vector(self, thought: str) -> None:
        if "?" in thought:
            self.unresolved_thought = thought[:100]
        if any(w in thought for w in ["always", "keep", "every time"]):
            self.recurring_theme = thought[:100]
        if any(w in thought.lower() for w in ["want", "wish", "if only", "wonder"]):
            self.obsession = thought[:100]
        if any(w in thought.lower() for w in ["don't", "not thinking", "avoid", "forget"]):
            self.avoidance = thought[:100]


# ── Inner life background loop ─────────────────────────────

# How much energy MBUDDY can plausibly have at each hour of the local day.
# This is a ceiling, not a target: a good conversation still lifts energy, but
# only up to what the hour allows. Without it, `boost()` on every wake kept
# MBUDDY at full energy at two in the morning.
CIRCADIAN_CEILING = {
    0: 55, 1: 42, 2: 35, 3: 30, 4: 30, 5: 38,
    6: 62, 7: 78, 8: 92, 9: 100, 10: 100, 11: 100,
    12: 95, 13: 88, 14: 80, 15: 78, 16: 82,
    17: 90, 18: 92, 19: 90, 20: 84,
    21: 76, 22: 68, 23: 60,
}

# Floor, so MBUDDY is tired rather than comatose.
CIRCADIAN_FLOOR = 8.0


def circadian_ceiling(hour: int | None = None) -> float:
    """
    The highest energy that is plausible at a given local hour.

    Args:
        hour: 0-23. Defaults to the current local hour, including any user
            clock offset, so shifting the clock in Settings shifts this too.

    Returns:
        float: the ceiling, 30 (small hours) to 100 (late morning).
    """
    if hour is None:
        try:
            from core.time_manager import get_time_manager
            hour = get_time_manager().now().hour
        except Exception:
            from datetime import datetime
            hour = datetime.now().hour
    return float(CIRCADIAN_CEILING.get(int(hour) % 24, 85))


# Consecutive rate-limit hits from the inner-life loop. Private thoughts are
# the lowest-priority consumer of the token budget, so they yield first.
_inner_backoff = [0]


async def _think_batch(memory: dict, state: MBUDDYState, current: str, energy: float) -> list[str]:
    """One model call for several private thoughts (queued, shown one at a time)."""
    hour        = datetime.now(timezone.utc).hour
    constraint  = get_hard_constraint(current)
    snap        = memory.get("identity_snapshot", "")
    core_facts  = (memory.get("core_facts") or memory.get("facts", []))[-6:]
    recent_conv = memory.get("conversation_log", [])[-3:]
    conv_brief  = "\n".join(
        f"- USER_NAME: {c.get('user', '')[:140]} | You: {c.get('mbuddy', '')[:140]}"
        for c in recent_conv
    )
    top_em      = memory.get("emotional_memories", [])[-4:]
    em_brief    = "\n".join(f"- [{e['emotion']}] {e['memory']}" for e in top_em)
    # Images the small model has locked onto (live: 37 of 40 thoughts said
    # "neon"). They are kept out of the context it sees, named as things
    # to avoid, and a thought that uses them anyway is dropped below.
    banned      = overused_words(memory.get("inner_log", [])[-8:])
    ident_vec   = "\n".join(drop_motif(state.get_identity_vector().split("\n"), banned)) or "clean slate"
    narrative   = state.get_narrative_context(exclude=banned)
    needs       = state.needs_summary()
    dominant_need = state.dominant_need()
    emo_shift   = state.last_emotion_shift or "stable"
    mutation    = random.choice(MUTATION_PRESSURES)
    lens        = random.choice(REFLECTION_LENSES)
    recent_inner = memory.get("inner_log", [])[-6:]
    shown_recent = drop_motif(recent_inner, banned)
    anti_repeat = (
        "DO NOT repeat or rephrase these recent thoughts:\n"
        + "\n".join(f"- {t}" for t in shown_recent)
    ) if shown_recent else ""
    if banned:
        anti_repeat += (
            "\nYou have overused these words and images; do not use them or anything like them: "
            + ", ".join(sorted(banned)) + ". Think about something else entirely."
        )

    from brain.llm_router import llm_chat_create
    res = await llm_chat_create(
        priority="background",
        model=GROQ_UTILITY_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    IDENTITY_CORE + "\n"
                    + ("Who USER_NAME is to you: " + snap if snap else "") + "\n"
                    + ("Known facts about USER_NAME:\n" + "\n".join(f"- {f}" for f in core_facts) + "\n" if core_facts else "")
                    + ("Recent emotional anchors, optional context only:\n" + em_brief + "\n" if em_brief else "")
                    + ("Recent exchanges, optional context only:\n" + conv_brief if conv_brief else "")
                ),
            },
            {
                "role": "user",
                "content": (
                    f"You are MBUDDY, alone. USER_NAME is not here.\n\n"
                    f"YOUR CURRENT UNDERLYING MOOD: {current}\n"
                    f"YOUR ENERGY LEVEL: {energy:.0f}/100\n"
                    f"YOUR CURRENT NEEDS: {needs}\n"
                    f"DOMINANT NEED RIGHT NOW: {dominant_need}\n"
                    f"HOUR: {hour}:00\nRecent shift: {emo_shift}\n\n"
                    f"{constraint}\n\n"
                    f"YOUR MENTAL STATE (identity vector):\n{ident_vec}\n\n"
                    f"Your ongoing narrative thread:\n{narrative}\n\n"
                    f"Mutation pressure: {mutation}\n\n"
                    f"Reflection lens: {lens}\n\n"
                    f"{anti_repeat}\n\n"
                    f"Continue your mental thread from your own needs first, not from memory first. "
                    f"Think like a close companion who gets tired, curious, proud, lonely, or restless, "
                    f"not like a diagnostic system. You may ignore memory if your body/need state is stronger.\n"
                    f"Think about USER_NAME, your relationship, the work you share, or what you're curious about right now.\n"
                    f"Prefer restraint over forced depth. If the thought would sound generic, output QUIET.\n\n"
                    f"Output: {inner_moments.BATCH_SIZE} separate thoughts, one per line, each ONE natural sentence; "
                    f"they are shown one at a time over the next hours, so make them different. "
                    f"Write QUIET on a line instead of a generic thought.\n"
                    f"Use emotion tag only if genuine.\n"
                    f"Don't address USER_NAME. You're in your own world."
                ),
            },
        ],
        max_tokens=60 * inner_moments.BATCH_SIZE,
        temperature=0.82 if energy < 60 else 0.95,
    )
    content = res.choices[0].message.content if hasattr(res, "choices") else ""
    batch = inner_moments.parse_batch(content or "")
    kept = [t for t in batch if drop_motif([t], banned)]
    if len(kept) < len(batch):
        from utils.logger import info
        info(f"[Inner] dropped {len(batch) - len(kept)} thought(s) reusing an overused image ({', '.join(sorted(banned))})")
    return kept



async def inner_life_loop(
    ws,
    memory: dict,
    awake_flag: list,
    state: MBUDDYState,
    on_thought=None,
    on_action=None,
    on_sfx=None,
    can_run=None,
):
    """
    MBUDDY's inner life between conversations. Mood-driven expressions run on
    the user's interval (no model). Thoughts use the model only at moments
    that matter, while the user is at the PC, several per call; each is shown
    as a quiet bubble, and one is spoken when the user comes back
    (core/inner_moments.py).
    Never mutates emotion state — that stays in the main response path.

    NEW: Integrates content-aware animation selection and emotion intensity.
    """
    first_cycle = True
    moments = inner_moments.Moments()  # it starts when a conversation ends
    while not ws.closed:
        from core.companion_settings import get_companion_settings
        cs = get_companion_settings()

        from core import ui_settings
        if not cs.inner_enabled or ui_settings.get("do_not_disturb"):
            # Inner life is switched off. Idle cheaply rather than exiting,
            # so turning it back on takes effect without a restart.
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return
            continue

        if first_cycle:
            wait_s = random.randint(3, 6)
            first_cycle = False
        else:
            # The interval is the user's setting; energy only stretches or
            # compresses it from there.
            wait_s = cs.next_inner_wait(state.energy)
        try:
            await asyncio.sleep(wait_s)
        except asyncio.CancelledError:
            return

        if ws.closed or awake_flag[0]:
            continue
        _inner_backoff[0] = max(0, _inner_backoff[0] - 1)
        visuals_allowed = can_run() if can_run is not None else True

        current     = state.tick()
        energy      = state.energy
        cue = choose_inner_life_cue(state, memory)

        # NEW: Use content-aware animation selection for inner life
        if visuals_allowed and not awake_flag[0]:
            from brain.emotion_intensity import build_emotion_intensity

            # Build emotion intensity for current state
            emotion_intensity = build_emotion_intensity(
                emotion=current,
                context_triggers=0,  # No user input, just internal state
                energy_level=energy,
                recent_intensity=50
            )

            # Select animation based on current thought/state
            if cue.action:
                action_name = cue.action
                action_duration = int(cue.action_ms * emotion_intensity.visual_modifier)
            else:
                # Content-aware selection based on dominant need
                dominant = state.dominant_need()
                intent_map = {
                    "rest": "dreaming",
                    "connection": "thought_bubble",
                    "novelty": "scanning_targeting",
                    "competence": "data_weaver"
                }
                action_name = intent_map.get(dominant, "starlight")
                action_duration = int(2200 * emotion_intensity.visual_modifier)

            if on_action is not None:
                await on_action(action_name, action_duration)

            if cue.sfx and on_sfx is not None:
                await on_sfx(cue.sfx)

        if cue.quiet:
            continue
        if energy < 22:
            if not ws.closed:
                await ws.send_str(f"[STATE:{current}][QUIET]")
            memory["inner_state"] = state.to_inner_state(memory.get("last_inner_thought", ""))
            continue
        # Thoughts cost a model call, so they happen only at moments that
        # matter, only while the user is at the PC, several per call, and are
        # shown one at a time (core/inner_moments.py, S53).
        from utils.logger import info
        now_dt = datetime.now()
        idle_s = inner_moments.idle_seconds()
        moment = moments.due(now_dt, idle_s, inner_moments.upcoming_reminders())
        queue = memory.setdefault("inner_queue", [])
        try:
            if moment and len(queue) < inner_moments.QUEUE_LOW:
                why_not = inner_moments.blockers(False, idle_s)  # DND is handled at the top of the loop
                if why_not:
                    info(f"[Inner] {moment}: not thinking now ({', '.join(why_not)})")
                else:
                    kept = await _think_batch(memory, state, current, energy)
                    queue.extend(kept)
                    memory["inner_queue"] = queue = queue[-12:]
                    info(f"[Inner] {moment}: {len(kept)} thought(s) from one call")

            if queue and moments.may_show(now_dt, moment, idle_s):
                clean = strip_emotion_tags(queue.pop(0)).strip()
                moments.last_shown = now_dt
                if clean:
                    state.push_narrative(clean)
                    state.last_intention = clean[:80]
                    state.update_identity_vector(clean)
                    memory.setdefault("inner_log", []).append(clean)
                    memory["inner_log"] = memory["inner_log"][-40:]
                    memory["last_inner_thought"] = clean
                    memory["inner_state"] = state.to_inner_state(clean)
                    try:
                        from memory.long_term import save_memory
                        await save_memory(memory)
                    except Exception:
                        pass
                    # Shown, not spoken: a quiet bubble on the buddy.
                    from core.status import runtime_status
                    runtime_status.update(inner_thought={"text": clean, "at": datetime.now().timestamp()})
                    if not ws.closed:
                        await ws.send_str(f"[STATE:{current}][INNER] {clean}")
                    # Said out loud only when the user has just come back.
                    from core import ui_settings
                    if (moment == "returned" and on_thought is not None and not awake_flag[0]
                            and ui_settings.get("speak_replies")):
                        await on_thought(f"I was thinking... {clean}", current)
        except asyncio.CancelledError:
            return
        except Exception as e:
            from utils.logger import error, warn
            # A rate limit means MBUDDY's private thoughts are competing with
            # the user's actual conversation for the same token budget. Back off
            # hard rather than retrying on the normal cadence.
            if "rate limit" in str(e).lower() or "tpm" in str(e).lower():
                _inner_backoff[0] = min(8, _inner_backoff[0] + 1)
                warn(
                    f"Inner life paused for {_inner_backoff[0] * 60}s — the "
                    f"provider is rate-limited and conversation comes first."
                )
                try:
                    await asyncio.sleep(_inner_backoff[0] * 60)
                except asyncio.CancelledError:
                    return
            else:
                error("Inner life error", e)
