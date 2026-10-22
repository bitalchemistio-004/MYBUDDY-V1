"""
brain/obsidian_vault.py - prototype: an Obsidian vault as MBUDDY's readable brain (S87).

Plain markdown files, no Obsidian plugin and no network. Obsidian does not need
to be running (or even installed): it is only the viewer and editor the user
can open on the same folder.

What lives where (see docs/research/obsidian_brain.md):
    <vault>/MBUDDY/People, Facts, Skills, Episodes, Preferences
Each note has YAML frontmatter holding the learning numbers (strength, uses,
successes, failures, last_used, status, source, confidence) and a body.

Rules this module keeps in code:
- It writes only inside <vault>/MBUDDY. Every path is resolved and checked.
- The body has one MBUDDY-managed region between two HTML comment markers.
  Text outside the markers is the user's and is never changed. If the user
  edits inside the region (its hash no longer matches) or deletes the
  markers, MBUDDY stops writing to that body for good (`user_edited: true`).
- Frontmatter is merged, never replaced: keys the user added stay, values
  already in the file win for descriptive keys, lists are unioned, and the
  learning numbers are read from the file before each change, so a number the
  user typed in Obsidian is the one that gets reinforced or decayed.
- A note whose frontmatter does not parse is treated as the user's and is not
  written at all.
- Nothing is deleted: forgetting sets `status: archived`.

The learning-rate numbers come from docs/BRAIN_PLAN.md ("Growing a brain");
see LearningRate.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import yaml
import numpy as np
import networkx as nx

ROOT_FOLDER = "MBUDDY"
KINDS = ("People", "Facts", "Skills", "Episodes", "Preferences", "Knowledge", "Apps", "Debugging experience")

HEADER = "FROM YOUR NOTES (what you have learned; the user can edit these):"
START_MARK = "<!-- mbuddy:start -->"
END_MARK = "<!-- mbuddy:end -->"

# Learning keys MBUDDY computes. Everything else in the frontmatter is descriptive.
LEARNING_KEYS = ("strength", "uses", "successes", "failures", "streak", "last_used",
                 "last_decay", "status")
# Frontmatter key order when a note is written (unknown keys follow, as found).
KEY_ORDER = ("type", "status", "strength", "confidence", "uses", "successes", "failures",
             "streak", "last_used", "last_decay", "created", "source", "user_approved",
             "user_edited", "pinned", "tags", "aliases", "links", "mbuddy_hash")

TIME_FMT = "%Y-%m-%dT%H:%M"
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\[\]#^]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_WORD = re.compile(r"[\w']+", re.UNICODE)
_STOP = {
    "the", "and", "for", "you", "your", "are", "was", "what", "when", "where", "how", "who",
    "why", "can", "could", "would", "should", "does", "did", "have", "has", "had", "this",
    "that", "with", "from", "into", "about", "please", "just", "some", "any", "tell", "me",
    "my", "is", "it", "a", "an", "of", "to", "in", "on", "at", "do", "i", "we", "be", "or",
}


@dataclass(frozen=True)
class LearningRate:
    """How fast one experience changes what MBUDDY shows the model (BRAIN_PLAN, S67)."""
    start: float = 0.5              # a new verified success
    start_confirmed: float = 1.0    # a user-confirmed skill, or a note the user wrote
    reinforce: float = 0.1          # per verified success after the note was used
    penalty: float = 0.3            # per failure/correction after it was used
    weekly_decay: float = 0.05      # 5% per week without use, applied continuously
    show_threshold: float = 0.4     # only notes at or above this reach the prompt
    run_threshold: float = 0.7      # a skill replays its saved steps only above this
    archive_threshold: float = 0.1  # below this: archived, never shown, never deleted
    promote_streak: int = 3         # uncorrected successes in a row: candidate -> known
    ceiling: float = 1.0


DEFAULT_RATE = LearningRate()


# --------------------------------------------------------------------- helpers

def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _fmt(t: datetime) -> str:
    return t.strftime(TIME_FMT)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    for fmt in (TIME_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for qwen3 without calling the model.

    English runs about 4 characters per token with this tokenizer family; 3.5
    leaves a margin. Arabic and other non-ASCII script is costlier, counted at
    1.5 characters per token. Over-estimating only wastes a little budget;
    under-estimating would overflow the 4096 context.
    """
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_chars
    return math.ceil(ascii_chars / 3.5 + other / 1.5)


def safe_name(title: str) -> str:
    """A Windows- and Obsidian-safe file stem for a note title."""
    name = _BAD_NAME.sub(" ", str(title or "")).strip()
    name = re.sub(r"\s+", " ", name).strip(" .")[:80].strip(" .")
    if not name or name.lower() in _RESERVED:
        name = f"note {name}".strip()
    return name


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def split_note(text: str) -> tuple[dict | None, str]:
    """(frontmatter, body). Frontmatter is None when present but unparseable."""
    text = text.lstrip("﻿")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    raw = text[4:end]
    rest = text[end + 4:]
    if rest.startswith("\n"):
        rest = rest[1:]
    try:
        meta = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError:
        return None, text
    if not isinstance(meta, dict):
        return None, text
    return meta, rest


def join_note(meta: dict, body: str) -> str:
    ordered = {k: meta[k] for k in KEY_ORDER if k in meta}
    ordered.update({k: v for k, v in meta.items() if k not in ordered})
    fm = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{fm}---\n{body}"


def _managed_region(body: str) -> tuple[int, int] | None:
    """Character span of the text between the markers, or None if markers are gone."""
    s = body.find(START_MARK)
    e = body.find(END_MARK, s + len(START_MARK)) if s != -1 else -1
    if s == -1 or e == -1:
        return None
    return s + len(START_MARK), e


def links_in(body: str, meta: dict | None = None) -> list[str]:
    found = [m.group(1).strip() for m in _WIKILINK.finditer(body or "")]
    for extra in (meta or {}).get("links") or []:
        found.extend(m.group(1).strip() for m in _WIKILINK.finditer(str(extra)))
        if "[[" not in str(extra):
            found.append(str(extra).strip())
    seen, out = set(), []
    for name in found:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def plain(text: str) -> str:
    """What the model reads from a note: no wiki-link brackets or heading marks,
    which a small model gets distracted by (S104). [[Note|alias]] -> alias."""
    text = _WIKILINK.sub(lambda m: m.group(0).split("|", 1)[1].rstrip("]") if "|" in m.group(0) else m.group(1), text)
    return re.sub(r"(?m)^\s*#{1,6}\s*(.+?)\s*$", r"\1:", text)


def _stem(word: str) -> str:
    """A word without its possessive, plural or -ing/-ed ending, so "cat's"
    and "cats" meet "cat", "works" meets "work", "deleting" meets "delete".
    Deliberately small: a wrong merge would match notes that have nothing to
    do with the question."""
    word = word.strip("'")
    for ending in ("'s", "s'"):
        if word.endswith(ending):
            word = word[: -len(ending)]
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    # "deleting", "deleted" and "delete" are one word; short words are left alone.
    for ending in ("ing", "ed"):
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            word = word[: -len(ending)]
            break
    if len(word) >= 5 and word.endswith("e"):
        word = word[:-1]
    return word


def _words(text: str) -> set[str]:
    out = set()
    for raw in _WORD.findall(text.lower()):
        word = _stem(raw)
        if len(word) > 2 and word not in _STOP and raw.strip("'") not in _STOP:
            out.add(word)
    return out


# ------------------------------------------------------------------------ vault

class OutsideVaultError(ValueError):
    """Raised when a write would land outside <vault>/MBUDDY."""


class Vault:
    def __init__(self, root: str | os.PathLike, rate: LearningRate = DEFAULT_RATE) -> None:
        self.root = Path(root).resolve()
        self.base = (self.root / ROOT_FOLDER).resolve()
        self.rate = rate
        self._cache: dict[Path, tuple[float, dict | None, str]] = {}
        # Meaning, not only words: a function texts -> unit vectors (or None
        # when it cannot). Unset, retrieval is by words alone. brain_store
        # sets it to the embedder the library already uses.
        self.embed = None
        self._vectors: dict[Path, tuple[float, object]] = {}
        self._vectors_loaded = False

    def _vectors_cache_file(self) -> Path:
        return self.base / ".vectors_cache.npz"

    def _load_disk_vectors(self) -> None:
        if self._vectors_loaded:
            return
        path = self._vectors_cache_file()
        if not path.is_file():
            self._vectors_loaded = True
            return
        try:
            import json
            data = np.load(path, allow_pickle=False)
            meta = json.loads(str(data["meta"]))
            vectors = data["vectors"]
            for i, item in enumerate(meta):
                p = Path(item["path"])
                mtime = float(item["mtime"])
                if p.is_file() and p.stat().st_mtime == mtime:
                    self._vectors[p] = (mtime, vectors[i])
        except Exception:
            pass
        self._vectors_loaded = True

    def _save_disk_vectors(self) -> None:
        path = self._vectors_cache_file()
        if not self._vectors:
            return
        try:
            import json
            meta, vecs = [], []
            for p, (mtime, vec) in self._vectors.items():
                if p.is_file():
                    meta.append({"path": str(p), "mtime": mtime})
                    vecs.append(vec)
            if vecs:
                np.savez(path, vectors=np.array(vecs, dtype=np.float32), meta=np.array(json.dumps(meta)))
        except Exception:
            pass

    # ---- layout and safety

    def init(self) -> Path:
        """Create <vault>/MBUDDY and its kind folders; idempotent, touches nothing else."""
        for kind in KINDS:
            (self.base / kind).mkdir(parents=True, exist_ok=True)
        readme = self.base / "About this folder.md"
        if not readme.exists():
            self._atomic_write(readme, (
                "MBUDDY keeps what it has learned here, as plain notes you can read and edit.\n\n"
                "- The numbers at the top of each note (strength, uses, successes) are how much "
                "MBUDDY trusts it. Raise or lower them and MBUDDY will use your value.\n"
                "- Text you write outside the `mbuddy` markers is never changed by MBUDDY. "
                "If you edit inside them, MBUDDY stops rewriting that note.\n"
                "- Set `status: archived` to make MBUDDY stop using a note. Nothing is deleted.\n"
                "- MBUDDY never writes outside this folder.\n"))
        return self.base

    def path_for(self, kind: str, title: str) -> Path:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
        return self._inside(self.base / kind / f"{safe_name(title)}.md")

    def _inside(self, path: Path) -> Path:
        resolved = Path(path).resolve()
        if resolved != self.base and self.base not in resolved.parents:
            raise OutsideVaultError(f"{resolved} is outside {self.base}")
        return resolved

    def _atomic_write(self, path: Path, text: str, expect_mtime: float | None = None) -> None:
        """Write through a temp file and os.replace, refusing if the file changed meanwhile."""
        path = self._inside(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if expect_mtime is not None and path.exists() and path.stat().st_mtime != expect_mtime:
            raise ConcurrentEditError(str(path))
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".mbuddy-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self._cache.pop(path, None)

    # ---- reading

    def read(self, path: Path) -> tuple[dict | None, str, float]:
        """(frontmatter or None if invalid, body, mtime); cached by mtime."""
        path = Path(path).resolve()
        mtime = path.stat().st_mtime
        hit = self._cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1], hit[2], mtime
        meta, body = split_note(path.read_text(encoding="utf-8"))
        self._cache[path] = (mtime, meta, body)
        return meta, body, mtime

    def notes(self) -> list[Path]:
        """MBUDDY's notes: inside the kind folders only. The "About this folder"
        readme at the top is for the user, not a note to recall or count."""
        if not self.base.exists():
            return []
        # The reference library (pages MBUDDY read) is looked up per task, not
        # recalled every turn or listed as notes (S117).
        return sorted(p for p in self.base.rglob("*.md") if not p.name.startswith(".") and p.parent != self.base
                      and "Library" not in p.relative_to(self.base).parts[:1])

    # ---- writing

    def upsert(self, kind: str, title: str, body: str = "", **meta: Any) -> Path:
        """Create or update a note, merging frontmatter and keeping the user's body text.

        `body` is MBUDDY's content for its managed region. Keyword args are
        frontmatter fields (tags, aliases, links, source, confidence, ...).
        """
        path = self.path_for(kind, title)
        for _attempt in range(3):
            try:
                return self._upsert_once(path, kind, body, meta)
            except ConcurrentEditError:
                continue  # the user saved in between: re-read and merge again
        raise ConcurrentEditError(str(path))

    def _upsert_once(self, path: Path, kind: str, body: str, new: dict) -> Path:
        now = _fmt(_now())
        if not path.exists():
            source = new.get("source", "mbuddy")
            start = self.rate.start_confirmed if source == "user" or new.get("user_approved") \
                else self.rate.start
            meta = {"type": kind.lower().rstrip("s") if kind != "People" else "person",
                    "status": "known" if source == "user" else "candidate",
                    "strength": start, "confidence": 0.55, "uses": 0, "successes": 0,
                    "failures": 0, "streak": 0, "last_used": None, "created": now,
                    "source": source}
            for key, value in new.items():
                meta[key] = value
            region = f"\n{body.strip()}\n" if body.strip() else "\n"
            meta["mbuddy_hash"] = _hash(region)
            self._atomic_write(path, join_note(meta, f"{START_MARK}{region}{END_MARK}\n"),
                               expect_mtime=None)
            return path

        meta, old_body, mtime = self.read(path)
        if meta is None:
            return path  # broken frontmatter: the user's file now; do not touch it
        merged = dict(meta)
        for key, value in new.items():
            if key in LEARNING_KEYS:
                continue  # learning numbers change only through reinforce/decay
            if isinstance(value, list):
                existing = merged.get(key) or []
                if not isinstance(existing, list):
                    existing = [existing]
                merged[key] = existing + [v for v in value if v not in existing]
            elif key not in merged or merged[key] in (None, ""):
                merged[key] = value
            # an existing scalar wins: the user may have changed it in Obsidian
        new_body = old_body
        span = _managed_region(old_body)
        if span is None or merged.get("user_edited"):
            merged["user_edited"] = True
        else:
            current = old_body[span[0]:span[1]]
            if merged.get("mbuddy_hash") and _hash(current) != merged["mbuddy_hash"]:
                merged["user_edited"] = True  # the user rewrote MBUDDY's part: it is theirs now
            elif body.strip():
                region = f"\n{body.strip()}\n"
                new_body = old_body[:span[0]] + region + old_body[span[1]:]
                merged["mbuddy_hash"] = _hash(region)
        if merged != meta or new_body != old_body:
            self._atomic_write(path, join_note(merged, new_body), expect_mtime=mtime)
        return path

    def _update_meta(self, path: Path, change) -> dict | None:
        """Read-modify-write the frontmatter only; the body is written back unchanged."""
        path = self._inside(path)
        for _attempt in range(3):
            meta, body, mtime = self.read(path)
            if meta is None:
                return None
            updated = change(dict(meta))
            if updated == meta:
                return meta
            try:
                self._atomic_write(path, join_note(updated, body), expect_mtime=mtime)
                return updated
            except ConcurrentEditError:
                continue
        raise ConcurrentEditError(str(path))

    # ---- learning rate

    def reinforce(self, path: Path, success: bool | None = True, now: datetime | None = None) -> dict | None:
        """Record that a note was used; success raises strength, a failure lowers
        it more. success=None records the use only: nothing checked whether the
        note helped, so its strength doesn't move (U2)."""
        r = self.rate
        stamp = _fmt(now or _now())

        def change(m: dict) -> dict:
            s = float(m.get("strength", r.start))
            m["uses"] = int(m.get("uses", 0)) + 1
            m["last_used"] = stamp
            m["last_decay"] = stamp
            if success is None:
                return m
            if success:
                m["successes"] = int(m.get("successes", 0)) + 1
                m["streak"] = int(m.get("streak", 0)) + 1
                s = min(r.ceiling, s + r.reinforce)
                if m.get("status") == "unverified":  # a code-checked success earns it back
                    m["status"] = "candidate"
                    m["verified"] = stamp
            else:
                m["failures"] = int(m.get("failures", 0)) + 1
                m["streak"] = 0
                s = max(0.0, s - r.penalty)
            m["strength"] = round(s, 4)
            return self._apply_status(m)

        return self._update_meta(path, change)

    def mark_checked(self, path: Path, checked: bool, now: datetime | None = None) -> dict | None:
        """Whether a note's result was checked by code (U2). Checked: verified,
        and a note marked unverified becomes a candidate again. Not checked:
        status unverified and strength at most 0.3, below what recall shows."""
        stamp = _fmt(now or _now())

        def change(m: dict) -> dict:
            if checked:
                m["verified"] = stamp
                if m.get("status") == "unverified":
                    m["status"] = "candidate"
            else:
                m["status"] = "unverified"
                m["strength"] = round(min(float(m.get("strength", self.rate.start)), 0.3), 4)
                m.pop("verified", None)
            return m
        return self._update_meta(path, change)

    def decay_all(self, days: float | None = None, now: datetime | None = None) -> int:
        """Apply time decay to every note; returns how many changed.

        With `days`, that many days are applied to every note (a fixed step, for
        a scheduled job or tests). Without it, each note decays by the time since
        its own `last_decay` (or `last_used`/`created`), so running it twice in a
        row changes nothing the second time.
        """
        r = self.rate
        now = now or _now()
        changed = 0
        for path in self.notes():
            def change(m: dict) -> dict:
                if "strength" not in m or m.get("status") == "archived":
                    return m
                if days is not None:
                    elapsed = float(days)
                else:
                    since = (_parse_time(m.get("last_decay")) or _parse_time(m.get("last_used"))
                             or _parse_time(m.get("created")))
                    if since is None:
                        return m
                    elapsed = max(0.0, (now - since).total_seconds() / 86400.0)
                if elapsed <= 0:
                    return m
                s = float(m["strength"]) * (1.0 - r.weekly_decay) ** (elapsed / 7.0)
                if m.get("pinned") or m.get("source") == "user":
                    s = max(s, r.show_threshold)  # what the user wrote never fades out
                m["strength"] = round(s, 4)
                m["last_decay"] = _fmt(now)
                return self._apply_status(m)

            before = self.read(path)[0]
            after = self._update_meta(path, change)
            if after is not None and after != before:
                changed += 1
        return changed

    def _apply_status(self, m: dict) -> dict:
        """Promote, demote or archive by threshold. Skills also need the user's approval."""
        r = self.rate
        s = float(m.get("strength", 0.0))
        status = m.get("status", "candidate")
        if s < r.archive_threshold and not (m.get("pinned") or m.get("source") == "user"):
            m["status"] = "archived"
            return m
        if status == "candidate" and int(m.get("streak", 0)) >= r.promote_streak \
                and s >= r.show_threshold:
            if m.get("type") == "skill" and not m.get("user_approved"):
                m["ready_for_review"] = True  # shown in the Skills screen, not promoted by code
            else:
                m["status"] = "known"
        elif status == "known" and s < r.show_threshold and m.get("source") != "user":
            m["status"] = "candidate"  # a known note that keeps failing has to earn it back
        return m

    def can_replay(self, path: Path) -> bool:
        """A saved skill may replay its steps only when approved, known and strong enough."""
        meta, _body, _ = self.read(path)
        return bool(meta and meta.get("type") == "skill" and meta.get("user_approved")
                    and meta.get("status") == "known"
                    and float(meta.get("strength", 0)) >= self.rate.run_threshold)

    # ---- skills and episodes

    def export_skill(self, title: str, steps: Iterable[dict | str], when: str = "",
                     confirmed_by_user: bool = False, tags: list[str] | None = None) -> Path:
        """Save learned steps as a Skills note (a numbered list the user can read and edit)."""
        lines = []
        if when:
            lines += ["## When", when.strip(), ""]
        lines.append("## Steps")
        for i, step in enumerate(steps, 1):
            if isinstance(step, dict):
                tool = step.get("tool", "step")
                args = ", ".join(f"{k}={v!r}" for k, v in (step.get("args") or {}).items())
                text = f"{i}. `{tool}({args})`"
                if step.get("say"):
                    text += f" - {step['say']}"
                if step.get("check"):
                    text += f" (check: {step['check']})"
            else:
                text = f"{i}. {step}"
            lines.append(text)
        meta: dict[str, Any] = {"tags": ["mbuddy/skill", *(tags or [])]}
        if confirmed_by_user:
            meta["user_approved"] = True
            meta["source"] = "user-confirmed"
        path = self.upsert("Skills", title, "\n".join(lines), **meta)
        if confirmed_by_user:
            # BRAIN_PLAN: a user-confirmed skill starts at 1.0 and is known.
            self._update_meta(path, lambda m: {**m, "user_approved": True,
                                               "strength": max(float(m.get("strength", 0)),
                                                               self.rate.start_confirmed),
                                               "status": "known"})
        return path

    def add_episode(self, day: datetime, events: list[str]) -> Path:
        """Daily note from the shared timeline (real actions only); appends new lines."""
        title = day.strftime("%Y-%m-%d")
        path = self.path_for("Episodes", title)
        existing: list[str] = []
        if path.exists():
            meta, body, _ = self.read(path)
            span = _managed_region(body) if meta is not None else None
            if span:
                existing = [ln for ln in body[span[0]:span[1]].strip().splitlines() if ln.strip()]
        lines = existing + [f"- {e}" for e in events if f"- {e}" not in existing]
        return self.upsert("Episodes", title, "\n".join(lines), tags=["mbuddy/episode"])

    # ---- retrieval

    def _eligible(self, meta: dict | None) -> bool:
        if meta is None:
            return False
        if meta.get("status") == "archived":
            return False
        if meta.get("source") == "user" or meta.get("pinned"):
            return True
        return float(meta.get("strength", self.rate.start)) >= self.rate.show_threshold

    # How close in meaning a note must be to a question to count (cosine of
    # the embeddings). Measured on everyday sentences that must match nothing
    # ("what time is it?" reached 0.38 against an unrelated note) and on
    # questions whose answer is in a note (0.39 to 0.70).
    SEM_STRONG = 0.42    # meaning alone is enough
    SEM_WITH_WORD = 0.33  # meaning plus at least one shared word
    # By words alone (a rule's trigger words are often far in meaning from the
    # request that triggers it: "jot down my packing list" scored 0.09 against
    # the note about jotting down lists): most of the weight of the query's
    # words that the vault knows at all, at least two of them, one of them a
    # word that few notes have.
    WORD_SHARE = 0.6
    RARE_IN = 0.15       # "few notes": at most this share of them (at least one)

    def _build_graph(self, index: dict[str, dict]) -> nx.DiGraph:
        G = nx.DiGraph()
        for key, n in index.items():
            meta = n.get("meta") or {}
            G.add_node(key, title=n["title"], kind=n["kind"], strength=float(meta.get("strength", self.rate.start)))
            for target in n.get("links") or []:
                if target in index:
                    G.add_edge(key, target)
        return G

    def _graph_scores(self, G: nx.DiGraph, seeds: dict[str, float]) -> dict[str, float]:
        if not G or not seeds:
            return {}
        scores = dict(seeds)
        try:
            for seed_key, seed_score in list(seeds.items()):
                if seed_key not in G:
                    continue
                lengths = nx.single_source_shortest_path_length(G.to_undirected(), seed_key, cutoff=2)
                for node, dist in lengths.items():
                    if node == seed_key or node not in G:
                        continue
                    decay = 0.5 if dist == 1 else 0.25
                    node_strength = G.nodes[node].get("strength", 0.5)
                    boost = seed_score * decay * node_strength
                    scores[node] = max(scores.get(node, 0.0), boost)
        except Exception:
            pass
        return scores

    def _meaning(self, query: str, index: dict[str, dict]) -> dict[str, float]:
        """How close each note is to the query in meaning (cosine); {} when
        there is no embedder or it fails. A note's vector is kept until the
        note changes."""
        if self.embed is None or not index:
            return {}
        self._load_disk_vectors()
        try:
            fresh = []
            for key, n in index.items():
                mtime = n["path"].stat().st_mtime
                cached = self._vectors.get(n["path"])
                if cached is None or cached[0] != mtime:
                    fresh.append((key, n["path"], mtime, f"{n['title']}. {plain(n['text'])[:400]}"))
            if fresh:
                vectors = self.embed([f[3] for f in fresh])
                if vectors is None:
                    return {}
                for (key, path, mtime, _), vector in zip(fresh, vectors):
                    self._vectors[path] = (mtime, vector)
                self._save_disk_vectors()
            q = self.embed([query])
            if q is None:
                return {}
            return {key: float(self._vectors[n["path"]][1] @ q[0]) for key, n in index.items() if n["path"] in self._vectors}
        except Exception:
            return {}

    def retrieve(self, query: str, token_budget: int = 300, max_notes: int = 5,
                 snippet_chars: int = 280, min_match: float = 0.0) -> list[dict]:
        """Top notes for a query within a token budget: words and meaning,
        then one link hop.

        A note counts when enough of the query's words are in it (title x3,
        tags/aliases x2, body x1, against `min_match`), or when it is close in
        meaning, or when most of the query's words that the vault knows at all
        are in it, one of them a word few notes have. Score = (words + meaning)
        scaled by strength. Links and NetworkX graph proximity propagate scores.
        """
        q = _words(query)
        if not q or token_budget <= 0:
            return []
        index: dict[str, dict] = {}
        for path in self.notes():
            meta, body, _ = self.read(path)
            if not self._eligible(meta):
                continue
            text = body.replace(START_MARK, "").replace(END_MARK, "").strip()
            tags = " ".join(str(t) for t in (meta.get("tags") or []) + (meta.get("aliases") or []))
            index[path.stem.lower()] = {"path": path, "title": path.stem, "kind": path.parent.name,
                                        "meta": meta, "text": text,
                                        "links": [l.lower() for l in links_in(body, meta)],
                                        "w_title": _words(path.stem), "w_tags": _words(tags),
                                        "w_body": _words(text)}
        scores: dict[str, float] = {}
        meaning = self._meaning(query, index)
        vocabulary = set().union(*(n["w_title"] | n["w_tags"] | n["w_body"] for n in index.values())) if index else set()
        known = q & vocabulary   # the query's words that some note has
        count = {w: sum(1 for n in index.values() if w in n["w_title"] or w in n["w_tags"] or w in n["w_body"]) for w in known}
        weight = {w: math.log((len(index) + 1) / (count[w] + 0.5)) for w in known}   # a word many notes have says little
        rare = max(1, round(self.RARE_IN * len(index)))
        for key, n in index.items():
            words = n["w_title"] | n["w_tags"] | n["w_body"]
            hits = 3 * len(q & n["w_title"]) + 2 * len(q & n["w_tags"]) + len(q & n["w_body"])
            # min_match: how much of the query a note must match (title words
            # count 3, tags 2, body 1), before strength scales it.
            by_words = hits / (3 * len(q))
            close = meaning.get(key, 0.0)
            shared = len(q & words)
            counts = bool(hits) and by_words >= min_match
            if meaning:
                counts = counts or close >= self.SEM_STRONG or (shared >= 1 and close >= self.SEM_WITH_WORD)
            if not counts and min_match > 0 and shared >= 2:
                both = q & words
                share = sum(weight[w] for w in both) / (sum(weight.values()) or 1.0)
                counts = share >= self.WORD_SHARE and any(count[w] <= rare for w in both)
            if counts:
                scores[key] = (by_words + max(0.0, close)) * float(n["meta"].get("strength", self.rate.start))
        for key in sorted(scores, key=scores.get, reverse=True)[:max_notes]:
            seed = scores[key]
            neighbours = set(index[key]["links"]) | {k for k, n in index.items() if key in n["links"]}
            for nb in neighbours:
                if nb in index and nb != key:
                    meta_nb = index[nb].get("meta") or {}
                    hop = 0.5 * seed * float(meta_nb.get("strength", self.rate.start))
                    scores[nb] = max(scores.get(nb, 0.0), hop)
        G = self._build_graph(index)
        scores = self._graph_scores(G, scores)
        picked, used = [], estimate_tokens(HEADER) + 1  # the header is part of the budget
        for key in sorted(scores, key=lambda k: (-scores[k], k)):
            if len(picked) >= max_notes:
                break
            n = index[key]
            snippet = re.sub(r"\s+", " ", plain(n["text"]))[:snippet_chars]
            line = f"- [{n['kind']}] {n['title']}: {snippet}".rstrip(": ")
            cost = estimate_tokens(line) + 1  # +1 for the newline
            remaining = token_budget - used
            if cost > remaining:
                if remaining < 16:
                    continue
                while line and estimate_tokens(line) + 1 > remaining:
                    line = line[: max(0, len(line) - 12)]
                line = line.rstrip() + "..."
                cost = estimate_tokens(line) + 1
                if cost > remaining:
                    continue
            picked.append({"path": n["path"], "title": n["title"], "kind": n["kind"],
                           "score": round(scores[key], 4), "line": line, "tokens": cost, "meta": n["meta"]})
            used += cost
        return picked

    @staticmethod
    def render(results: list[dict]) -> str:
        """Prompt block for the picked notes, or "" when nothing matched."""
        if not results:
            return ""
        return HEADER + "\n" + "\n".join(r["line"] for r in results)


class ConcurrentEditError(RuntimeError):
    """The file changed between MBUDDY's read and its write (the user saved in Obsidian)."""
