"""
brain/llm_router.py — Universal MBUDDY request router.

Flow per user utterance:
  1. Determine if a web search is needed (with fallback across providers).
  2. Build the system prompt (memory, search context, mood).
  3. Stream the reply through the primary provider.
  4. If the primary provider fails (rate-limit, error), fallback to secondary.
  5. Update conversation history exactly once after the stream completes.

Deduplication / duplicate-request guard:
  - `_ACTIVE_REQUESTS` tracks in-flight user texts so the same utterance
    cannot trigger a concurrent second request to any provider.
  - Providers are resolved via `get_available_providers()` which only includes
    providers with valid API keys, preventing blind calls into Gemini when
    GEMINI_API_KEY is absent.
"""

import asyncio
import os
from core.time_manager import get_time_manager
import hashlib
import re
import time
from typing import AsyncIterator, List, Dict, Optional, Set

from core.config import MBUDDY_ENABLE_WEB_SEARCH
from .llm_bridge import llm_chat_create, llm_client  # noqa: F401  re-exported: memory/, core/ and api/ import them from here
from .brain_adapters import get_brain_adapter, get_available_providers
from .tools import execute_tool_async
from memory.vector_store import get_vector_store
from utils.logger import info, ok, warn, error as logerr

log = info, ok, warn, logerr

# ── Deduplication guard ───────────────────────────────────────
# Tracks request fingerprints that are currently being processed.
# Prevents double-fire on noisy VAD edges or reconnection races.
_ACTIVE_REQUESTS: Set[str] = set()

MAX_SYSTEM_PROMPT_CHARS = 12_000
MAX_HISTORY_CHARS = 1600    # the local model runs at 4096 tokens; replies are 1-3 sentences
MAX_HISTORY_MESSAGES = 6    # at most the last 6 messages...
_LAST_REPLY_PROMPT = [""]    # the last reply prompt, to see what changed (S6b)
HISTORY_STEP = 4            # ...but the window's start moves 4 at a time (S6b): a window that
                            # slid by one each turn made Ollama re-read the whole history


class _SearchNotNeeded(Exception):
    """Raised to skip the search step when a tool already answered."""


def _memory_key(memory_text: str) -> str:
    return hashlib.sha1(memory_text.strip().lower().encode("utf-8")).hexdigest()[:12]


def _request_key(user_text: str, provider: str) -> str:
    normalized = " ".join(user_text.lower().split())
    return f"{provider}:{normalized[:160]}"


def _clip_text(text: str, limit: int) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _compact_history_for_llm(history: list) -> list[dict[str, str]]:
    """Keep recent dialogue useful while preventing provider token blowups."""
    compact: list[dict[str, str]] = []
    remaining = MAX_HISTORY_CHARS
    # The start only moves in steps, so the same messages open the history for
    # two turns running and stay in Ollama's cache.
    extra = len(history) - MAX_HISTORY_MESSAGES
    start = 0 if extra <= 0 else ((extra - 1) // HISTORY_STEP + 1) * HISTORY_STEP
    for msg in reversed(history[start:]):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        if "my brain hit a wall" in content.lower() or "all providers unavailable" in content.lower():
            continue
        content = _clip_text(content, 650)
        cost = len(content) + len(role) + 16
        if cost > remaining and compact:
            break
        compact.append({"role": role, "content": content})
        remaining -= min(cost, remaining)
    compact.reverse()
    return compact


def _friendly_provider_fallback(exc: Exception, detected_lang: str) -> str:
    """Return a spoken-safe fallback in strict English without leaking backend exception names."""
    err = str(exc).lower()
    if "request too large" in err or "413" in err or "tokens per minute" in err or "tpm" in err:
        return "[WORRIED] My brain context was too heavy for the provider, so I am trimming it. Please repeat your sentence."
    if "rate" in err or "429" in err:
        return "[WORRIED] My brain provider is rate-limited right now. Give me a moment and try again."
    if "connect" in err or "connection" in err or "network" in err:
        return "[WORRIED] I can hear you, but my brain provider connection dropped. Re-establishing link..."
    return "[WORRIED] My brain provider experienced a temporary hiccup, but I am online and ready."


LIVE_SEARCH_PATTERNS = (
    r"\b(search|look up|google|internet|online|web)\b",
    r"\b(latest|current|recent|today|now|right now|this week|this month|202[4-9])\b",
    r"\b(weather|forecast|news|price|stock|score|scores|result|results|schedule|standings|exchange rate)\b",
    r"\b(start|starts|starting|begin|begins|beginning|kick off|kicks off|opening date|release date|launch date)\b",
    r"\b(final|finals|fixture|lineup|who vs who|who versus who|versus who|vs who)\b",
    r"\b(world cup|champions league|premier league|nba|nfl|mlb|fifa|uefa|olympics)\b",
    r"\b(who (is|are)|who's) (the )?(current )?(president|prime minister|king|queen|ceo|minister|mayor|governor|coach|captain|winner|champion)\b",
    r"\b(what|when|where) (is|are|'s) (the )?(current|latest|next|upcoming|final|match|game|event|result|score|schedule)\b",
    r"\b(huawei|apple|nvidia|openai|chatgpt|google|microsoft|breakthrough|chip|semiconductor|ai model|fusion|quantum|stock market|btc|bitcoin|crypto)\b",
)

# Queries that must NEVER trigger a web search — answer from memory and personality.
NEVER_SEARCH_PATTERNS = (
    # Memory / self-reference
    r"\b(do you remember|remember when|you remember|you recall|we talked about|we discussed|you told me|i told you|last time we|did i tell|didn't i tell)\b",
    r"\b(your memory|your thoughts|your feelings|how do you feel|what do you think|your opinion|what's your take|you like|do you like|do you prefer|would you)\b",
    r"\b(your inner life|your mind|who are you|what are you|your personality|are you okay|how are you doing|how's it going|how are you feeling)\b",
    # Social / everyday / subjective
    r"\b(should i eat|should i buy|should i go|should i do|what should i|give me advice|any advice|what do you suggest|what would you do)\b",
    r"\b(i'm feeling|i feel|i am feeling|i had|i did|i went|i saw|i watched|i ate|i think|i want|i need|i like|i love|i hate)\b",
    # Time/date (answered from system clock)
    r"\bwhat (date|day|time) is it\b",
    r"\b(today'?s date|current date|current time)\b",
)

SPORTS_CONTEXT_TERMS = (
    ("champions league final", "UEFA Champions League final"),
    ("champions league", "UEFA Champions League"),
    ("uefa", "UEFA"),
    ("world cup", "World Cup"),
    ("premier league", "Premier League"),
    ("nba", "NBA"),
    ("nfl", "NFL"),
    ("mlb", "MLB"),
    ("final", "final"),
    ("match", "match"),
    ("game", "game"),
)

AMBIGUOUS_SEARCH_FOLLOWUP = re.compile(
    r"\b(why|how come).*\b(search|look up|google|check|browse)\b.*\b(that|it|this)\b"
    r"|\b(search|look up|google|check|browse)\b.*\b(that|it|this)\b"
)


def needs_live_search(user_text: str) -> bool:
    """Return True ONLY when a query clearly needs fresh external facts.
    
    Philosophy: search is opt-in, not default. MBUDDY must answer from personality and
    memory unless the query clearly needs real-time external data.
    """
    text = user_text.lower()
    # Hard block — never search for these
    if any(re.search(p, text) for p in NEVER_SEARCH_PATTERNS):
        return False
    # Only search if it positively matches a live-data pattern
    return any(re.search(p, text) for p in LIVE_SEARCH_PATTERNS)


def search_query_for_user_text(user_text: str) -> str:
    """Extract a clean search query from user speech, stripping STT filler."""
    # Strip common spoken filler that contaminates search quality
    filler_patterns = (
        r"^(wait|anyways|anyway|okay|ok|so|hey|uh|um|like|you know|i mean|you see)[,\s]+",
        r"^(i want to ask about|i wanted to ask|tell me about|i heard that|did you hear|can you check|can you look up)[,\s]+",
        r"^(bro|man|dude|habibi|yalla)[,\s]+",
    )
    cleaned = user_text.strip()
    for pat in filler_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()

    text = cleaned.lower()
    today = get_time_manager().naive_now().strftime("%Y-%m-%d")
    if "world cup" in text and re.search(r"\b(start|starts|starting|begin|begins|beginning|kick off|kicks off|opening|schedule)\b", text):
        return f"FIFA World Cup start date opening match schedule {today}"
    if re.search(
        r"\b(latest|current|recent|today|now|right now|this week|this month|weather|forecast|score|scores|result|results|schedule|standings|price|stock|final|finals|start|starts|starting|begin|begins|beginning|kick off|opening|release date|launch date)\b",
        text,
    ):
        return f"{cleaned} {today}"
    return cleaned


def _recent_text(history: list, turns: int = 8) -> str:
    recent = []
    for msg in history[-turns:]:
        if isinstance(msg, dict):
            recent.append(str(msg.get("content", "")))
    return " ".join(recent)


def _previous_user_topic(history: list) -> str | None:
    for msg in reversed(history):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = str(msg.get("content", "")).strip()
            if content:
                return content
    return None


def _standalone_context_query(user_text: str, history: list) -> str | None:
    text = user_text.lower()
    if not AMBIGUOUS_SEARCH_FOLLOWUP.search(text):
        return None
    previous = _previous_user_topic(history)
    if previous:
        return search_query_for_user_text(previous)
    return None


def _contextual_sports_query(user_text: str, history: list) -> str | None:
    text = user_text.lower()
    if not re.search(r"\b(final|finals|fixture|match|game|score|result|results|winner|who vs who|who versus who|versus who|vs who)\b", text):
        return None

    recent_lower = _recent_text(history).lower()
    matched_terms: list[str] = []
    for needle, label in SPORTS_CONTEXT_TERMS:
        if needle in recent_lower or needle in text:
            if any(label.lower() in existing.lower() for existing in matched_terms):
                continue
            matched_terms = [existing for existing in matched_terms if existing.lower() not in label.lower()]
            matched_terms.append(label)

    if not matched_terms:
        return None

    seed = " ".join(dict.fromkeys(matched_terms))
    if re.search(r"\b(result|results|winner|won|score|finals?)\b", text):
        return f"{seed} result {get_time_manager().naive_now().strftime('%Y-%m-%d')}"
    return f"{seed} {user_text} {get_time_manager().naive_now().strftime('%Y-%m-%d')}"


def live_search_query(user_text: str, history: list | None = None) -> str | None:
    """Return a standalone search query, or None for stable model knowledge."""
    history = history or []
    contextual_followup = _standalone_context_query(user_text, history)
    if contextual_followup:
        return contextual_followup

    if not needs_live_search(user_text):
        return None
    contextual = _contextual_sports_query(user_text, history)
    if contextual:
        return contextual
    return search_query_for_user_text(user_text)


# ─────────────────────────────────────────────────────────────
# Search reasoning (with cross-provider fallback)
# ─────────────────────────────────────────────────────────────

async def determine_search_query(user_text: str, history: List[Dict]) -> Optional[str]:
    """
    Return a search query when concrete external data is needed, else None.

    Fully pattern-driven — no secondary LLM call:
      1. NEVER_SEARCH_PATTERNS hard-block memory/social/introspective queries.
      2. LIVE_SEARCH_PATTERNS positively identify queries needing external data.

    The old LLM-fallback branch for "ambiguous" queries was removed because:
      a) Pattern routing already handles the relevant cases correctly.
      b) The extra adapter.stream_chat() call was contributing to Groq rate-limit errors.
    """
    if not MBUDDY_ENABLE_WEB_SEARCH:
        return None

    return live_search_query(user_text, history)



# ─────────────────────────────────────────────────────────────
# Main conversation stream (with cross-provider fallback)
# ─────────────────────────────────────────────────────────────

_VERIFY = re.compile(
    r"\b(are you sure|did you (really |actually )?(do|change|set|mute|lower|turn|fix|add|save|check)"
    r"|did it work|is it (still|really|muted|on|off|done|working)|is the \w+ still"
    r"|still (high|loud|low|quiet|muted|on|running|there)|check (it |that |this )?again|can you check"
    r"|what did you (just )?do|what'?s the (volume|level)|how loud is)\b",
    re.IGNORECASE,
)


def is_verification_question(user_text: str) -> bool:
    """
    Questions about whether something is so or was done. These must be
    answered from a fresh check of the real state, never from earlier
    replies (live: "Are you sure it's still high?" -> "I just checked. It's
    not." with nothing checked).
    """
    return bool(_VERIFY.search((user_text or "").replace("’", "'")))


def check_real_state(user_text: str) -> str:
    """Read the state a verification question is about; a prompt block."""
    lines = []
    t = user_text.lower()
    try:
        if re.search(r"\b(volume|audio|sound|loud|quiet|mute|hear|high|low)\b", t) or not re.search(r"\b(timer|remind|task)\b", t):
            from brain.tools import execute_volume_tool
            lines.append(execute_volume_tool("what's the volume"))
            from brain.agent import other_apps_muted
            lines.append("Other apps are muted." if other_apps_muted() else "No other apps are muted.")
        if re.search(r"\btimer", t):
            from brain.tools import _timer_manager
            timers = _timer_manager.list_timers()
            lines.append("Running timers: " + (", ".join(f"{x.label} ({int(x.remaining_seconds())}s left)" for x in timers) or "none") + ".")
        if re.search(r"\b(remind|task|list)", t):
            from brain.task_tracker import format_task_list, task_tracker
            lines.append(format_task_list(task_tracker.get_pending_tasks()))
    except Exception as exc:
        lines.append(f"(check failed: {type(exc).__name__})")
    return "CHECKED JUST NOW (the real state; answer from this, not from earlier replies):\n" + "\n".join(f"- {l}" for l in lines)


_OWN_CHECK_OPENER = re.compile(
    r"^\s*(\[[A-Z]+\]\s*)?((okay|ok|alright|sure),?\s*)?((let me|lemme|i'?ll) check( again| that| it)?"
    r"|let'?s see)\s*[.…,!—–-]*\s*",
    re.IGNORECASE,
)


def strip_own_check_opener(first_sentence: str) -> str:
    """
    MBUDDY has already said "Let me check."; drop the model's own tag and a
    second "Let me check…" so the reply doesn't say it twice.
    """
    text = re.sub(r"^\s*\[[A-Z]+\]\s*", "", first_sentence)
    rest = _OWN_CHECK_OPENER.sub("", text.replace("’", "'"), count=1)
    if rest == text.replace("’", "'"):
        return text
    return rest[:1].upper() + rest[1:]


_READ_TOOLS = {"get_time", "get_weather", "web_search", "list_tasks", "pc_status",
               "clock", "weather", "news", "calculator"}


def acted(results, keyword_tool: str = "") -> bool:
    """Whether a tool that DOES something succeeded this turn. A search or a
    clock read backs no "I'll play it" (live: a web search, then "I'll start it
    right now", S91)."""
    from brain import pc_actions
    from brain.tools import ToolFailure
    if keyword_tool and keyword_tool not in _READ_TOOLS:
        return True
    for name, _a, result in results or []:
        ability = pc_actions.REGISTRY.get(name)
        is_read = name in _READ_TOOLS or (ability is not None and ability.risk == "read")
        if not is_read and not isinstance(result, ToolFailure) and not str(result).startswith("NOT "):
            return True
    return False


def failures_only(results) -> bool:
    """Every tool this turn failed (that case is answered on its own, in code)."""
    from brain.tools import ToolFailure
    return bool(results) and all(isinstance(r, ToolFailure) for _n, _a, r in results)


def _spoken_line_for(name: str) -> bool:
    """True for PC actions, whose results code says itself."""
    from brain import pc_actions
    ability = pc_actions.REGISTRY.get(name)
    return bool(ability) and ability.risk != "read"


def providers_include_ollama() -> bool:
    """The agent step uses Ollama's native tool calling, so it needs Ollama."""
    return "ollama" in get_available_providers()


_UNCLEAR_NOTE = (
    "(Speech recognition was unsure about these words, so one or two may be "
    "misheard. Answer what USER_NAME most likely meant, using the conversation "
    "for context. If you really cannot tell, ask one short, specific question "
    "about the unclear word. Never mention speech recognition or audio.)"
)


def user_content_for_turn(user_text: str, detected_lang: str, heard_unclearly: bool = False) -> str:
    """The user message the model sees this turn; history keeps the plain text."""
    from stt.language_policy import user_message_for_lang

    content = user_message_for_lang(user_text, detected_lang)
    return f"{content}\n\n{_UNCLEAR_NOTE}" if heard_unclearly else content


_HEAD_CHARS = 24  # enough to see "[TAG]" or "Unimpressed." at the start


def _leading_tag_fix(head: str) -> str:
    """Give a reply's opening a proper [TAG]; see ensure_leading_tag."""
    from .personality import STATE_TO_TAG

    stripped = head.lstrip()
    if not stripped:
        return head
    if stripped.startswith("["):
        # The model also invents tags ("[WOORRIED]", "[HOPEFUL]"); map a
        # typo to the tag it meant, anything else to [NORMAL].
        m = re.match(r"\[([A-Za-z]+)\]", stripped)
        valid = set(STATE_TO_TAG.values()) | {"[RELIEVED]"}
        if m and f"[{m.group(1).upper()}]" not in valid:
            import difflib
            close = difflib.get_close_matches(f"[{m.group(1).upper()}]", sorted(valid), n=1, cutoff=0.8)
            return (close[0] if close else "[NORMAL]") + stripped[m.end():]
        return head
    # "Focused. ..." or the word alone on the first line ("HAPPY  \nThat's ...", live 2026-09-27).
    word = re.match(r"([A-Za-z]+)(?:[.:!,]|[ \t]*\r?\n)\s*", stripped)
    if word:
        for state, tag in STATE_TO_TAG.items():
            if word.group(1).lower() == state.lower():
                return f"{tag} {stripped[word.end():]}"
    return f"[NORMAL] {stripped}"


async def ensure_leading_tag(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """
    Make sure a streamed reply starts with an emotion tag.

    The local model (qwen3:4b-instruct) often writes the emotion as a plain
    word ("Focused. Still running...") instead of "[FOCUSED] ...". That word
    was then spoken aloud, and the missing tag made every reply fail the
    language check. Only the first few characters are held back, so the
    reply still streams.
    """
    head = ""
    passthrough = False
    async for delta in stream:
        if passthrough:
            yield delta
            continue
        head += delta
        opened = head.lstrip().startswith("[")
        # Wait for the "]": the tag arrives in pieces ("[WOO" + "RRIED]").
        if (opened and "]" in head) or (not opened and len(head) >= _HEAD_CHARS) or len(head) >= 2 * _HEAD_CHARS:
            yield _leading_tag_fix(head)
            passthrough = True
    if not passthrough and head:
        yield _leading_tag_fix(head)


# A sentence ends at . ! or ? followed by a space. Dots in an ellipsis
# ("Then... nothing") are not ends; "…" is a different character anyway.
_SENTENCE_END = re.compile(r"(?<!\.)[.!?](?!\.)(?=\s)")


async def limit_sentences(stream: AsyncIterator[str], max_sentences: int) -> AsyncIterator[str]:
    """
    Stop a streamed reply after `max_sentences` sentences.

    The local model ignores "1-2 sentences" and runs on (a live reminder
    reply went ten sentences and drifted into "I'll start the hum. Low. Like
    a fridge."). The prompt still asks for brevity; this enforces it.
    """
    text, emitted = "", 0
    async for delta in stream:
        text += delta
        # A short fragment ("Oof.", "Long day, huh?") costs half a sentence:
        # it can't crowd out the sentence that says something, and a chain
        # of fragments ("Low. Like a fridge.") still ends.
        cost, start = 0.0, 0
        for m in _SENTENCE_END.finditer(text):
            words = re.sub(r"\[[A-Z]+\]", "", text[start:m.end()]).split()
            start = m.end()
            cost += 1.0 if len(words) > 3 else 0.5
            if cost >= max_sentences:
                yield text[emitted:m.end()]
                return
        yield text[emitted:]
        emitted = len(text)


# ── Truth guard ──────────────────────────────────────────────
# Prompt rules alone did not stop the small model from claiming actions it
# never took ("I recalibrated. Now it's at 50%.", "I pulled the trigger.") or
# inventing the user's habits ("You skip breakfast. You leave the kettle on.")
# with empty memory. These sentences are removed before they are spoken.
_ACTION_VERBS = (
    r"set|cut|lowered|raised|turned|muted|unmuted|changed|adjusted|recalibrated|saved|added|fixed|"
    r"dropped|reduced|increased|started|scheduled|pulled|handled|"
    r"closed|minimi[sz]ed|opened|focused|brought|hid"
)
_ACTION_CLAIM = re.compile(
    r"\bi('ve| have)?\s+(just\s+|already\s+)?(" + _ACTION_VERBS + r"|did it)\b"
    r"|\bvolume (cut|lowered|raised|set)\b|\bdone[.!]"
    r"|^\s*(did it|done|handled|all set)\b"
    # Window actions (live 2026-09-27: "Closed the scratch notes window." with no tool run).
    r"|^\s*(closed|closing|minimi[sz](ed|ing)|brought up|bringing up|opened|opening|hid|hiding)\b",
    re.IGNORECASE,
)
# "Checked" only counts as a claim when no real check ran this turn.
_CHECK_CLAIM = re.compile(r"\bi('ve| have)?\s+(just\s+|already\s+)?checked\b|\bchecked the (last )?reading\b", re.IGNORECASE)
_PERCENT_CLAIM = re.compile(r"\b(it'?s|volume'?s?|it is|now it'?s)\s+(now\s+)?(at\s+)?\d+\s*%", re.IGNORECASE)
_PAST_OR_HABIT = re.compile(
    r"\b(i (just |still )?remember(ed)?|last time|like before|you always|you never|you keep|you usually|you still"
    r"|you skip|you leave|you forget|every time you|(he|she) always|you'?ve been \w+ing\b.*\bfor \d+"
    r"|you'?re still \w+ing|3 ?a\.?m"
    # Harness, 2026-09-26: "I just remembered, last week you said you loved
    # that old arcade game we played when you were ten."
    r"|last (week|night|month|year|weekend)|yesterday|when you were (a kid|little|young|\w+)"
    r"|we (played|went|watched|talked about|did)|(mentioned|said|told me)\b.*\b(before|once|earlier)"
    r"|i (already )?knew that|already saved)\b",
    re.IGNORECASE,
)
_CONTINUITY_PHRASE = re.compile(
    r"\s*[,—–-]?\s*\b(since i (last |just )?checked|(just )?(like|as) we (agreed|said|discussed|talked( about it)?|mentioned|planned)|like always|as usual|as promised)\b"
    r"|^\s*no (changes|drift|drops|tricks)[^.!?]*[.!?]?\s*$"
    r"|\s*[,—–-]?\s*\bno (changes|drift|drops) since\b[^.!?]*",
    re.IGNORECASE,
)
_VOLUME_PROMISE = re.compile(r"\b(i'?ll|let me|i will|i'?m going to)\s+(lower|raise|turn|drop|bump|set|adjust|mute|unmute)\b", re.IGNORECASE)
# Any other promise to act, or narrating the act ("I'll start it right now",
# "I'm adding it now"): the tools have run before the reply, so a promise in
# the reply never happens (live 2026-09-28: Word, music, texting; S91).
# By grammar, not by a list of verbs from past examples (S96): first person
# saying it will do, or is doing, something. Only an acting tool this turn can
# back that. Verbs of being, feeling and thinking are not actions, so "I'm
# feeling good", "let me know" and "I'll be here" stay.
_NOT_ACTIONS = (r"(?:be|being|feel|feeling|think|thinking|guess|guessing|wonder|wondering|hope|hoping|know|"
                r"see|mean|meaning|love|loving|listening|doing|going|kidding|joking|trying|rooting|here|not|"
                r"always|still|sure)\b")
_ACTION_PROMISE = re.compile(
    r"\b(?:i'?ll|i will|i'?m going to|i'?m gonna|let me)\s+(?:just\s+|now\s+|go ahead and\s+)?"
    r"(?!" + _NOT_ACTIONS + r")[a-z]{2,}"
    r"|\bi'?m\s+(?:just\s+|now\s+|already\s+)?(?!" + _NOT_ACTIONS + r")[a-z]+ing\b",
    re.IGNORECASE,
)
# "*sighs*" hugs its asterisks; "* item" is a bullet (live: "* *Chuckles* That's..." became "Chuckles* That's...")
_STARRED = re.compile(r"\*(?!\s)([^*\n]{1,40}?)(?<!\s)\*")


def _only_playable_directions(text: str) -> str:
    """
    Keep a *stage direction* only when MBUDDY's voice can make it (a laugh, a
    sigh: tts.expressions.STAGE_DIRECTIONS). Any other acted-out action is
    something that didn't happen ("*types*" with no way to type), so it goes;
    starred emphasis keeps its word.
    """
    from tts.expressions import STAGE_DIRECTIONS

    def one(m: re.Match) -> str:
        body = m.group(1).strip()
        if STAGE_DIRECTIONS.get(body.lower()):
            return m.group(0)
        acted = body.lower() in STAGE_DIRECTIONS or any(
            w.endswith(("s", "ing", "ed")) for w in re.findall(r"[a-z]+", body.lower()))
        return "" if acted else body
    return _STARRED.sub(one, text)
_STILL_AT = re.compile(r"\bstill (at )?\d{1,3}\s*%", re.IGNORECASE)
_FILLER_ONLY = re.compile(r"^\s*(mm-?hmm|huh|got it|okay|ok|sure|right|yeah|hmm|oh|ah|oh wow|wow)\s*[.!,]?\s*$", re.IGNORECASE)
_GUARD_STOP = {
    "you", "your", "i", "the", "a", "an", "and", "that", "this", "it", "is", "are", "was", "at", "on",
    "in", "of", "to", "after", "always", "never", "keep", "usually", "still", "remember", "last", "time",
    "say", "said", "again", "huh", "like", "before", "every", "when", "just", "do", "did", "eating",
    "been", "for", "minutes", "hours", "working",
}


# What a claim is about -> the tool whose real run backs it. A claim is backed by one logged action of the right tool, never by
# a topic word that merely appears somewhere in the log (live 2026-10-11: "saved everything into that Word document" passed
# because the log held "memory: Recalled from memory", a lookup, not a save).
_ACTION_TOPICS = {
    "timer": (r"timer", r"timer"),
    "volume": (r"volume|mute|unmute|loud|quiet|%", r"volume"),
    "reminder": (r"remind", r"remind"),
    "task": (r"task|list|added", r"task"),
    "memory": (r"saved|remember|memory", r"memory|remember|note"),
}
# A read is not a write: a recall or a look-up never backs "I saved" or "I added".
_READ_RESULT = re.compile(r"^\s*(recalled|nothing stored|nothing found|no (matching|results?)|found|here is|here are)", re.IGNORECASE)
_LOG_LINE = re.compile(r"^\s*-\s*(?:\d{1,2}:\d{2}(?::\d{2})?\s+)?([A-Za-z_][\w.-]*):\s*(.*)$")


def _supported_by_actions(sentence: str, actions_text: str) -> bool:
    """
    True when the claim is about something the action log shows really
    happened ("I set a timer" after set_timer ran two turns ago): one logged
    action of the matching tool that wrote something, not a recall.
    """
    if not actions_text:
        return False
    s = sentence.lower()
    entries = []
    for line in actions_text.splitlines():
        m = _LOG_LINE.match(line)
        if m:
            entries.append((m.group(1).lower(), m.group(2)))
    for said, tool in _ACTION_TOPICS.values():
        if not re.search(said, s):
            continue
        for name, result in entries:
            if re.search(tool, name) and not _READ_RESULT.match(result):
                return True
    return False


def _supported_by_memory(sentence: str, memory_text: str) -> bool:
    """True when the sentence's content words actually appear in memory."""
    words = {w for w in re.findall(r"[a-z]+", sentence.lower()) if len(w) > 3 and w not in _GUARD_STOP}
    if not words or not memory_text:
        return False
    known = memory_text.lower()
    return sum(1 for w in words if w.rstrip("s") in known) >= max(1, len(words) // 2)


# Ongoing work by its grammar: "still", or a present progressive in the first person.
_ONGOING = re.compile(r"\bstill\b|\bi(?:'m| am)\b[^.!?]{0,60}\b[a-z]{3,}ing\b|\b(?:currently|right now)\b", re.IGNORECASE)

# Grammar words: they carry no claim of their own.
_FUNCTION_WORDS = frozenset(
    "that this with have your just right still yeah sure really what when will been from about them they then "
    "than some here there now very much more also only even into over onto were does doing done being would "
    "could should while until again already always never".split())


def _supported_by_jobs(sentence: str, background: list[dict] | None) -> bool:
    """True when MBUDDY's own background work backs the sentence (W1): it names
    the app or topic of a job that is running or just ended, or, while a job
    is running, its words are the job's own ("Yes, I'm working on it"). A claim
    about anything else ("I'm playing some jazz") is not backed by the job."""
    if not background:
        return False
    s = sentence.lower()
    for job in background:
        name = str(job.get("subject", "")).lower().split()
        if name and name[0] in s:
            # An ended job backs what was done, not a claim of still doing it
            # (live: "I'm still in <app>, working on it" after it had finished).
            if job.get("running") or not _ONGOING.search(s):
                return True
    running = [job for job in background if job.get("running")]
    if not running:
        return False
    from core import jobs
    now = time.time()
    known = " ".join(jobs._line(job, now) for job in running).lower()
    words = [w for w in re.findall(r"[a-z]+", s) if len(w) > 3 and w not in _FUNCTION_WORDS]
    # Nothing but grammar ("Yes, I am.") claims nothing beyond the job.
    return not words or sum(1 for w in words if w.rstrip("s") in known) * 2 >= len(words)


def _says_ended_job_is_ongoing(sentence: str, background: list[dict] | None) -> bool:
    """True when the sentence speaks of still working in an app or on a topic
    whose job has ended and isn't running again (F2)."""
    if not background or not _ONGOING.search(sentence):
        return False
    s = sentence.lower()
    if re.search(r"\b(can'?t|cannot|don'?t|won'?t|not able to|unable to|stopped|finished|ended)\b", s):
        return False
    running = {str(j.get("subject", "")).lower() for j in background if j.get("running")}
    for job in background:
        subject = str(job.get("subject", "")).lower()
        name = subject.split()
        if name and name[0] in s and not job.get("running") and subject not in running:
            return True
    return False


# ── facts of the turn: a detail in the reply that the tool's own result contradicts ───────────────────────
# The field test (2026-10-19): "it's just a one-time reminder" when the tool said every day; "Saved as [QA] Bicycle
# history.docx" when it saved "... (2).docx"; "sunny tomorrow" when only the current weather was read.
_ONCE_WORDS = re.compile(r"\b(one[- ]time|one[- ]off|just once|only once|once only|single reminder|won'?t repeat|no repeat)\b", re.I)
_EVERY_WORDS = re.compile(r"\b(every (hour|day|morning|evening|night|week)|daily|hourly|weekly|each (day|week)|repeats?|recurring)\b", re.I)
_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)|\b(\d{1,2}):(\d{2})\b", re.I)
_FILE = re.compile(r"[\w\[\]() .\-]{1,80}\.(?:docx|xlsx|pptx|txt|pdf|png|jpg|blend|fcstd|ics|csv)\b", re.I)


def _minutes(text: str) -> set[int]:
    out = set()
    for m in _CLOCK.finditer(text or ""):
        if m.group(3):
            h, mi = int(m.group(1)) % 12, int(m.group(2) or 0)
            h += 12 if m.group(3).lower().startswith("p") else 0
        else:
            h, mi = int(m.group(4)), int(m.group(5))
        if h < 24 and mi < 60:
            out.add(h * 60 + mi)
    return out


def contradicts_results(sentence: str, results: str) -> str:
    """Why [sentence] contradicts the tool results of this turn ("" when it doesn't, or when no result speaks to it)."""
    if not results or not sentence:
        return ""
    r, s = results, sentence
    said_every = bool(_EVERY_WORDS.search(r))
    if _ONCE_WORDS.search(s) and said_every and not _ONCE_WORDS.search(r) and " once" not in r.lower():
        return "says once, the result repeats"
    if _EVERY_WORDS.search(s) and re.search(r"\bReminder set: .+ on \w{3} \d{1,2} \w{3}", r) and not said_every:
        return "says it repeats, the result is one time"
    told, real = _minutes(s), _minutes(r)
    scheduling = re.search(r"Reminder set|Changed:|Marked done|timer|It's \d{1,2}:\d{2}|It's \d{1,2} ?[AP]M", r, re.I)
    if scheduling and told and real and not (told & real):
        return "a time the result doesn't have"
    names = {m.group(0).strip().strip('"').lower() for m in _FILE.finditer(r)}
    for m in _FILE.finditer(s):
        name = m.group(0).strip().strip('"').lower()
        if names and not any(name.endswith(n.split("\\")[-1]) or n.endswith(name.split("\\")[-1]) for n in names):
            return "a file name the result doesn't have"
    if re.search(r"\btomorrow|tonight|later today|this weekend\b", s, re.I) and "Current weather" in r and "Forecast" not in r \
            and re.search(r"\b(sunny|rain|cloud|°|degrees|warm|cold|hot|wind)\b", s, re.I):
        return "a forecast from the current weather"
    return ""


async def truth_guard(
    stream: AsyncIterator[str],
    *,
    tools_ran: bool,
    memory_text: str,
    real_volume: int | None = None,
    quiet_if_empty: bool = False,
    acted: bool | None = None,
    checked: bool = False,
    actions_text: str = "",
    background: list[dict] | None = None,
    on_empty=None,
    results_text: str = "",
) -> AsyncIterator[str]:
    """
    Yield the reply sentence by sentence, dropping false claims.

    - action claims need a tool that ran this turn, or a matching entry in
      the action log (`actions_text`); "I checked" needs a real check
      (`checked`), not an earlier reply;
    - a volume percentage that disagrees with Windows (`real_volume`) is
      replaced by the real value;
    - claims about the user's habits or shared past need support in memory;
    - an opening filler ("Mm-hmm.") is dropped when real content follows;
    - MBUDDY's own background work (`background`, core.jobs) supports what it
      says about that work;
    - when nothing is left, `on_empty(kind, dropped)` writes the reply again
      from the facts instead of a stub (W1: "Nothing changed just now." while
      a job was running).
    """
    text, emitted, kept = "", 0, 0
    dropped: list[str] = []
    did_act = tools_ran if acted is None else acted  # a read (search, clock) is not an action
    dropped_action = dropped_memory = False
    pending_filler = ""
    # After a false action claim, the rest of the reply builds on it ("I'm
    # turning off the lights." then "The room should go dark now."): it goes too.
    poisoned = False

    def verdict(sentence: str) -> str:
        nonlocal dropped_action, dropped_memory, poisoned
        # The model writes curly apostrophes ("it’s"); match on straight ones.
        body = re.sub(r"^\s*\[[A-Z]+\]\s*", "", sentence).replace("’", "'").replace("‘", "'")
        if poisoned:
            log[2](f"[Guard] dropped the rest of a false claim: {body.strip()[:80]}")
            return ""
        if real_volume is not None:
            fixed = re.sub(r"\b(\d{1,3})(\s*%|\s+percent)", lambda m: f"{real_volume}{m.group(2)}", body)
            if fixed != body:
                if fixed != re.sub(r"\b(\d{1,3})(\s*%|\s+percent)", lambda m: m.group(0), body):
                    log[2](f"[Guard] corrected volume to the real {real_volume}%: {body.strip()[:80]}")
                body = fixed
        percent_ok = real_volume is not None
        # Continuity nobody observed ("No changes since", "since I checked",
        # "just like we agreed"): cut the phrase, keep the rest.
        trimmed = _CONTINUITY_PHRASE.sub("", body)
        if trimmed != body:
            log[2](f"[Guard] cut invented continuity: {body.strip()[:80]}")
            body = trimmed if re.search(r"[A-Za-z]{2}", trimmed) else ""
            if not body:
                return ""
            if not re.search(r"[.!?]\s*$", body):
                body = body.rstrip(" ,;:—–-") + "."
        # A volume promise is either already done (the tool ran and the reply
        # should report it) or never going to happen (no tool ran). "Still at
        # 60%" after the tool changed it contradicts what just happened.
        if _VOLUME_PROMISE.search(body) or (tools_ran and real_volume is not None and _STILL_AT.search(body)):
            log[2](f"[Guard] dropped volume promise / stale level: {body.strip()[:80]}")
            dropped_action = dropped_action or not tools_ran
            dropped.append(body.strip())
            return ""
        staged = _only_playable_directions(body)
        if staged != body:
            log[2](f"[Guard] cut acted-out action: {body.strip()[:80]}")
            body = staged.strip() if re.search(r"[A-Za-z]{2}", staged) else ""
            if not body:
                return ""
            body = body[:1].upper() + body[1:]
        in_job = _supported_by_jobs(body, background)  # its own background work backs it
        wrong = contradicts_results(body, results_text)
        if wrong:
            log[2](f"[Guard] dropped a detail the tool result contradicts ({wrong}): {body.strip()[:80]}")
            dropped_action = True
            dropped.append(body.strip())
            return ""
        if _says_ended_job_is_ongoing(body, background):
            log[2](f"[Guard] dropped a claim of still working on a job that has ended: {body.strip()[:80]}")
            dropped_action = poisoned = True
            dropped.append(body.strip())
            return ""
        if not did_act and not in_job and _ACTION_PROMISE.search(body):
            log[2](f"[Guard] dropped action promise: {body.strip()[:80]}")
            dropped_action = poisoned = True
            dropped.append(body.strip())
            return ""
        if not did_act and not in_job and (_ACTION_CLAIM.search(body) and not _supported_by_actions(body, actions_text)):
            dropped_action = poisoned = True
            log[2](f"[Guard] dropped action claim (nothing was done): {body.strip()[:80]}")
            dropped.append(body.strip())
            return ""
        if not tools_ran and not in_job and (
            (_ACTION_CLAIM.search(body) and not _supported_by_actions(body, actions_text))
            or (_CHECK_CLAIM.search(body) and not checked)
            or (_PERCENT_CLAIM.search(body) and not percent_ok)
        ):
            dropped_action = True
            log[2](f"[Guard] dropped action claim (no tool ran): {body.strip()[:80]}")
            dropped.append(body.strip())
            return ""
        if not in_job and _PAST_OR_HABIT.search(body) and not _supported_by_memory(body, memory_text):
            dropped_memory = True
            log[2](f"[Guard] dropped unsupported memory claim: {body.strip()[:80]}")
            dropped.append(body.strip())
            return ""
        return body

    def emit(good: str):
        nonlocal kept, pending_filler
        prefix = (tag + " ") if kept == 0 and tag else ""
        if kept == 0 and not pending_filler and _FILLER_ONLY.match(good):
            pending_filler = good.strip()  # held: dropped if real content follows
            return ""
        pending_filler = ""
        kept += 1
        return prefix + good.strip()

    tag = ""
    async for delta in stream:
        text += delta
        if not tag:
            m = re.match(r"\s*(\[[A-Z]+\])\s*", text)
            if m:
                tag, emitted = m.group(1), m.end()
        for end in [m.end() for m in _SENTENCE_END_ANY.finditer(text, emitted)]:
            sentence = text[emitted:end]
            emitted = end
            good = verdict(sentence)
            if good.strip():
                out = emit(good)
                if out:
                    yield out + " "
    rest = text[emitted:]
    if rest.strip():
        good = verdict(rest)
        if good.strip():
            out = emit(good)
            if out:
                yield out
    if kept == 0:
        if quiet_if_empty:
            return  # code already said what happened; nothing to add
        if on_empty is not None and (dropped_action or dropped_memory):
            # Never a stub: write it again from the facts (W1).
            async for piece in on_empty("action" if dropped_action else "memory", dropped):
                yield piece
            return
        if dropped_action:
            yield f"{tag or '[NORMAL]'} Nothing changed just now."
        elif dropped_memory:
            yield f"{tag or '[NORMAL]'} I don't know that about you yet."
        elif pending_filler:
            yield f"{tag + ' ' if tag else ''}{pending_filler}"
        elif tag:
            yield tag


# Sentence boundary for the guard: . ! ? (not inside "...") and the end
# of a chunk only when followed by whitespace.
_SENTENCE_END_ANY = re.compile(r"(?<!\.)[.!?](?!\.)(?=\s)")


def _pc_status_off() -> bool:
    from .agent import tool_switched_off
    return tool_switched_off("pc_status")


CONVERSATION_GAP_S = 30 * 60   # this long without a turn and the next message starts a new conversation


def this_conversation(history: list, now: float | None = None) -> list:
    """The entries since the last gap of CONVERSATION_GAP_S (an entry without a time counts as current)."""
    last = time.time() if now is None else now
    kept = []
    for entry in reversed(history or []):
        at = entry.get("at") if isinstance(entry, dict) else None
        if at is not None:
            if last - float(at) > CONVERSATION_GAP_S:
                break
            last = float(at)
        kept.append(entry)
    return kept[::-1]


async def ask_mbuddy_stream(
    state,
    user_text: str,
    history: list,
    memory: dict,
    detected_lang: str,
    mood_ctx: str = "",
    cognitive_state=None,
    behavior_decision=None,
    heard_unclearly: bool = False,
) -> AsyncIterator[str]:
    """
    Primary MBUDDY entry point. Streams reply chunks.
    Automatically falls back to secondary provider on quota / error.
    """
    from .reply_policy import reply_max_sentences, reply_max_tokens, reply_temperature
    from .memory_context import build_conversation_system
    from .personality import strip_emotion_tags
    from .fallbacks import fallback_reply
    from .response_authenticity import build_authenticity_context
    from stt.language_policy import reply_matches_language
    from utils.web_search import perform_web_search

    # Only this conversation (F46: 'try again' ran another person's routine from an earlier conversation; F35:
    # old questions were re-answered). The whole list is kept and grows; this turn sees what is still current.
    store, history = history, this_conversation(history)

    # The user's message is the strongest signal about what was just done (brain/outcomes.py):
    # thanks, a correction or a retry is read in the background and teaches the notes.
    try:
        from brain import outcomes
        outcomes.on_user_message(user_text)
    except Exception as exc:  # noqa: BLE001
        log[2](f"[Outcome] reaction skipped: {exc}")

    # Dictation (brain/dialog.py): while the user is dictating, or opens one ("write something", "take a
    # note"), code answers: no model call, and a save is read back before it is claimed.
    try:
        from brain import dialog
        dictation = dialog.handle(user_text)
    except Exception as exc:  # noqa: BLE001
        log[2](f"[Dialog] skipped: {exc}")
        dictation = None
    if dictation is not None:
        yield f"[{dictation.tag}] {dictation.say}"
        return

    # "Use the bigger model to ...": named by the user, so it is honoured (brain/escalation.py). On a failure
    # the reason is said and the local model still answers.
    try:
        from brain import escalation
        bigger_task = escalation.explicit(user_text)
    except Exception as exc:  # noqa: BLE001
        log[2](f"[Escalation] skipped: {exc}")
        bigger_task = None
    if bigger_task:
        source = escalation.source_of(bigger_task)
        if source:
            # Something from this PC would leave it: the usual yes first, naming exactly what is sent and where.
            from brain import pc_actions
            asked = await asyncio.to_thread(pc_actions.run, "ask_bigger_model_about", {"task": bigger_task, "source": source})
            pending_q = getattr(getattr(asked, "pending", None), "question", "")
            if pending_q:
                yield f"[FOCUSED] {pending_q}"
                return
            yield f"[FOCUSED] {str(asked).removeprefix('FAILED: ')} "
        else:
            ok, words = await asyncio.to_thread(escalation.run, bigger_task)
            if ok:
                yield f"[NORMAL] {words}"
                return
            yield f"[FOCUSED] {words} "

    # ── 0. Is the local brain there? ──────────────────────────
    # Down, a turn used to retry 3x3 in silence for ~30 s before a vague
    # "connection dropped" (M2 R4). Now it says so at once, starts Ollama if
    # nothing is listening, and continues; if it can't, it says why and stops.
    # Neither line goes into the history (MBUDDY's own error lines were read
    # back to it as conversation, R5).
    providers_now = get_available_providers()
    if providers_now[:1] == ["ollama"]:
        from . import ollama_service
        # Only when nothing is listening is it started and said; a brain
        # that's there but slow is left to the normal timeouts.
        if await asyncio.to_thread(ollama_service.probe) == "refused":
            yield "[FOCUSED] Starting my brain, one moment. "
            # 20 s here (the reply itself still needs room); past it, a
            # background wait finishes the start and warms it (B2).
            starting = asyncio.ensure_future(asyncio.to_thread(ollama_service.ensure_running, 20.0))
            done, _ = await asyncio.wait({starting}, timeout=8.0)
            if not done:
                yield "[FOCUSED] Still waking up. "
            up, why = await starting
            if not up:
                if "didn't answer" in why:
                    ollama_service.finish_start_in_background()
                    yield "[FOCUSED] My brain is still waking up. Ask me again in a few seconds."
                else:
                    yield f"[SAD] I can't reach my brain right now: {why}."
                return
        await asyncio.to_thread(ollama_service.warm_if_needed)

    # ── 1. Semantic Memory ────────────────────────────────────
    related_memories = []
    if len(user_text.split()) >= 3:
        try:
            from core import timeline
            timeline.mark("memory search>")
            candidates = await asyncio.wait_for(
                asyncio.to_thread(get_vector_store().search, user_text, 4, True),
                timeout=2.0,
            )
            timeline.mark("memory search<")
            recent_keys = memory.setdefault("_recent_semantic_memory_keys", [])
            fresh = [
                item for item in candidates
                if _memory_key(str(item.get("text", ""))) not in recent_keys
            ]
            selected = (fresh or candidates)[:1]
            if selected:
                key = _memory_key(str(selected[0].get("text", "")))
                memory["_recent_semantic_memory_keys"] = (recent_keys + [key])[-5:]
            related_memories = selected
        except asyncio.TimeoutError:
            log[2]("[Router] Semantic memory search timed out - continuing without it.")
        except Exception as exc:
            log[2](f"[Router] Semantic memory unavailable: {type(exc).__name__}")
    semantic_ctx = ""
    if related_memories:
        semantic_ctx = (
            "\nPOSSIBLY RELEVANT MEMORY (use silently unless it truly improves the reply):\n"
            + "\n".join(f"- {m['text']}" for m in related_memories)
        )

    # ── 2. Tool Execution (if behavior decision selected a tool) ───
    tool_output = ""
    # Whether a tool has already supplied live data for this turn. A web
    # search on top of that fetches the same facts a second time and doubles
    # the prompt, which is what pushed requests over the provider's limits.
    tool_answered = False
    tool_name = None
    phone_skip = False      # a keyword tool was set aside because the request is for the user's phone
    keyword_result = None   # what a keyword-chosen tool returned (a reflex may answer with it, core/reflex.py)
    if behavior_decision and behavior_decision.tool:
        tool_name = behavior_decision.tool
        from .agent import _PHONE_DOES, _PHONE_TARGET, tool_switched_off
        if tool_name in ("timer", "task", "clock") and _PHONE_TARGET.search(user_text) and _PHONE_DOES.search(user_text):
            # "put a timer on my phone": the user said where it is for. The PC's own timer, started by a keyword, is the
            # wrong answer: the phone area (the agent) handles it.
            log[0](f"[Router] '{tool_name}' keyword skipped: the request is for the phone")
            tool_name, phone_skip = None, True
        keyword_setting_tool = {"sys_volume": "adjust_volume", "timer": "set_timer", "task": "add_task",
                                "web_search": "web_search", "news": "web_search"}.get(tool_name)
        if keyword_setting_tool and tool_switched_off(keyword_setting_tool):
            tool_output = f"\n\nTOOL FAILED ({tool_name}): switched off in MBUDDY's settings; say so plainly.\n"
            log[2](f"[Router] {tool_name} is switched off in settings")
            tool_name = None
    if tool_name:
        try:
            log[0](f"🔧 Executing tool: {tool_name}")
            result = await asyncio.wait_for(
                execute_tool_async(tool_name, user_text, memory=memory),
                timeout=10.0
            )
            from brain.tools import ToolFailure

            if isinstance(result, ToolFailure):
                # Say what happened rather than presenting the explanation as
                # data. The model is told plainly that nothing was retrieved,
                # so it reports that instead of inventing a plausible number.
                tool_output = f"\n\nTOOL FAILED ({tool_name}): {result}\n"
                log[2](f"✗ Tool {tool_name} returned no data: {result}")
            elif result:
                keyword_result = result
                tool_output = f"\n\nTOOL OUTPUT ({tool_name}):\n{result}\n"
                tool_answered = True
                log[1](f"✓ Tool {tool_name} executed successfully")
                if not str(result).startswith("Current system volume is"):  # a read, not an action
                    from brain import action_log
                    action_log.record(tool_name, {"said": user_text[:80]}, result)
        except asyncio.TimeoutError:
            log[2](f"[Router] Tool {tool_name} timed out")
            tool_output = f"\n\nTOOL ERROR: {tool_name} timed out\n"
        except Exception as exc:
            log[3](f"[Router] Tool {tool_name} failed: {exc}")
            tool_output = f"\n\nTOOL ERROR: {tool_name} failed\n"

    # ── 2a. Verification: check the real state, don't trust earlier replies
    verifying = is_verification_question(user_text)
    checked_block = await asyncio.to_thread(check_real_state, user_text) if verifying else ""
    if verifying:
        log[0](f"[Router] verification question, checked real state: {checked_block[-160:]!r}")

    # ── 2b. Agent step (hybrid router) ───────────────────────────
    # No keyword rule picked a tool: let the local model decide whether what
    # the user said implies an action ("my head hurts, the volume is high" ->
    # volume down), run it for real, and hand the reply the actual results.
    agent_tools: set[str] = set()
    agent_ran = False
    agent_results: list = []
    facts_before_turn = [str(f) for f in ((memory or {}).get("core_facts") or (memory or {}).get("facts") or [])]
    # A PC action is waiting for a yes (brain/pc_windows.py): this turn is the
    # answer. Only a clear yes carries it out; anything else cancels it.
    from brain import pc_windows
    from brain import pc_actions
    settled = pc_windows.resolve(user_text) if pc_windows.pending() else None
    settled = settled or (pc_actions.resolve(user_text) if pc_actions.pending() else None)
    # "Close Spotify" is a direct command: no model needed to know what's meant
    # (live, the model skipped it when earlier replies were in the history).
    command = None if settled else pc_windows.direct_command(user_text)
    if command and command[0] in ("next", "previous") and not await asyncio.to_thread(pc_windows.now_playing):
        command = None  # "Next." with no music player: ordinary talk
    if command and command[0] not in pc_windows.MEDIA_ACTIONS and not await asyncio.to_thread(pc_windows.find_window, command[1]):
        command = None  # "close enough": no such window, so not a command
    if command:
        from .agent import format_tool_output, tool_switched_off
        from brain.tools import ToolFailure
        if tool_switched_off("window_action"):
            command_result = ToolFailure("Window actions are switched off in MBUDDY's settings.")
        else:
            command_result = await asyncio.to_thread(pc_windows.request, *command)
        agent_ran, agent_results = True, [("window_action", {"action": command[0], "target": command[1]}, command_result)]
        tool_output += format_tool_output(agent_results)
        tool_answered = True
    elif not settled and pc_windows.is_pc_question(user_text) and not _pc_status_off():
        # "Is anything playing?" is read live every time, never from history.
        from .agent import format_tool_output
        status = await asyncio.to_thread(pc_windows.pc_status_text)
        agent_ran, agent_results = True, [("pc_status", {}, status)]
        tool_output += format_tool_output(agent_results)
        agent_tools, tool_answered = {"pc_status"}, True
    elif settled:
        from .agent import format_tool_output
        from brain.tools import ToolFailure
        agent_ran, agent_results = True, [settled]
        tool_output += format_tool_output(agent_results)
        agent_tools = {settled[0]} if not isinstance(settled[2], ToolFailure) else set()
        tool_answered = True
    elif (phone_skip or not (behavior_decision and behavior_decision.tool)) and providers_include_ollama():
        agent_ran = True
        from .agent import format_tool_output, run_agent
        from brain.tools import ToolFailure

        agent_results = await run_agent(user_text, history, memory, question_only=verifying)
        if agent_results:
            tool_output += format_tool_output(agent_results)
            agent_tools = {name for name, _a, r in agent_results if not isinstance(r, ToolFailure)}
            tool_answered = bool(agent_tools)

    # A PC action is asked about, or was just answered: the words come from
    # code, never the model (live, the model said "Closing it now" when it had
    # only asked, S49).
    from brain.tools import ToolFailure as _Failure
    if settled:
        result = settled[2]
        yield f"[NORMAL] {result}" if isinstance(result, _Failure) or not str(result).startswith("Cancelled") \
            else f"[NORMAL] Okay, I left {settled[1]['target']} as it is."
        return
    waiting = pc_actions.pending() or pc_windows.pending()
    if waiting and any(isinstance(r, str) and r.startswith("NOT DONE YET") for _n, _a, r in (agent_results or [])):
        yield f"[NORMAL] Want me to {waiting.describe()}? Say yes or no."
        return
    # Every PC action's result is said by code; the model adds at most one
    # warm sentence and can't claim more (S89, S96: "playing" when it wasn't).
    from .agent import spoken_results
    said = spoken_results(agent_results) if not failures_only(agent_results) else []
    acted_this_turn = acted(agent_results, getattr(behavior_decision, "tool", "") if tool_answered else "")
    said_ok = any(not isinstance(r, _Failure) and not str(r).startswith("NOT ")
                  for n, _a, r in (agent_results or []) if _spoken_line_for(n))
    # Every tool this turn failed: say what failed, in code. Left to the model,
    # "I couldn't understand the date" became "That event doesn't exist" and
    # then a refusal it kept repeating (live 2026-09-27).
    failures = [r for _n, _a, r in (agent_results or []) if isinstance(r, _Failure)]
    if failures and len(failures) == len(agent_results):
        lines = ["I can't do that yet." if str(f).startswith("There is no tool called")
                 else re.sub(r"^(FAILED:\s*)+", "", str(f)) for f in failures]
        try:
            from brain import brain_store, outcomes
            _blk, _paths = await asyncio.to_thread(brain_store.recall, user_text)
            outcomes.record_turn(user_text, list(agent_results or []), _paths)   # every tool failed: the clearest lesson
        except Exception as exc:  # noqa: BLE001
            log[2](f"[Outcome] not recorded: {exc}")
        yield "[NORMAL] " + " ".join(dict.fromkeys(lines))
        return

    # ── 3. Internet Search ────────────────────────────────────
    # Tools that already return live external data. Searching after one of
    # these succeeds asks the internet a question that was just answered.
    LIVE_DATA_TOOLS = {"weather", "web_search", "news", "get_weather"}

    internet_ctx = ""
    skip_search = tool_answered and (
        (behavior_decision and behavior_decision.tool in LIVE_DATA_TOOLS)
        or bool(agent_tools & LIVE_DATA_TOOLS)
    )
    try:
        if skip_search:
            log[0]("[Router] Search skipped — a tool already returned live data")
            raise _SearchNotNeeded
        if agent_ran:
            # The agent has web_search and already decided whether this turn
            # needs it. The old keyword/LLM search decision on top of it cost
            # an extra model call per turn and searched the web for "I'm on a
            # call now" (68 s) because of the word "now".
            raise _SearchNotNeeded
        search_query = await asyncio.wait_for(determine_search_query(user_text, history), timeout=4.0)
        if search_query:
            log[0](f"🔍 Brain decided to search: '{search_query}'")
            internet_ctx = "\n\nREAL-TIME INTERNET INFO:\n" + await asyncio.wait_for(
                perform_web_search(search_query, max_results=5),
                timeout=12.0,
            )
    except _SearchNotNeeded:
        pass
    except asyncio.TimeoutError:
        log[2](f"[Router] Search reasoning timed out for '{user_text[:30]}…'")
    except Exception as e:
        log[2](f"[Router] Search error: {e}")

    # A live lookup was needed and every attempt failed: say so, in code.
    # Left to the model, a failed search came out as "wasn't it Real Madrid?
    # I think they pulled off another win" from stale training data.
    tried_live = ({getattr(behavior_decision, "tool", "")} & LIVE_DATA_TOOLS) or \
        ({name for name, _a, _r in (agent_results or [])} & LIVE_DATA_TOOLS)
    if tried_live and not tool_answered and not internet_ctx:
        log[2]("[Router] live lookup failed; answering honestly instead of from model memory")
        yield "[NORMAL] I couldn't look that up just now, the search didn't come back. Want me to try again?"
        return

    # ── 4. System Prompt ──────────────────────────────────────
    # Wake-word based context loading:
    # - Full context ONLY on "hey buddy" wake phrase
    # - Minimal prompt for all follow-up queries to avoid payload bloat
    # Reflex: a simple command (time, calculation, timer, reminder, volume) the
    # tool has already answered in one finished sentence. The model is not
    # called to repeat it; the sentence is the reply, and the ledger tells the
    # model about it the next time it runs (core/reflex.py, core/ledger.py).
    if tool_name and keyword_result is not None and not agent_ran and not heard_unclearly:
        from core import reflex
        instant = reflex.reply_for(tool_name, keyword_result)
        if instant:
            reflex.handled(user_text, tool_name, instant)
            try:
                from brain import outcomes
                outcomes.record_turn(user_text, [(tool_name, {}, keyword_result)], [])   # so the next message can correct it
            except Exception as exc:  # noqa: BLE001
                log[2](f"[Outcome] not recorded: {exc}")
            log[0](f"[Reflex] {tool_name} answered by code, no model call")
            yield f"[NORMAL] {instant}"
            return

    from brain.memory_context import wants_introspection

    # Groq's ceiling here is tokens per minute across every request, not just
    # per call, so the deciding question is what this turn genuinely needs.
    #
    # Full prompt only when MBUDDY is being asked about itself, or when live
    # search results need the rules that govern how to use them. Everything
    # else — including the wake word, which used to trigger the heaviest
    # prompt of all — runs on identity, mood, language and the transcript.
    is_wake_word = "hey buddy" in user_text.lower() or "hi buddy" in user_text.lower()
    # "What apps are running?" is about the PC, not MBUDDY: when the PC-status
    # tool answered, MBUDDY's own state dump (~4,800 chars) isn't needed (live,
    # it pushed the prompt to 12k chars and the reply timed out at 30 s).
    is_introspective = wants_introspection(user_text) and "pc_status" not in agent_tools
    # Live facts go AFTER the conversation, not in the system prompt: a small
    # model trusts what it read last, and live that was its own false replies
    # ("Riptide" kept winning over a fresh reading that said "Golden").
    live_facts: list[str] = []
    needs_source_rules = bool(internet_ctx)

    # What changes each turn goes in one system message after the conversation
    # (M2 S6): the system prompt and the history then stay cached, and only
    # this block and the user's words are read anew (~110 tokens/s here).
    turn_parts: list[str] = []
    if not is_introspective and not needs_source_rules:
        from brain.memory_context import build_minimal_system, build_turn_block
        system = build_minimal_system(state, memory, detected_lang)
        turn_parts.append(build_turn_block(state))
        from core import ledger
        if ledger.block():
            turn_parts.append(ledger.block())   # what code handled without the model since it last ran
        ledger.mark_model_turn()
        if tool_output:
            live_facts.append(tool_output.strip())
        why = "wake" if is_wake_word else "turn"
        log[0](f"[Brain] Lean prompt ({why}): '{user_text[:40]}'")
    else:
        system = build_conversation_system(
            state,
            memory,
            detected_lang,
            introspective=is_introspective,
            mood_ctx=mood_ctx,
            cognitive_state=cognitive_state,
            behavior_decision=behavior_decision,
            world_model=getattr(cognitive_state, "worldModel", None),
            capability_registry=getattr(cognitive_state, "capabilityRegistry", None),
            authenticity_context=build_authenticity_context(history),
        )
        if tool_output:
            live_facts.append(tool_output.strip())
        if semantic_ctx:
            system += f"\n\n{semantic_ctx}"
        if internet_ctx:
            system += internet_ctx
            system += (
                "\n\nINTERNET ANSWER RULES:\n"
                "- Treat REAL-TIME INTERNET INFO as source snippets, not gospel.\n"
                "- Answer only what the snippets support; do not invent missing details from memory.\n"
                "- Never merge conflicting snippets into one claim. If sources conflict, say they conflict and give only the safest verified part.\n"
                "- If search failed, returned no results, or snippets do not answer the question, say you cannot verify the exact answer right now.\n"
                "- Use 'as of today' qualifiers for current facts.\n"
                "- Prefer official competition/organization/source pages when they appear.\n"
            )
        if len(system) > MAX_SYSTEM_PROMPT_CHARS:
            system = (
                _clip_text(system, MAX_SYSTEM_PROMPT_CHARS)
                + "\n\nCONTEXT TRUNCATED FOR PROVIDER LIMITS. Keep the answer concise."
                + "\nALWAYS start every reply with exactly ONE valid emotion tag."
            )

    # What it is and what it is doing right now, from its real state (W1): it
    # denied having a voice, and didn't know its own job was running.
    from . import self_state
    from core import jobs as _jobs
    background = _jobs.snapshot()
    turn_parts.append(self_state.self_card())
    turn_parts.append(_jobs.status_text())
    from brain import awareness     # the moment: what is in front, today's time, the next event (read in the background)
    _moment = awareness.block()
    if _moment:
        turn_parts.append(_moment)

    # ── 4. Build Message Payload ──────────────────────────────
    # Non-verbal cues describe how something was said, not what was said, so
    # they belong in the system prompt rather than inside the user's turn.
    try:
        from tts.expressions import detect_user_expressions, describe_user_expressions
        cue_line = describe_user_expressions(detect_user_expressions(user_text))
        if cue_line:
            turn_parts.append(cue_line)
    except Exception:
        pass

    # Anchor volume talk to Windows, not to the conversation. Live, MBUDDY
    # kept repeating its own earlier "Volume cut to 50%" (which never
    # happened) instead of the real level.
    real_volume = None
    # Read after the tools ran, so the number is the level Windows has now.
    if verifying or re.search(r"\b(volume|audio|sound|loud|quiet|hear|mute|muted|high|low)\b", user_text.lower()):
        try:
            from brain.tools import ToolFailure, execute_volume_tool
            real = await asyncio.to_thread(execute_volume_tool, "what's the volume")
            if not isinstance(real, ToolFailure):
                live_facts.append(f"ACTUAL PC VOLUME RIGHT NOW (read from Windows this moment): {real}")
                found = re.search(r"(\d+)%", str(real))
                real_volume = int(found.group(1)) if found else None
        except Exception:
            pass
    if checked_block:
        live_facts.append(checked_block)
        # "Are you sure it's at 50?" never says "volume", but the fresh check
        # read it; the guard corrects numbers against that reading.
        found = re.search(r"volume is (\d+)%", checked_block)
        if real_volume is None and found:
            real_volume = int(found.group(1))
    from brain import action_log
    actions_text = action_log.block()
    if actions_text:
        live_facts.append(actions_text)
    # The brain is checked every turn (S102): the notes that match reach the
    # reply, at most two (S104). Notes used in a turn where an action
    # succeeded get stronger; a failure weakens them (the learning rate).
    brain_used: list = []
    brain_block = ""
    try:
        from brain import brain_store
        from core import timeline
        timeline.mark("recall>")
        brain_block, brain_used = await asyncio.to_thread(brain_store.recall, user_text)
        timeline.mark("recall<")
        log[0](brain_store.used_line())  # every turn: what the brain put in, for the log and the bench
        if brain_block:
            live_facts.append(brain_block)
        try:
            from brain import outcomes
            ran = list(agent_results or [])
            if tool_name and keyword_result is not None:
                ran.append((tool_name, {}, keyword_result))
            if ran:
                outcomes.record_turn(user_text, ran, brain_used)   # a verdict for what was done, and the feedback chain
        except Exception as exc:  # noqa: BLE001
            log[2](f"[Outcome] not recorded: {exc}")
        if brain_used:
            # A use only: an action succeeding this turn doesn't show the note
            # helped (live: an app note gained "successes" from unrelated turns and
            # a wrong skill reached 0.9). Strength moves only on code-checked
            # outcomes (U2).
            await asyncio.to_thread(brain_store.note_used, brain_used, None)
    except Exception as exc:
        log[2](f"[Brain] recall skipped: {exc}")
    if not agent_results and not tool_output.strip() and not checked_block:
        # Model first: say it outright (live, "Text my mom" got "What's the
        # subject?" as if a message could be sent; S96).
        # What it means is in TURN_RULES (cached); only the marker changes.
        live_facts.append("NOTHING WAS DONE ON THE PC THIS TURN.")
    if said:
        live_facts.append("YOU ALREADY TOLD THE USER THIS, and it is exactly what you did this turn: "
                          + " ".join(said) + "\nAdd at most one short, warm sentence of your own. Don't "
                          "repeat it or contradict it, and don't claim or promise anything else. If part of what "
                          "he asked wasn't done, say that part wasn't done; don't do it here in the chat instead.")

    # What the guard may treat as known about the user from BEFORE this turn:
    # stored facts, emotional memories and what he said in earlier turns.
    # Never MBUDDY's own replies, and not what he said just now or a fact
    # saved from it a moment ago: "I remember you mentioned Lina before"
    # right after he first said it is still invented.
    memory_text_for_guard = " ".join(
        facts_before_turn
        + [str(e.get("memory", "")) for e in memory.get("emotional_memories", []) if isinstance(e, dict)]
        + [str(h.get("content", "")) for h in history if isinstance(h, dict) and h.get("role") == "user"]
    )

    user_content = user_content_for_turn(user_text, detected_lang, heard_unclearly)
    from core.ui_settings import personalize  # the user's name from the app
    if live_facts:
        # In the turn's system message before the user's words, never as a
        # message after them: live, a trailing system message made the model
        # answer with a bare mood word ("SQUINT") or copy the prompt's layout.
        turn_parts.append("LIVE FACTS:\n\n"
                   # Capped: the model runs at 4096 tokens, and a document read
                   # alone can be 4000 characters (SENIOR_REVIEW.md, M8).
                   + "\n\n".join(f if len(f) <= 2400 else f[:2400] + " ...(cut)" for f in live_facts))
    if turn_parts and (is_introspective or needs_source_rules):
        from brain.memory_context import TURN_RULES  # the full prompt doesn't carry them
        turn_parts.insert(0, TURN_RULES)
    messages: List[Dict] = [{"role": "system", "content": personalize(system)}]
    past = _compact_history_for_llm(history)
    # Only a real reading this turn hides MBUDDY's earlier replies. The action
    # log is on almost every turn; filtering on it made MBUDDY forget what it
    # had just said ("I didn't do anything").
    # A write (add_task) is not a reading: live, filtering after one made
    # MBUDDY re-answer an older request (docs/research/lag_and_stubborn_study.md).
    read_tools = {"pc_status", "get_weather", "weather", "news", "web_search", "get_time", "list_tasks",
                  "read_file", "list_folder", "find_apps", "list_abilities"}
    ran = {n for n, _a, _r in (agent_results or [])}
    if behavior_decision and behavior_decision.tool:
        ran.add(behavior_decision.tool)
    fresh_read = bool(checked_block or real_volume is not None or ran & read_tools)
    if fresh_read and os.getenv("MBUDDY_HISTORY_FILTER", "1") != "0":
        # Measured 2026-09-27 on the "Riptide" case: with its own earlier
        # replies the reply repeated its false claim 0/5; without them it
        # named the real song 5/5. Plain chat keeps the whole conversation.
        from .agent import last_question
        kept = [m for m in past if m["role"] == "user"]
        if past and past[-1]["role"] == "assistant" and last_question(past[-1]["content"]):
            kept.append({"role": "assistant", "content": last_question(past[-1]["content"])})
        past = kept
    messages += past
    if turn_parts:
        messages.append({"role": "system", "content": personalize("\n\n".join(turn_parts))})
    messages.append({"role": "user", "content": user_content})

    # Diagnostic: log the last 3 turns of history being sent
    hist_snippet = [f"{m['role']}: {m['content'][:40]}..." for m in messages[-4:]]
    payload_chars = sum(len(str(m.get("content", ""))) for m in messages)
    log[0](f"[Brain] Sending {len(messages)} messages / {payload_chars} chars. Recents: {hist_snippet}")

    max_tok = reply_max_tokens(user_text, energy=state.energy)
    max_sentences = reply_max_sentences(user_text)
    # A plain lookup gets a plain answer. "What time is it?" drew "It's 7:46
    # PM, just past dinner time. You ready to stretch or head to bed?"
    if {"clock", "get_time"} & ({getattr(behavior_decision, "tool", "")} | agent_tools):
        max_sentences = 1
    if said:
        max_sentences = 1
    from .agent import says_whole_reply
    if said and says_whole_reply(agent_results):
        # The action's own words are the whole answer ("That's new to me…"):
        # no model sentence after it (live, it repeated the same line, S116).
        whole = ("[HAPPY] " if said_ok else "[NORMAL] ") + " ".join(said)
        store.append({"role": "user", "content": user_text, "at": time.time()})
        store.append({"role": "assistant", "content": strip_emotion_tags(whole) or whole, "at": time.time()})
        yield whole
        return
    temp    = reply_temperature(user_text, energy=state.energy)
    if detected_lang not in ("en", "mixed"):
        temp = min(temp, 0.42)
    if internet_ctx:
        temp = min(temp, 0.25)

    from core import timeline
    timeline.mark("prompt built")

    # ── 5. Streaming with provider fallback ───────────────────
    async def _stream_from(provider_name: str) -> AsyncIterator[str]:
        """Stream from a single provider; raises on failure so caller can fallback."""
        rkey = _request_key(user_text, provider_name)
        if rkey in _ACTIVE_REQUESTS:
            log[2](f"[Router] Duplicate request blocked for provider={provider_name}")
            if detected_lang == "ar":
                yield "[NORMAL] عم عالج نفس الطلب هلأ."
            else:
                yield "[NORMAL] I'm already handling that."
            return

        _ACTIVE_REQUESTS.add(rkey)
        try:
            curr_adapter = get_brain_adapter(provider_name)
            reply = ensure_leading_tag(curr_adapter.stream_chat(messages, max_tok, temp))
            guard_facts = dict(
                # After code said the results, any claim of the model's own is dropped.
                tools_ran="TOOL OUTPUT" in tool_output and not said,
                acted=acted_this_turn and not said,
                memory_text=memory_text_for_guard,
                real_volume=real_volume,
                checked=verifying,
                actions_text=actions_text,
                background=background,
                results_text=tool_output,
            )

            async def plain_fact(kind: str, _dropped: list):
                # The last resort is the fact itself, from MBUDDY's own state.
                fact = _jobs.plain_status() or ("I didn't do anything on the PC just now." if kind == "action"
                                                else "I don't know that about you yet.")
                yield "[NORMAL] " + fact

            async def regenerate(kind: str, dropped: list):
                """Nothing was left of the draft: write it once more, told why and
                what is true, instead of a stub (W1)."""
                why = ("it says you did, are doing or will do something that nothing shows happened"
                       if kind == "action" else
                       "it states something about him or your shared past that isn't in your memory")
                facts = [f for f in (tool_output.strip(), _jobs.status_text(), actions_text, brain_block) if f]
                note = ("You drafted: \"" + " ".join(dropped)[:300] + "\". That can't be said: " + why + ".\n"
                        "What is true:\n" + "\n".join(facts) + "\n"
                        "Answer his message again in one or two sentences, using only what is true. If he asks "
                        "whether you did or know something and you didn't or don't, say so plainly and offer "
                        "what you can do.")
                log[0](f"[Guard] nothing was left of the reply ({kind}); writing it again from the facts")
                again = messages[:-1] + [{"role": "system", "content": note}, messages[-1]]
                second = ensure_leading_tag(curr_adapter.stream_chat(again, max_tok, temp))
                async for piece in truth_guard(limit_sentences(second, max_sentences), on_empty=plain_fact, **guard_facts):
                    yield piece

            guarded = truth_guard(limit_sentences(reply, max_sentences), quiet_if_empty=bool(said),
                                  on_empty=regenerate, **guard_facts)
            if said:
                yield ("[HAPPY] " if said_ok else "[NORMAL] ") + " ".join(said) + " "
                first = True
                async for delta in guarded:
                    if first:
                        delta = re.sub(r"^\s*\[[A-Z]+\]\s*", "", delta)
                        if not delta.strip():
                            continue
                        first = False
                    yield delta
            elif verifying:
                # Say it straight away, then answer from the fresh check. The
                # model's own opening tag is dropped so the reply stays one
                # utterance with one emotion.
                yield "[FOCUSED] Let me check. "
                first = True
                async for delta in guarded:
                    if first:
                        delta = strip_own_check_opener(delta)
                        if not delta.strip():
                            continue
                        first = False
                    yield delta
            else:
                async for delta in guarded:
                    yield delta
            usage = getattr(curr_adapter, "last_usage", None)
            rendered = "".join(f"<{m['role']}>{m.get('content', '')}" for m in messages)
            previous = _LAST_REPLY_PROMPT[0]
            same_system = previous.startswith(f"<system>{messages[0].get('content', '')}<")
            if usage is not None and getattr(usage, "prompt_tokens", None):
                # How much of the prompt Ollama read anew: that is most of the
                # wait before the first words (S6).
                details = getattr(usage, "prompt_tokens_details", None)
                cached = int(getattr(details, "cached_tokens", 0) or 0)
                log[0](f"[Brain] reply prompt {usage.prompt_tokens} tokens: {cached} cached, "
                       f"{usage.prompt_tokens - cached} read anew; reply {usage.completion_tokens} tokens; "
                       f"system prompt unchanged: {'yes' if same_system else 'no'}")
            else:
                # A reply cut at its sentence limit closes the stream before
                # Ollama's count arrives (S6b): estimated from what differs.
                shared = next((i for i, (a, b) in enumerate(zip(previous, rendered)) if a != b),
                              min(len(previous), len(rendered)))
                log[0](f"[Brain] reply prompt ~{len(rendered) / 3.8:.0f} tokens, ~{(len(rendered) - shared) / 3.8:.0f} "
                       f"new since the last reply (estimated: the reply ended before Ollama's count); "
                       f"system prompt unchanged: {'yes' if same_system else 'no'}")
            _LAST_REPLY_PROMPT[0] = rendered
        finally:
            _ACTIVE_REQUESTS.discard(rkey)

    providers = get_available_providers()
    last_full_reply = ""

    if not providers:
        err_msg = "[SAD] My brain provider is not configured right now."
        yield err_msg
        last_full_reply = err_msg

    for p in providers:
        try:
            async for delta in _stream_from(p):
                last_full_reply += delta
                yield delta
            if not reply_matches_language(last_full_reply, detected_lang):
                raise ValueError("reply_language_mismatch")
            break  # Stream completed without error
        except Exception as e:
            if last_full_reply.strip():
                # We already yielded content. Don't failover or append error messages.
                log[2](f"[Router] Post-stream failure (e.g. lang mismatch) in '{p}': {e}")
                break

            last_full_reply = ""  # Reset on failure so we don't append partial garbage
            if p == providers[-1]:
                # This is the exception a spoken reply gets built from, and
                # until now nothing logged it here — only intermediate
                # providers logged their failure before moving on. With one
                # provider (the common case) that meant a friendly-sounding
                # fallback replaced a real error with no trace of what broke,
                # anywhere.
                log[3](f"[Router] All providers exhausted; last failure in '{p}': {type(e).__name__}: {e}")
                err_msg = _friendly_provider_fallback(e, detected_lang)
                yield err_msg
                last_full_reply = err_msg
            else:
                log[2](f"[Router] Provider '{p}' failed — trying '{providers[providers.index(p)+1]}'…")

    # ── 6. History update (exactly once, after stream completes) ──
    store.append({"role": "user", "content": user_text, "at": time.time()})
    if last_full_reply.strip() and not reply_matches_language(last_full_reply, detected_lang):
        log[2](f"[Router] Wrong-language reply excluded from history for lang={detected_lang}")
        last_full_reply = fallback_reply(
            "reply_language_mismatch",
            getattr(state, "current", "Normal"),
            lang=detected_lang,
        )
    clean_reply = strip_emotion_tags(last_full_reply) or last_full_reply
    store.append({"role": "assistant", "content": clean_reply, "at": time.time()})
