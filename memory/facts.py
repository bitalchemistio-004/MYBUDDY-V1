"""
memory/facts.py — deduplicated, confidence-weighted facts about the user.

Facts used to be appended blindly and then trimmed by slicing the tail:

    memory["core_facts"].extend(core_new)
    memory["core_facts"] = memory["core_facts"][-60:]

Two things went wrong with that. Nothing checked whether a fact was already
known, so "the user can speak Spanish" was stored six times — and repetition in a
fact list reads to the model as emphasis, so MBUDDY was told something false,
insistently. And `compress_identity_snapshot()` then cut the list to the last
20, deleting real history every tenth turn in exchange for a lossy prose
summary.

This module keeps the stored shape — `core_facts` stays a list of plain
strings, so every existing reader keeps working — and holds the bookkeeping
beside it in `fact_meta`. A repeated fact becomes one entry that gets stronger
rather than a second copy, and facts are ranked by strength so the handful that
reach the prompt are the ones that matter rather than the ones that arrived
last.
"""
from __future__ import annotations

import re
import time
from typing import Any

# Where the per-fact bookkeeping lives, keyed by the normalised fact text.
META_KEY = "fact_meta"

# Facts are cheap on disk and expensive in a prompt, so the store is large and
# the retrieval is small. This cap exists only to stop unbounded growth; at a
# few facts a day it is years away.
MAX_FACTS = 400

# Two facts whose word sets overlap this much are treated as the same fact
# reworded. Set high deliberately: merging distinct facts loses information,
# which is worse than keeping a near-duplicate.
NEAR_DUPLICATE_OVERLAP = 0.85

# A fact stated once is plausible; repetition across separate exchanges makes it
# more credible, but never certain — nothing here is ever proven.
CONFIDENCE_ON_FIRST_SIGHT = 0.55
CONFIDENCE_CEILING = 0.95

_PUNCT = re.compile(r"[^\w\s؀-ۿ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
_ARABIC_TATWEEL = re.compile(r"ـ+")
_ARABIC_DIACRITICS = re.compile(r"[ً-ْٰ]")


def normalise_fact(text: Any) -> str:
    """
    Reduce a fact to a comparison key.

    Case, punctuation and spacing carry no meaning here, and Arabic adds two
    more: tatweel is decorative stretching, and diacritics are usually omitted,
    so the same fact typed twice can differ in both without differing at all.

    Args:
        text: the fact as written

    Returns:
        A normalised key, or "" when there is nothing to compare.
    """
    value = str(text or "").strip().lower()
    if not value:
        return ""
    value = _ARABIC_TATWEEL.sub("", value)
    value = _ARABIC_DIACRITICS.sub("", value)
    # Alef variants are written inconsistently and mean the same letter.
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ة", "ه")  # taa marbuta / haa
    value = value.replace("ي", "ى")  # yaa / alef maqsura
    value = _PUNCT.sub(" ", value)
    return _SPACES.sub(" ", value).strip()


def _overlap(a: str, b: str) -> float:
    """Jaccard overlap of two normalised facts' word sets."""
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _meta(memory: dict) -> dict:
    """The bookkeeping map, created on first use."""
    meta = memory.get(META_KEY)
    if not isinstance(meta, dict):
        meta = {}
        memory[META_KEY] = meta
    return meta


def find_existing(memory: dict, text: str) -> str | None:
    """
    The normalised key of an already-known fact matching `text`, if any.

    Checks for an exact normalised match first, then for a rewording close
    enough to be the same fact.
    """
    key = normalise_fact(text)
    if not key:
        return None

    meta = _meta(memory)
    if key in meta:
        return key

    for known in meta:
        if _overlap(key, known) >= NEAR_DUPLICATE_OVERLAP:
            return known
    return None


def remember_fact(memory: dict, text: Any, confidence: float | None = None) -> str:
    """
    Store a fact, or strengthen it if it is already known.

    Args:
        memory: the memory dict
        text: the fact as stated
        confidence: initial confidence for a genuinely new fact; defaults to
            `CONFIDENCE_ON_FIRST_SIGHT`

    Returns:
        "new" when the fact was not known, "replaced" when it is a new name that
        replaced the old one, "reinforced" when it was already known and has been
        strengthened, or "rejected" when there was nothing usable to store.
    """
    clean = str(text or "").strip()
    key = normalise_fact(clean)
    if not key or len(key) < 3:
        return "rejected"

    facts = memory.setdefault("core_facts", [])
    meta = _meta(memory)
    now = time.time()

    existing = find_existing(memory, clean)
    if existing is not None:
        entry = meta.setdefault(existing, {})
        entry["mentions"] = int(entry.get("mentions", 1)) + 1
        entry["last_seen"] = now
        # Independent restatements raise confidence, with diminishing returns
        # and a ceiling — repetition is evidence, never proof.
        prior = float(entry.get("confidence", CONFIDENCE_ON_FIRST_SIGHT))
        entry["confidence"] = min(CONFIDENCE_CEILING, prior + (1.0 - prior) * 0.25)
        return "reinforced"

    replaced = replaces_name(memory, clean)
    facts = memory.setdefault("core_facts", [])
    facts.append(clean)
    meta[key] = {
        "text": clean,
        "confidence": float(
            CONFIDENCE_ON_FIRST_SIGHT if confidence is None else confidence
        ),
        "mentions": 1,
        "first_seen": now,
        "last_seen": now,
    }
    prune_facts(memory)
    return "replaced" if replaced else "new"


def fact_strength(memory: dict, text: Any) -> float:
    """
    How much weight a fact deserves, combining confidence and recency.

    A fact mentioned often and recently outranks one stated once a year ago,
    which is what decides the handful that reach the prompt.
    """
    key = find_existing(memory, text)
    if key is None:
        return 0.0
    entry = _meta(memory).get(key, {})
    confidence = float(entry.get("confidence", CONFIDENCE_ON_FIRST_SIGHT))
    age_days = max(0.0, (time.time() - float(entry.get("last_seen", 0))) / 86400.0)
    # Halves roughly every 90 days, so old facts fade from the prompt without
    # ever being deleted from the store.
    recency = 0.5 ** (age_days / 90.0)
    return confidence * (0.6 + 0.4 * recency)


def ranked_facts(memory: dict, limit: int = 6) -> list[str]:
    """
    The strongest facts, best first.

    The prompt previously took the last N facts, so what MBUDDY "knew" was
    whatever happened to be extracted most recently. Ranking means the facts
    that actually characterise the user surface instead.
    """
    facts = list(memory.get("core_facts") or memory.get("facts") or [])
    if not facts:
        return []
    ordered = sorted(facts, key=lambda f: fact_strength(memory, f), reverse=True)
    return ordered[: max(0, limit)]


def prune_facts(memory: dict, keep: int = MAX_FACTS) -> int:
    """
    Bound the store by dropping the weakest facts, not the oldest.

    Slicing the tail threw away whatever was learned first, which is usually
    the most established knowledge. Removing the least-supported facts instead
    keeps the store's best content as it fills.

    Returns:
        How many facts were removed.
    """
    facts = memory.get("core_facts") or []
    if len(facts) <= keep:
        return 0

    ordered = sorted(facts, key=lambda f: fact_strength(memory, f), reverse=True)
    kept = ordered[:keep]
    dropped = ordered[keep:]

    memory["core_facts"] = kept
    meta = _meta(memory)
    for fact in dropped:
        key = normalise_fact(fact)
        meta.pop(key, None)
    return len(dropped)


def adopt_existing_facts(memory: dict) -> int:
    """
    Give already-stored facts their bookkeeping, and collapse old duplicates.

    Facts written before this module existed are bare strings with no metadata
    and, in many cases, several copies. Running this once on load turns them
    into single entries whose mention counts reflect how often they were
    repeated.

    Returns:
        How many duplicate entries were collapsed.
    """
    facts = list(memory.get("core_facts") or [])
    if not facts:
        return 0

    memory["core_facts"] = []
    memory[META_KEY] = {}

    collapsed = 0
    for fact in facts:
        if remember_fact(memory, fact) == "reinforced":
            collapsed += 1
    return collapsed


# ── one name per person ──────────────────────────────────────────────────
# "The user's name is Karim", then "The user's name is Kim, not Karim": both were kept and shown as 'you' (F33).
_NAME_FACT = re.compile(r"\b(?:(?:my|the user'?s|user'?s)\s+name\s+is|i\s+am\s+called|call\s+me"
                        r"|(?:wants|likes|prefers)\s+to\s+be\s+called)\b", re.IGNORECASE)


def replaces_name(memory: dict, text: str) -> list[str]:
    """A new name for the user replaces the old one: the name facts it supersedes (removed from the store)."""
    if not _NAME_FACT.search(text or ""):
        return []
    old = [f for f in memory.get("core_facts") or [] if _NAME_FACT.search(str(f)) and normalise_fact(f) != normalise_fact(text)]
    for fact in old:
        memory["core_facts"].remove(fact)
        _meta(memory).pop(normalise_fact(fact), None)
    return old


# ── preferences: how the user wants MBUDDY to answer or act ──────────────
# 'Always use bullet points' was never stored (F31). Preferences are their own list, shown with Forget on the
# Memory page and given to every reply.
PREFERENCES_KEY = "preferences"
MAX_PREFERENCES = 40


def remember_preference(memory: dict, text: Any) -> str:
    """'new', 'known' or 'rejected'."""
    clean = str(text or "").strip()
    key = normalise_fact(clean)
    if not key or len(key) < 3:
        return "rejected"
    prefs = memory.setdefault(PREFERENCES_KEY, [])
    if any(normalise_fact(p) == key or _overlap(normalise_fact(p), key) >= NEAR_DUPLICATE_OVERLAP for p in prefs):
        return "known"
    replaces_name(memory, clean)   # 'call me Samuel, not Sam' as a preference: the old name fact goes (live 2026-10-20)
    prefs[:] = [p for p in prefs if not (_NAME_FACT.search(p) and _NAME_FACT.search(clean))]
    prefs.append(clean)
    del prefs[:-MAX_PREFERENCES]
    return "new"


def preferences(memory: dict) -> list[str]:
    return [str(p) for p in memory.get(PREFERENCES_KEY) or []]
