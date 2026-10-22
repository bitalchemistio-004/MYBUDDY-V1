"""
brain/brain_store.py - MBUDDY's brain: an Obsidian vault it checks every turn and learns into.

S101: the user installed Obsidian; the vault it has open (%APPDATA%\\obsidian\\
obsidian.json) is linked automatically, unless the user picks another one or
unlinks it in the app. MBUDDY writes only inside <vault>/MBUDDY
(brain/obsidian_vault.py resolves and checks every path).

S102, the learning loop the user described:
- Every turn the brain is checked first: notes that match what was said reach
  the reply prompt (a small token budget, recall()).
- When MBUDDY is asked to do or know something no ability covers, and its
  notes don't have it, it says "that's new to me, give me a little time,
  I'll learn it and come back to you" (learn_about()). A background job
  reads the web (search, then the top pages), writes a note in its own words
  (what it is, how it's done, what MBUDDY itself can do about it, the
  sources), and comes back to the user with what it learned.
- Learning rate (obsidian_vault.LearningRate): notes used in a turn where an
  action succeeded get stronger, a failure weakens them more, and time makes
  unused notes fade until they're archived (never deleted by MBUDDY).

S95: a connected app removed by the user takes its learned notes with it
(forget_app()). Notes go to the Recycle Bin, never deleted outright.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

import utils.logger as log
from brain.obsidian_vault import OutsideVaultError, Vault, safe_name
from brain.tools import ToolFailure

RECALL_BUDGET = 220        # tokens of notes in the reply prompt (the model runs at 4096)
RECALL_MIN_MATCH = 0.34    # a title/tag word, or several body words: one body word alone never qualifies
KNOWN_SCORE = 0.75         # a note this relevant counts as "already learned"
IDLE_WAIT_S = 90           # the learning job waits for a quiet moment before using the model
_LOCK = threading.Lock()
_vault: Vault | None = None
_vault_root: Path | None = None
_loop: asyncio.AbstractEventLoop | None = None
_learning: set[str] = set()


def _fail(message: str) -> ToolFailure:
    return ToolFailure(f"FAILED: {message}")


# ── where the brain lives ─────────────────────────────────────────────────
def _link_file() -> Path:
    from core import ui_settings
    return Path(ui_settings._path()).with_name("brain_link.json")


def _read_link() -> dict:
    try:
        return json.loads(_link_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_link(data: dict) -> None:
    path = _link_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def detect_obsidian_vault() -> str | None:
    """The vault Obsidian has open (or used last), from its own settings file."""
    cfg = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return None
    vaults = [v for v in (data.get("vaults") or {}).values() if isinstance(v, dict) and v.get("path")]
    vaults.sort(key=lambda v: (not v.get("open"), -float(v.get("ts") or 0)))
    return next((str(v["path"]) for v in vaults if Path(v["path"]).is_dir()), None)


def obsidian_installed() -> bool:
    return any(Path(p).exists() for p in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Obsidian" / "Obsidian.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Obsidian" / "Obsidian.exe"))


def vault_root() -> Path | None:
    link = _read_link()
    if link.get("unlinked"):
        return None  # the user unlinked it on purpose
    path = link.get("vault") or detect_obsidian_vault()
    return Path(path) if path and Path(path).is_dir() else None


def _embed_notes(texts: list[str]):
    """The embedder the reference library already uses (loaded once, shared);
    None when it isn't available, and retrieval is then by words alone."""
    try:
        from brain import library
        return library._embed(texts)
    except Exception:
        return None


def get_vault() -> Vault | None:
    global _vault, _vault_root
    root = vault_root()
    if root is None:
        return None
    with _LOCK:
        if _vault is None or _vault_root != root:
            vault = Vault(root)
            vault.init()
            vault.embed = _embed_notes   # retrieval by meaning as well as by words (W5)
            _vault, _vault_root = vault, root
        return _vault


def link(vault: str | None = None) -> dict:
    """Link a vault (default: the one Obsidian has open). Raises ValueError."""
    path = (vault or "").strip() or detect_obsidian_vault()
    if not path or not Path(path).is_dir():
        raise ValueError("no Obsidian vault folder was found; open a vault in Obsidian first")
    _write_link({"vault": str(Path(path)), "linked_at": datetime.now().isoformat(timespec="seconds")})
    get_vault()
    return status()


def unlink() -> dict:
    _write_link({"unlinked": True})
    return status()


def status() -> dict:
    root = vault_root()
    vault = get_vault() if root else None
    return {"linked": vault is not None, "vault": str(root) if root else None,
            "folder": str(vault.base) if vault else None, "notes": len(list_notes()) if vault else 0,
            "obsidian_installed": obsidian_installed(), "learning": sorted(_learning),
            "library": _library_size() if vault else {"topics": 0, "pages": 0, "chars": 0}}


def _library_size() -> dict:
    from brain import library
    return library.size()


# ── checking the brain every turn ─────────────────────────────────────────
# What of the brain may reach a prompt, for measuring whether it helps
# (tools/bench_brain.py): off = nothing from the vault; memory = notes and
# facts; lessons = + lessons; skills = + skills; full = everything, examples
# too. Each level includes the ones before it.
BRAIN_MODES = ("off", "memory", "lessons", "skills", "full")
_LESSON_KINDS = {"Lessons", "Debugging experience"}
_SKILL_KINDS = {"Skills"}
last_used = {"mode": "full", "n_notes": 0, "lessons": 0, "skills": 0, "examples": 0}


def brain_mode() -> str:
    mode = os.getenv("MBUDDY_BRAIN_MODE", "full").strip().lower()
    return mode if mode in BRAIN_MODES else "full"


def allows(what: str) -> bool:
    """May this kind of memory (memory, lessons, skills, examples) reach a prompt?"""
    needed = {"memory": 1, "lessons": 2, "skills": 3, "examples": 4}[what]
    return BRAIN_MODES.index(brain_mode()) >= needed


def used_line() -> str:
    """What the brain put into this turn, one line the log and the bench read."""
    u = last_used
    return f"[Brain] used: {u['mode']} n_notes={u['n_notes']} lessons={u['lessons']} skills={u['skills']} examples={u['examples']}"


def recall(text: str, budget: int = RECALL_BUDGET) -> tuple[str, list[Path]]:
    """(prompt block of the notes that match, their paths); ("", []) when none."""
    last_used.update(mode=brain_mode(), n_notes=0, lessons=0, skills=0, examples=0)
    if not allows("memory"):
        return "", []
    vault = get_vault()
    if vault is None or not (text or "").strip():
        return "", []
    try:
        # At most two notes: more makes a small model lose the thread (S104).
        # Only notes that really match: one shared body word pulled an unrelated
        # note into almost every turn ("what time is it?" got a slides note), which
        # misled the reply and cost ~200 tokens to read (S6b).
        picked = vault.retrieve(text, token_budget=budget, max_notes=2, min_match=RECALL_MIN_MATCH)
    except Exception as exc:
        log.warn(f"[Brain] recall failed: {exc}")
        return "", []
    picked = [p for p in picked if (p["kind"] not in _LESSON_KINDS or allows("lessons"))
              and (p["kind"] not in _SKILL_KINDS or allows("skills"))]
    lessons = sum(1 for p in picked if p["kind"] in _LESSON_KINDS)
    skills = sum(1 for p in picked if p["kind"] in _SKILL_KINDS)
    last_used.update(n_notes=len(picked) - lessons - skills, lessons=lessons, skills=skills)
    return vault.render(picked), [p["path"] for p in picked]


_rules_cache: dict[str, tuple[float, str]] = {}


def _same_app(picked: dict, text: str) -> bool:
    """A rule learned in one app applies only when the request is about that app (F8: a Blender rule was
    handed to the decision for a greeting). A rule tied to no app applies anywhere."""
    tags = [str(t) for t in ((picked.get("meta") or {}).get("tags") or [])]
    apps = [t.rsplit("/", 1)[-1] for t in tags if t.startswith("mbuddy/app/")]
    if not apps:
        return True
    said = set(re.findall(r"[a-z]+", (text or "").lower()))
    return any(re.match(r"[a-z]+", a) and re.match(r"[a-z]+", a).group(0) in said for a in apps)


def recall_rules(text: str, budget: int = 170, max_notes: int = 3) -> str:
    """The lessons and skills that apply to this request, as short lines for the
    DECISION step (which tool is right). Bounded, and only what the brain mode
    allows. Cached for a few seconds: the two decision steps ask the same thing.
    "" when nothing applies."""
    text = (text or "").strip()
    if not text or not (allows("lessons") or allows("skills")):
        return ""
    hit = _rules_cache.get(text)
    if hit and time.time() - hit[0] < 20:
        return hit[1]
    vault = get_vault()
    if vault is None:
        return ""
    try:
        picked = vault.retrieve(text, token_budget=budget + 120, max_notes=8, min_match=RECALL_MIN_MATCH)
    except Exception as exc:
        log.warn(f"[Brain] rules recall failed: {exc}")
        return ""
    picked = [p for p in picked if ((p["kind"] in _LESSON_KINDS and allows("lessons"))
              or (p["kind"] in _SKILL_KINDS and allows("skills"))) and _same_app(p, text)][:max_notes]
    out = "; ".join(re.sub(r"^- \[[^\]]*\] ", "", p["line"]) for p in picked)
    if picked:
        log.info("[Brain] rules for the decision: " + ", ".join(p["title"] for p in picked))
    if len(_rules_cache) > 32:
        _rules_cache.clear()
    _rules_cache[text] = (time.time(), out)
    return out


def search_notes(query: str) -> str:
    """The search_notes ability: a deterministic keyword/title/tag lookup."""
    vault = get_vault()
    if vault is None:
        return _fail("my brain isn't linked yet: link your Obsidian vault in Settings.")
    query = (query or "").strip()
    picked = vault.retrieve(query, token_budget=300, max_notes=2) if query else []
    return vault.render(picked) or f"Nothing in my notes about {query or 'that'} yet."


def note_used(paths: list[Path], success: bool | None) -> None:
    """The learning rate (obsidian_vault.LearningRate). success=True only from a
    CODE check of the result (never "ran without errors" or the model's own
    review, U2); False on a checked failure; None records the use only."""
    vault = get_vault()
    for path in paths or []:
        try:
            vault.reinforce(Path(path), success=success)
        except Exception as exc:
            log.warn(f"[Brain] could not update {Path(path).name}: {exc}")


def daily_decay() -> int:
    vault = get_vault()
    return vault.decay_all() if vault else 0


# ── learning something new ────────────────────────────────────────────────
def _known(vault: Vault, topic: str) -> dict | None:
    for hit in vault.retrieve(topic, token_budget=120, max_notes=1):
        if hit["score"] >= KNOWN_SCORE and hit["kind"] in ("Knowledge", "Apps", "Skills"):
            return hit
    return None


def learn_about(topic: str, kind: str = "topic", why: str = "") -> str:
    """The brain first; if it's new, learn it in the background and come back."""
    topic = re.sub(r"\s+", " ", (topic or "").strip().strip(".?!"))[:80]
    if not topic:
        return _fail("nothing was named to learn about.")
    vault = get_vault()
    if vault is None:
        return _fail("my brain isn't linked yet: link your Obsidian vault in Settings, then ask me again.")
    if _known(vault, topic):
        # F11: 'I'll go from those' was said and nothing followed. A task in an app that is connected is done there.
        if kind == "app" and why.strip():
            try:
                from core import ui_settings
                if ui_settings.connected_app(topic) is not None:
                    from brain.connectors import app_driver
                    return app_driver.use_app({"app": topic, "task": why.strip()})
            except Exception as exc:
                log.warn(f"[Brain] handing {topic!r} to use_app failed: {exc}")
        return f"I already have notes on {topic}."
    key = topic.lower()
    with _LOCK:
        if key in _learning:
            return f"I'm still learning about {topic}. I'll come back to you when I'm done."
        _learning.add(key)
    threading.Thread(target=_learn_job, args=(topic, "app" if kind == "app" else "topic", why),
                     name=f"learn:{key}", daemon=True).start()
    return f"That's new to me. Give me a little time to learn about {topic}, and I'll come back to you."


def _search(topic: str, kind: str) -> list[dict]:
    return _search_raw(f"{topic} app what is it and how to use it" if kind == "app" else f"{topic} how to")


def _search_raw(query: str) -> list[dict]:
    from ddgs import DDGS
    for backend in ("api", "lite", "auto"):
        try:
            with DDGS(timeout=6) as ddgs:
                found = [r for r in ddgs.text(query, max_results=5, backend=backend) if r.get("href")]
            if found:
                return found
        except Exception as exc:
            log.warn(f"[Brain] search backend {backend} failed: {type(exc).__name__}")
    return []


def _read_page(url: str, limit: int = 40000) -> str:
    """A page's readable text for learning: headings, paragraphs, list items,
    definitions and code, where manuals put their commands. (The search
    excerpt kept ~1000 characters, and a manual's flags came after that.)"""
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        from bs4 import BeautifulSoup
        from utils.search_provider import MODERN_UA
        response = requests.get(url, headers={"User-Agent": MODERN_UA}, timeout=6)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        for node in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
            node.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        parts, seen, size = [], set(), 0
        for node in main.find_all(["h1", "h2", "h3", "p", "li", "dt", "dd", "pre", "code", "kbd", "td"]):
            text = " ".join(node.get_text(" ", strip=True).split())
            if len(text) < 3 or text in seen:
                continue
            seen.add(text)
            parts.append(text)
            size += len(text) + 1
            if size > limit:
                break
        return "\n".join(parts)[:limit]
    except Exception:
        return ""


def _raw_code_url(url: str) -> str:
    """The raw file behind a GitHub file or gist page, or ""."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/([^?#]+)", url)
    if m:
        return f"https://raw.githubusercontent.com/{m[1]}/{m[2]}/{m[3]}"
    m = re.match(r"https://gist\.github\.com/([^/]+)/([0-9a-f]+)/?$", url)
    return f"https://gist.githubusercontent.com/{m[1]}/{m[2]}/raw" if m else ""


def read_code_blocks(url: str, limit: int = 8) -> list[str]:
    """The code blocks on a page (<pre>), keeping line breaks and indentation,
    which the text reader flattens (S111: examples found online)."""
    if not url.startswith(("http://", "https://")):
        return []
    from utils.search_provider import MODERN_UA
    raw = _raw_code_url(url)
    if raw:  # a code file on GitHub: the file itself, not the page around it
        try:
            response = requests.get(raw, headers={"User-Agent": MODERN_UA}, timeout=8)
            response.raise_for_status()
        except Exception:
            return []
        code = response.text.replace("\r", "").strip("\n")
        return [code] if 4 <= len(code.splitlines()) <= 400 else []
    try:
        from bs4 import BeautifulSoup
        response = requests.get(url, headers={"User-Agent": MODERN_UA}, timeout=8)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
    except Exception:
        return []
    blocks, seen = [], set()
    for pre in soup.find_all("pre"):
        for br in pre.find_all("br"):
            br.replace_with("\n")
        for line in pre.find_all(class_=re.compile(r"\bline\b")):  # highlighters that wrap each line
            line.append("\n")
        code = pre.get_text().replace("\r", "").strip("\n")
        count = len(code.splitlines())
        if 4 <= count <= 250 and code not in seen:
            seen.add(code)
            blocks.append(code)
        if len(blocks) >= limit:
            break
    return blocks


def _focus(text: str, limit: int = 1800) -> str:
    """A page's lines about scripts first, then other usage lines (flags,
    commands), then the rest: a manual's scripting part reaches the model
    even when it lists dozens of other options first."""
    lines = text.splitlines()
    scripts = [ln for ln in lines if re.search(r"\bscripts?\b", ln, re.I)]
    usage = [ln for ln in lines if ln not in scripts and re.search(r"(^|\s)--?\w|\bcommand\b", ln, re.I)]
    rest = [ln for ln in lines if ln not in scripts and ln not in usage]
    return "\n".join(scripts + usage + rest)[:limit]


def _wait_until_idle(max_wait: float = IDLE_WAIT_S) -> None:
    """Use the model when MBUDDY isn't mid-conversation (one Ollama slot)."""
    from core.status import runtime_status
    end, quiet = time.time() + max_wait, None
    while time.time() < end:
        if runtime_status.thinking or runtime_status.speaking:
            quiet = None
        elif quiet is None:
            quiet = time.time()
        elif time.time() - quiet > 3:
            return
        time.sleep(0.5)


_NOTE_PROMPT = (
    "You are MBUDDY writing a note for your own memory, from the web sources below. Use only what the "
    "sources say; if they don't say, leave it out. Plain English markdown with exactly these three sections:\n"
    "## What it is\n(one to three sentences)\n"
    "## How to do it\n(numbered steps, at most eight)\n"
    "## What I can do about it\n(which of YOUR abilities below can help, concretely; if none can, say plainly "
    "that you can't do it yourself yet, and what the user can do)\n"
    "YOUR ABILITIES: {abilities}"
)


def _write_note_text(topic: str, why: str, found: list[dict], pages: dict[str, str]) -> str:
    from brain.agent import AREAS, _chat_model, _ollama_chat_url
    abilities = "; ".join(f"{a}: {d}" for a, (d, _names) in AREAS.items() if a != "learn")
    sources = "\n\n".join(
        f"[{i}] {r.get('title', '')} ({r['href']})\n{(pages.get(r['href']) or r.get('body', ''))[:1800]}"
        for i, r in enumerate(found[:3], 1))
    body = {"model": _chat_model(), "stream": False, "think": False,
            "messages": [{"role": "system", "content": _NOTE_PROMPT.format(abilities=abilities)},
                         {"role": "user", "content": f"Topic: {topic}\n"
                                                     + (f"Why the user asked: {why}\n" if why else "")
                                                     + f"Sources:\n{sources[:6000]}"}],
            "options": {"temperature": 0.3, "num_predict": 600}, "keep_alive": "30m"}
    try:
        data = requests.post(_ollama_chat_url("background"), json=body, timeout=180).json()
    except Exception as exc:
        return _fail(f"I couldn't write the note ({type(exc).__name__}).")
    text = str((data.get("message") or {}).get("content") or "").strip()
    if data.get("error") or "## What it is" not in text:
        return _fail("the note came out empty or in the wrong shape.")
    return text


_DRIVER_PROMPT = (
    "From the sources, work out how a program can drive {app} with a script, without clicking. Reply with ONLY "
    "one JSON object and nothing else:\n"
    '{{"language": "<script language>", "file_ext": ".<extension>", "modules": ["<modules the script imports>"], '
    '"test": ["{{exe}}", "<arguments that run {{file}} without showing the app window, then exit>"], '
    '"show": ["{{exe}}", "<arguments that open the app and run {{file}} in front of the user>"]}}\n'
    "Write {{exe}} and {{file}} exactly like that. Use only arguments the sources show. If the sources show no way "
    'to drive it with a script, reply {{"language": null}}.'
)


def _extract_driver(app: str, found: list[dict], pages: dict[str, str]) -> dict | None:
    """How a program can drive the app, as the model reads it from the sources;
    checked by app_driver.validate_driver before it is kept or used."""
    from brain.agent import _chat_model, _ollama_chat_url
    from brain.connectors.app_driver import validate_driver
    sources = "\n\n".join(f"[{i}] {r.get('title', '')}\n{_focus(pages.get(r['href']) or r.get('body', ''))}"
                           for i, r in enumerate(found[:4], 1))
    body = {"model": _chat_model(), "stream": False, "think": False,
            "messages": [{"role": "system", "content": _DRIVER_PROMPT.format(app=app)},
                         {"role": "user", "content": f"App: {app}\nSources:\n{sources[:6000]}"}],
            "options": {"temperature": 0, "num_predict": 300}, "keep_alive": "30m"}
    try:
        text = str((requests.post(_ollama_chat_url("background"), json=body, timeout=180).json().get("message") or {})
                   .get("content") or "")
        m = re.search(r"\{.*\}", text, re.S)
        raw = json.loads(m.group(0)) if m else {}
    except Exception as exc:
        log.warn(f"[Brain] no driver for {app}: {exc}")
        return None
    driver = validate_driver(raw)
    if driver is not None and driver["test"] == driver["show"]:
        # A test that shows the window isn't a hidden test: find the flag.
        flag = _ask_one(f"Which single command-line argument starts {app} without opening its window (background "
                        f"or headless mode)? Answer with just that argument, or none.", sources[:4000])
        if flag and re.fullmatch(r"--?[A-Za-z][\w-]{0,30}", flag):
            driver = validate_driver({**driver, "test": [driver["show"][0], flag, *driver["show"][1:]]})
    if driver is not None or not isinstance(raw, dict):
        return driver
    # The command that shows the script may be right while the hidden test
    # isn't: ask one small question for the flag that runs the app without
    # its window, and test with the show command plus that flag.
    shown = validate_driver({**raw, "test": raw.get("show")})
    if shown is None:
        return None
    flag = _ask_one(f"Which single command-line argument starts {app} without opening its window (background or "
                    f"headless mode)? Answer with just that argument, or none.", sources[:4000])
    if flag and re.fullmatch(r"--?[A-Za-z][\w-]{0,30}", flag):
        return validate_driver({**raw, "show": shown["show"], "test": [shown["show"][0], flag, *shown["show"][1:]]})
    return None


def _ask_one(question: str, sources: str) -> str:
    """A short, focused question to the local model about the sources (one token-ish answer)."""
    from brain.agent import _chat_model, _ollama_chat_url
    body = {"model": _chat_model(), "stream": False, "think": False,
            "messages": [{"role": "system", "content": "Answer from the sources only, as briefly as possible."},
                         {"role": "user", "content": f"{question}\n\nSources:\n{sources}"}],
            "options": {"temperature": 0, "num_predict": 20}, "keep_alive": "30m"}
    try:
        text = str((requests.post(_ollama_chat_url("background"), json=body, timeout=120).json().get("message") or {})
                   .get("content") or "")
    except Exception:
        return ""
    m = re.search(r"--?[A-Za-z][\w-]*", text)
    return m.group(0) if m and "none" not in text.lower()[:10] else ""


_SCRIPTING_PROMPT = (
    "From the sources, write short notes for writing {language} scripts that {app} runs: the main modules and "
    "objects, how to create, find and change things, what changed in recent versions if the sources say so, and "
    "one small example script. Only what the sources show. Plain markdown, at most 18 lines."
)


def learn_scripting_now(name: str, language: str) -> str:
    """Learn how to write scripts for an app (its scripting API, with an
    example) and keep it in the app note's Scripting notes (S109: the first
    note was about installing the app, so scripts came from stale memory)."""
    from brain.agent import _chat_model, _ollama_chat_url
    from brain.connectors.app_driver import DRIVER_HEADING, SCRIPTING_HEADING
    found = _search_raw(f"{name} {language} scripting API example script")
    if not found:
        return ""
    pages = {r["href"]: _read_page(r["href"]) for r in found[:3]}
    sources = "\n\n".join(f"[{i}] {r.get('title', '')}\n{_focus(pages.get(r['href']) or r.get('body', ''), 1600)}"
                          for i, r in enumerate(found[:3], 1))
    _wait_until_idle()
    body = {"model": _chat_model(), "stream": False, "think": False,
            "messages": [{"role": "system", "content": _SCRIPTING_PROMPT.format(language=language, app=name)},
                         {"role": "user", "content": f"App: {name}\nSources:\n{sources[:6000]}"}],
            "options": {"temperature": 0.2, "num_predict": 500}, "keep_alive": "30m"}
    try:
        notes = str((requests.post(_ollama_chat_url("background"), json=body, timeout=180).json().get("message") or {})
                    .get("content") or "").strip()
    except Exception as exc:
        log.warn(f"[Brain] scripting notes for {name} failed: {exc}")
        return ""
    if not notes:
        return ""
    vault = get_vault()
    path = vault.base / "Apps" / f"{safe_name(name)}.md" if vault else None
    if path is not None and path.is_file():
        from brain.obsidian_vault import END_MARK, START_MARK
        text = vault.read(path)[1]
        start, end = text.find(START_MARK), text.find(END_MARK)
        managed = text[start + len(START_MARK):end].strip() if 0 <= start < end else text
        managed = re.sub(re.escape(SCRIPTING_HEADING) + r".*?(?=\n## |\Z)", "", managed, flags=re.S).strip()
        section = f"{SCRIPTING_HEADING}\n{notes}\n" + "\n".join(f"- {r['href']}" for r in found[:3])
        managed = managed.replace(DRIVER_HEADING, f"{section}\n\n{DRIVER_HEADING}", 1) if DRIVER_HEADING in managed \
            else f"{managed}\n\n{section}"
        vault.upsert("Apps", name, managed)
    return notes


def learn_app_now(name: str) -> tuple[str, dict | None]:
    """Learn an app now (used by the Learn button and by use_app): what it is,
    how to use it, and how a program can drive it. Writes Apps/<name>.md."""
    from brain.connectors.app_driver import DRIVER_HEADING, slug
    general = _search(name, "app")
    driving = _search_raw(f"{name} command line arguments run script automation")
    seen, driving_unique = {r["href"] for r in general}, []
    for r in driving:
        if r["href"] not in seen:
            seen.add(r["href"])
            driving_unique.append(r)
    if not general and not driving_unique:
        return "", None
    pages = {r["href"]: _read_page(r["href"]) for r in (general[:2] + driving_unique[:3])}
    _wait_until_idle()
    note = _write_note_text(name, "so you can use it for the user", general or driving_unique, pages)
    if isinstance(note, ToolFailure):
        return "", None
    driver = _extract_driver(name, driving_unique[:3] + general[:1], pages)
    drive = (f"{DRIVER_HEADING}\n```json\n{json.dumps(driver, indent=1)}\n```" if driver
             else f"{DRIVER_HEADING}\nNo way to drive it with a script was found yet.")
    urls = "\n".join(f"- {r.get('title', r['href'])}: {r['href']}" for r in (general[:2] + driving_unique[:3]))
    vault = get_vault()
    if vault is not None:
        vault.upsert("Apps", name, f"{note}\n\n{drive}\n\n## Sources\n{urls}", source="web", confidence=0.5,
                     tags=["mbuddy/learned", "mbuddy/app", f"mbuddy/app/{slug(name)}"])
    if driver:  # it can be driven: learn how to write its scripts too
        learn_scripting_now(name, driver["language"])
    return note, driver


def _summary(note: str) -> str:
    m = re.search(r"## What it is\s*(.+?)(?:\n##|\Z)", note, re.S)
    first = re.split(r"(?<=[.!?])\s+", (m.group(1) if m else "").strip())[0] if m else ""
    return first[:220]


def _learn_job(topic: str, kind: str, why: str) -> None:
    key = topic.lower()
    from core import jobs
    jobs.begin("learn", topic, why or topic)
    jobs.step_current(doing="looking for good sources", phase="planning", act="search")
    try:
        if kind == "app":  # the Learn button: what it is, how to use it, how to drive it
            note, driver = learn_app_now(topic)
            if not note:
                _announce(f"[NORMAL] I tried to learn about {topic}, but I couldn't find enough online.", topic, ok=False)
                return
            extra = " I also worked out how to drive it with a script." if driver else ""
            _announce(f"[HAPPY] I'm back. I learned about {topic}. {_summary(note)}{extra} It's in my notes now."
                      .replace("..", "."), topic, ok=True)
            return
        found = _search(topic, kind)
        if not found:
            _announce(f"[NORMAL] I tried to learn about {topic}, but I couldn't find anything useful online.",
                      topic, ok=False)
            return
        pages = {}
        for n, r in enumerate(found[:3], 1):   # pages read of pages to read: progress it really knows
            jobs.step_current(doing=f"reading page {n} of {len(found[:3])}", phase="executing", act="search",
                              progress=(n - 1) / (len(found[:3]) + 1))
            pages[r["href"]] = _read_page(r["href"])
        _wait_until_idle()
        jobs.step_current(doing="writing what I learned into my notes", phase="evaluating", act="write",
                          progress=len(pages) / (len(pages) + 1))
        note = _write_note_text(topic, why, found, pages)
        if isinstance(note, ToolFailure):
            _announce(f"[NORMAL] I read about {topic}, but I couldn't turn it into a proper note. Ask me again later.",
                      topic, ok=False)
            return
        urls = "\n".join(f"- {r.get('title', r['href'])}: {r['href']}" for r in found[:3])
        vault = get_vault()
        if vault is None:
            return
        path = vault.upsert("Apps" if kind == "app" else "Knowledge", topic, f"{note}\n\n## Sources\n{urls}",
                            source="web", confidence=0.5, tags=["mbuddy/learned", f"mbuddy/{kind}"])
        log.info(f"[Brain] learned {topic!r} -> {path}")
        summary = _summary(note)
        _announce(f"[HAPPY] I'm back. I learned about {topic}. {summary} It's in my notes now.".replace("..", "."),
                  topic, ok=True)
    except Exception as exc:
        log.warn(f"[Brain] learning {topic!r} failed: {exc}")
        _announce(f"[NORMAL] I tried to learn about {topic}, but something went wrong.", topic, ok=False)
    finally:
        jobs.finish_current(False, "it stopped unexpectedly")  # only if nothing ended it
        with _LOCK:
            _learning.discard(key)


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """The server's event loop, so a background job can speak (main.py)."""
    global _loop
    _loop = loop
    log.info(f"[Brain] mode: {brain_mode()} (MBUDDY_BRAIN_MODE)")
    from core import jobs
    jobs.on_change(_job_stalled)


def _job_stalled(event: str, job) -> None:
    """A job that stopped making progress is said, not left as "running" (F2)."""
    if event != "stalled":
        return
    what = f"the work in {job.subject}" if job.kind == "app" else f"learning about {job.subject}"
    _announce(f"[NORMAL] I got stuck on {what}: {job.outcome.removeprefix('stalled: ')}. "
              "I've stopped it; ask me again if you still want it.", job.subject, ok=False)


def _announce(speech: str, topic: str, ok: bool) -> None:
    """Come back to the user: a notification in the app, and said out loud
    when a voice session is open (like reminders, main.py)."""
    from core import jobs
    if jobs.cancelled_current():
        # F52: 'I got stuck, stopped' was said, then a late 'I made it' and a new window. One job, one story.
        log.info(f"[Announce] not said: the job was already ended as stalled ({speech[:80]!r})")
        jobs.finish_current(False)
        return
    log.info(f"[Announce] {speech}")  # in full: TTS logs only its first words (S116)
    # The announcement ends the background job running on this thread, with
    # its real outcome, so the next reply knows how it went (W1).
    jobs.finish_current(ok, re.sub(r"^\s*\[[A-Z]+\]\s*", "", speech).replace("I'm back. ", "")[:200])
    try:
        from core.status import runtime_status
        runtime_status.record_fired_reminders([{
            "id": f"learned:{safe_name(topic)}:{int(time.time())}",
            "title": ("Learned: " if ok else "Couldn't learn: ") + topic, "due": "", "source": "learned"}])
    except Exception:
        pass
    if _loop is None:
        return

    async def say() -> None:
        from api.device_control import active_session
        from core import ui_settings
        session = active_session()
        if ui_settings.get("do_not_disturb") or session is None or getattr(session, "ws", None) is None:
            return
        await session._send_checked(speech, "Happy" if ok else "Normal", lang="en")

    try:
        asyncio.run_coroutine_threadsafe(say(), _loop)
    except Exception as exc:
        log.warn(f"[Brain] could not announce: {exc}")


# ── what the user sees and removes ────────────────────────────────────────
def list_notes() -> list[dict]:
    vault = get_vault()
    out: list[dict[str, Any]] = []
    for path in (vault.notes() if vault else []):
        meta, _body, _ = vault.read(path)
        if meta is None:
            continue
        out.append({"title": path.stem, "kind": path.parent.name, "status": meta.get("status"),
                    "strength": float(meta.get("strength", 0) or 0), "uses": int(meta.get("uses", 0) or 0),
                    "source": meta.get("source"), "path": str(path)})
    return out


def _trash(path: Path) -> None:
    from brain.pc_actions import _recycle
    _recycle(str(path))


def delete_note(path: str) -> bool:
    """Move one of MBUDDY's notes to the Recycle Bin; never outside its folder."""
    vault = get_vault()
    if vault is None:
        return False
    try:
        target = vault._inside(Path(path))
    except OutsideVaultError:
        return False
    if target.suffix.lower() != ".md" or not target.is_file():
        return False
    _trash(target)
    return True


def forget_app(name: str) -> bool:
    """S95: the learned notes about a removed app go with it."""
    vault = get_vault()
    if vault is None or not (name or "").strip():
        return False
    removed = False
    for folder in ("Apps", "Knowledge"):
        path = vault.base / folder / f"{safe_name(name)}.md"
        if path.is_file():
            _trash(path)
            removed = True
    from brain import library  # the pages read about it, and their index (S117)
    return library.delete(name) or removed
