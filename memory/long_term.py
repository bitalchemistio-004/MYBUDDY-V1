"""
memory/long_term.py - Persistent memory, fact extraction, and tagging.

The runtime still works with a normal Python dict, but persistence is now
SQLite-backed through memory.sqlite_store.
"""
import asyncio
import json
import re
from datetime import datetime

from brain.llm_router import llm_chat_create
from core.config import GROQ_UTILITY_MODEL
from memory.sqlite_store import default_memory, get_memory_store
import utils.logger as log


def _exchange_too_uncertain_for_memory(user_text: str, reply: str) -> bool:
    """Avoid turning garbled STT / clarification turns into long-term memory."""
    joined = f"{user_text} {reply}".lower()
    uncertain_markers = (
        "please translate",
        "speak in english",
        "trying to understand",
        "what's going on",
        "what is going on",
        "i thought",
        "it seems like",
        "you're referring",
        "not aware of any specific connection",
        "repeat it",
        "came in messy",
        "training data",
        "knowledge cutoff",
        "recommend checking",
        "reliable sports",
        "official fifa",
        "not sure",
        "cannot verify",
        "can't verify",
        "no search results",
        "live search is not available",
        "amara.", "amara org", "subtitles by",
    )
    return any(marker in joined for marker in uncertain_markers)


def _looks_like_question(text: str) -> bool:
    t = text.strip().lower()
    if "?" in t:
        return True
    return bool(re.match(r"^(what|when|where|why|how|who|which|can|could|would|should|do|does|did|is|are|was|were)\b", t))


def _user_text_can_contain_stable_fact(text: str) -> bool:
    t = text.strip().lower()
    if _looks_like_question(t):
        return False
    first_person_fact = re.search(
        r"\b(i|i'm|im|i am|my|we|we're|we are)\b.*\b(am|work|live|have|own|use|build|built|like|prefer|need|want|study|speak|name|project)\b",
        t,
    )
    return bool(first_person_fact)


# Set on a fresh memory made because the stored one couldn't be read: saving it would wipe the real memory.
_UNREAD = "_unread_store"


def load_memory() -> dict:
    """Load memory from SQLite, migrating the old JSON file on first run."""
    try:
        memory = get_memory_store().load()
    except Exception:
        log.warn("SQLite memory load failed - starting fresh, and this fresh memory is never saved over the real one.")
        memory = default_memory()
        memory[_UNREAD] = True
        return memory

    # Facts stored before memory/facts.py existed are bare strings with no
    # bookkeeping, and many are duplicates of each other. Adopting them here
    # collapses the copies and gives each fact a mention count, so ranking
    # reflects the whole history rather than only what is learned from now on.
    try:
        from memory.facts import META_KEY, adopt_existing_facts

        if memory.get("core_facts") and not memory.get(META_KEY):
            before = len(memory["core_facts"])
            collapsed = adopt_existing_facts(memory)
            if collapsed:
                log.mem(
                    f"Adopted {before} stored facts, collapsed {collapsed} duplicates "
                    f"-> {len(memory['core_facts'])} distinct."
                )
    except Exception as exc:
        log.warn(f"Fact adoption skipped: {exc}")

    return memory


async def save_memory(memory: dict) -> None:
    """Async-safe SQLite write - runs blocking I/O in a thread executor."""
    if memory.get(_UNREAD):
        log.warn("Memory not saved: it was started fresh because the real one couldn't be read.")
        return

    def _write() -> None:
        get_memory_store().save(memory)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write)


async def extract_and_save_facts(user_text: str, reply: str, memory: dict) -> None:
    """Extract new stable facts about the user from the exchange and persist them."""
    if _exchange_too_uncertain_for_memory(user_text, reply) or not _user_text_can_contain_stable_fact(user_text):
        return
    try:
        known = json.dumps(
            (memory.get("core_facts") or memory.get("facts", []))[-20:],
            ensure_ascii=False,
        )
        response = await llm_chat_create(
            priority="background",
            model=GROQ_UTILITY_MODEL,
            messages=[{"role": "user", "content": f"""
From what USER_NAME said, extract NEW facts about him.
Text may be Arabic, English, or mixed Lebanese.
Only extract stable first-person facts USER_NAME explicitly states about himself.
Do NOT infer interests, preferences, language ability, or identity from a one-off
question, mistranscription, clarification, or MBUDDY guessing what USER_NAME meant.

Return JSON only:
{{"core": ["stable identity fact"], "noise": ["passing detail"]}}
Return empty arrays if nothing new.
Known: {known}

USER_NAME: {user_text}
"""}],
            max_tokens=150,
            temperature=0.15,
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "")
        data = json.loads(raw)
        core_new = data.get("core", [])
        if core_new:
            from memory.facts import remember_fact

            added, reinforced = [], []
            for fact in core_new:
                outcome = remember_fact(memory, fact)
                if outcome in ("new", "replaced"):
                    added.append(fact)
                elif outcome == "reinforced":
                    reinforced.append(fact)

            if added:
                log.mem(f"Core fact: {added}")
            if reinforced:
                # Worth saying out loud: this is the case that used to create a
                # duplicate and quietly overstate the fact by repeating it.
                log.mem(f"Fact reinforced (already known): {reinforced}")
            if added or reinforced:
                await save_memory(memory)
    except Exception:
        pass


async def tag_emotional_memory(user_text: str, reply: str, memory: dict) -> None:
    """Tag an exchange as emotionally significant if the LLM judges it so."""
    if _exchange_too_uncertain_for_memory(user_text, reply) or _looks_like_question(user_text):
        return
    try:
        response = await llm_chat_create(
            priority="background",
            model=GROQ_UTILITY_MODEL,
            messages=[{"role": "user", "content": f"""
Is what USER_NAME said emotionally significant for MBUDDY's bond with him?
Text may be Arabic, English, or mixed Lebanese.
Describe only what USER_NAME said or felt; add no details he did not say.

USER_NAME: {user_text}

JSON only: {{"significant": true, "emotion": "...", "memory": "one-line"}}
or: {{"significant": false}}
"""}],
            max_tokens=80,
            temperature=0.25,
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "")
        data = json.loads(raw)
        if data.get("significant"):
            memory.setdefault("emotional_memories", []).append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "emotion": data.get("emotion", "Unknown"),
                "memory": data.get("memory", ""),
            })
            memory["emotional_memories"] = memory["emotional_memories"][-40:]
            log.mem(f"Anchor: [{data['emotion']}] {data['memory']}")
            await save_memory(memory)
    except Exception:
        pass


async def compress_identity_snapshot(memory: dict) -> None:
    """Summarise accumulated facts into a dense identity snapshot for the system prompt."""
    try:
        facts = memory.get("core_facts", [])
        emotional = memory.get("emotional_memories", [])
        if len(facts) < 10 and len(emotional) < 5:
            return

        response = await llm_chat_create(
            priority="background",
            model=GROQ_UTILITY_MODEL,
            messages=[{"role": "user", "content": f"""
Compress MBUDDY's knowledge of USER_NAME into a dense identity snapshot.
3-5 sentences in MBUDDY's first-person voice. Dense. No fluff.
Facts: {json.dumps(facts[-30:], ensure_ascii=False)}
Emotional moments: {json.dumps([e['memory'] for e in emotional[-15:]], ensure_ascii=False)}
"""}],
            max_tokens=150,
            temperature=0.4,
        )
        memory["identity_snapshot"] = response.choices[0].message.content.strip()
        # The snapshot is a lossy summary *of* the facts, so it is not a
        # replacement for them. This used to cut the store to the last 20 here,
        # destroying real history every tenth turn; the store is bounded on
        # write by prune_facts() instead.
        await save_memory(memory)
        log.mem("Identity compressed.")
    except Exception:
        pass
