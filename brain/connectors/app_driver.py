"""
Connected apps: doing things inside an app the user connected (S103, S106, S107).

Nothing here is about a particular app. For any connected app:
1. The brain first. MBUDDY's note on the app (Apps/<name>.md) may hold a
   driver it learned: how a program can drive the app (the script language,
   a command that tests a script without showing the app, and one that runs
   it in front of the user). A skill note may hold a script that worked.
2. If the app is new: "that's new to me, give me a little time", then learn
   it from the web (brain_store.learn_app_now): what it is, how to use it,
   and the driver, which the model works out from what it read.
3. Write the script for the task from what was learned (and a skill, if one
   fits), check it (Python: no files, processes, network or eval), test it
   where the user doesn't see it, and on an error fix it with the error and
   the failed script in hand, up to three times.
4. Run it in front of the user, keep the working script as a skill, and make
   the app note stronger, or weaker when it fails (the learning rate).
An app without a script interface stays honest: MBUDDY says what it learned
and that it can't drive the app yet.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import requests

import utils.logger as log
from brain.connectors import Ability, fail
from brain.tools import ToolFailure

_NO_WINDOW = 0x08000000
MAX_TRIES = 6             # live: three tries ran out while each failure was being understood (S109)
TIME_BUDGET_S = 600       # but never more than ten minutes of trying
TEST_TIMEOUT_S = 120
# The local model's window holds the prompt and the script it writes. Past it,
# Ollama drops the start of the prompt mid-script: the instructions, the notes
# and the plan (seen live: "context shift ... n_discard = 2045").
MODEL_CONTEXT = 4096      # Ollama's num_ctx (docs/research/learning_efficiency_research.md, B1.5)
CHARS_PER_TOKEN = 2.6     # measured on a real script; prose is ~3.5, so this over-counts safely
SCRIPT_RESERVE = 1300     # tokens kept free for the script (a compact one measured ~1,200)
DRIVER_HEADING = "## How MBUDDY can drive it"
SCRIPTING_HEADING = "## Scripting notes"
_ARG = re.compile(r"^[\w\-./:=,{}]{1,60}$")      # no spaces or shell characters in a learned argument
# Modules that can reach files, processes, the network or the system. The app's
# own scripting modules can't be known in advance, so this is a deny-list.
_BANNED_MODULES = {"os", "sys", "subprocess", "shutil", "socket", "requests", "urllib", "urllib3", "pathlib",
                   "ctypes", "importlib", "pickle", "http", "ftplib", "glob", "tempfile", "io", "builtins",
                   "threading", "multiprocessing", "asyncio", "webbrowser", "winreg", "zipfile", "tarfile",
                   "sqlite3", "smtplib", "telnetlib", "ssl", "signal", "platform", "getpass", "fileinput",
                   "shelve", "marshal", "runpy", "code", "codeop", "pty", "select", "mmap", "msvcrt", "nt",
                   "posix", "_winapi", "win32api", "win32com", "pywintypes", "psutil", "email", "xmlrpc"}
_BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals",
                 "getattr", "setattr", "delattr", "vars"}
_busy: set[str] = set()
_LOCK = threading.Lock()
_ACTIVE_SESSIONS: dict[str, dict] = {}
_SHOWN: dict[str, int] = {}  # the window MBUDDY last showed a result in, per app (closing one is always asked first)
SESSION_TTL_S = 1800.0  # 30 minute active session window


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get_active_session(name: str) -> dict | None:
    """Returns recent active execution session for the app if within TTL."""
    key = slug(name)
    session = _ACTIVE_SESSIONS.get(key)
    if not session:
        return None
    if time.time() - session.get("updated_at", 0) > SESSION_TTL_S:
        _ACTIVE_SESSIONS.pop(key, None)
        return None
    return session


def record_active_session(name: str, task: str, code: str) -> None:
    """Records or updates active execution session for an app."""
    key = slug(name)
    _ACTIVE_SESSIONS[key] = {
        "app": name,
        "task": task,
        "code": code,
        "updated_at": time.time(),
    }



# ── the program behind a connected app ────────────────────────────────────
def program(path: str) -> str | None:
    """A .lnk followed to its target; a launcher shim ("x-launcher.exe") replaced
    by the program beside it ("x.exe"), since a launcher passes back no
    output or exit code."""
    if path.lower().endswith(".lnk"):
        try:
            import win32com.client
            path = win32com.client.Dispatch("WScript.Shell").CreateShortcut(path).TargetPath or ""
        except Exception:
            return None
    p = Path(path)
    m = re.match(r"(.+?)[-_ ]?launcher$", p.stem, re.I)
    if m and (p.parent / f"{m.group(1)}{p.suffix}").is_file():
        p = p.parent / f"{m.group(1)}{p.suffix}"
    return str(p) if path and p.is_file() else None


# ── what MBUDDY learned about the app ─────────────────────────────────────
def validate_driver(d) -> dict | None:
    """A learned driver, normalised and checked before anything runs; None
    when unusable. The model often writes the program's name for {exe} and
    an example file ("myscript.py") for {file}; those are mapped, not refused."""
    if not isinstance(d, dict) or not d.get("language"):
        return None
    lang = str(d.get("language", "")).strip()
    ext = str(d.get("file_ext", "")).strip().lower()
    ext = ext if ext.startswith(".") else f".{ext}" if ext else ""
    if not re.fullmatch(r"[\w+#.\- ]{1,24}", lang) or not re.fullmatch(r"\.\w{1,6}", ext):
        return None
    out = {"language": lang, "file_ext": ext,
           "modules": [m for m in d.get("modules") or [] if isinstance(m, str) and re.fullmatch(r"[A-Za-z_]\w{0,30}", m)][:20]}
    for key in ("test", "show"):
        cmd = d.get(key)
        if not isinstance(cmd, list) or not cmd or len(cmd) > 12 or not all(isinstance(a, str) for a in cmd):
            return None
        cmd = [part for a in cmd for part in a.split()]  # "--run {file}" -> "--run", "{file}"
        if not cmd:
            return None
        if cmd[0] != "{exe}" and not cmd[0].startswith("-"):
            cmd[0] = "{exe}"  # the program, however the model named it
        cmd = ["{file}" if a != "{file}" and a.lower().endswith(ext) and not a.startswith("-") else a for a in cmd]
        if cmd[0] != "{exe}" or "{file}" not in cmd or not all(_ARG.fullmatch(a) for a in cmd):
            return None
        out[key] = cmd
    return out


def learned(name: str) -> tuple[str, dict | None, Path | None]:
    """(what MBUDDY knows about the app as plain text, its driver, the note's path)."""
    from brain import brain_store
    from brain.obsidian_vault import plain, safe_name
    vault = brain_store.get_vault()
    path = vault.base / "Apps" / f"{safe_name(name)}.md" if vault else None
    if path is None or not path.is_file():
        name_lower = name.lower()
        if "word" in name_lower or "excel" in name_lower or "powerpoint" in name_lower:
            return "Use COM automation via win32com.client.", {
                "language": "python",
                "file_ext": ".py",
                "modules": ["win32com.client", "pythoncom"],
                "test": ["python", "{file}"],
                "show": ["python", "{file}"]
            }, None
        return "", None, None
    body = vault.read(path)[1]
    m = re.search(re.escape(DRIVER_HEADING) + r"\s*```json\s*(\{.*?\})\s*```", body, re.S)
    driver = None
    if m:
        try:
            driver = validate_driver(json.loads(m.group(1)))
        except ValueError:
            driver = None
    # What helps write a script: the scripting notes when it has them (live,
    # the rest was how to download the app, S109, and it filled a third of the
    # model's window). How it's driven, how results are checked and the
    # sources are for MBUDDY's code, not for writing the script.
    scripting = re.search(re.escape(SCRIPTING_HEADING) + r"\s*(.+?)(?=\n## |\Z)", body, re.S)
    if scripting:
        return f"Scripting notes:\n{plain(scripting.group(1))}"[:2400], driver, path
    for heading in (DRIVER_HEADING, INSPECT_HEADING, "## Sources"):
        body = re.sub(re.escape(heading) + r".*?(?=\n## |\Z)", "", body, flags=re.S)
    return plain(body)[:2400], driver, path


def _skill(name: str, task: str):
    """(path, script, verified) of the best skill for this app and task, or
    None. A script that never worked (tagged unfinished) is not a skill. A
    verified one (its result was checked by code) comes first; an unverified
    one is only offered as an earlier attempt that may be wrong (U2)."""
    from brain import brain_store
    from brain.obsidian_vault import _words
    vault = brain_store.get_vault()
    if vault is None:
        return None
    app_tag = f"mbuddy/app/{slug(name)}"

    def usable(meta, body):
        tags = [str(t) for t in (meta or {}).get("tags") or []]
        m = re.search(r"```\w*\n(.*?)```", body, re.S)
        return m.group(1) if m and app_tag in tags and "mbuddy/unfinished" not in tags else None

    for hit in vault.retrieve(f"{name} {task}", token_budget=400, max_notes=5):
        if hit["kind"] != "Skills":
            continue
        meta, body, _ = vault.read(hit["path"])
        script = usable(meta, body)
        if script and meta.get("verified") and meta.get("status") != "unverified":
            return hit["path"], script, True
    # Unverified skills sit below recall's threshold: looked up directly.
    want, best = _words(f"{name} {task}"), None
    folder = vault.base / "Skills"
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        meta, body, _ = vault.read(path)
        script = usable(meta, body)
        overlap = len(want & _words(path.stem))
        if script and overlap > 1 and (best is None or overlap > best[0]):
            best = (overlap, path, script)
    return (best[1], best[2], False) if best else None


# ── writing, checking, testing, showing ───────────────────────────────────
def code_from(text: str) -> str:
    """The code in a model reply: a fenced block, or the text from its first code-like line."""
    m = re.search(r"```[\w+-]*\s*\n(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    lines = text.strip().splitlines()
    for i, line in enumerate(lines):
        if re.match(r"\s*(import |from |#|def |class |[A-Za-z_][\w.]*\s*(=|\())", line):
            return "\n".join(lines[i:]).strip()
    return text.strip()


REPORT_TAG = "MBUDDY_STATE:"
INSPECT_HEADING = "## How MBUDDY checks results"


def inspector(name: str) -> str:
    """The learned snippet that prints what really exists in the app ("" if none)."""
    from brain import brain_store
    from brain.obsidian_vault import safe_name
    vault = brain_store.get_vault()
    path = vault.base / "Apps" / f"{safe_name(name)}.md" if vault else None
    if path is None or not path.is_file():
        return ""
    m = re.search(re.escape(INSPECT_HEADING) + r"\s*```\w*\n(.*?)```", vault.read(path)[1], re.S)
    return m.group(1).strip() if m else ""


def learn_inspector(name: str, driver: dict, exe: str, knowledge: str) -> str:
    """Learn, test and keep a snippet that prints the app's real state, so a
    result is checked against what exists, not what a script says it made
    (live check: the model's own report lines were hardcoded text, S110)."""
    from brain import brain_store
    system = f"You write {driver['language']} for {name}.\nWhat you learned about {name}:\n{knowledge[:1500]}"
    ask = (f"Write a short {driver['language']} snippet that, run at the end of any script in {name}, prints one line "
           f"for every object or part that exists, in this form:\n{REPORT_TAG} <name> <type> (dx, dy, dz) at (x, y, z) "
           "rot (rx, ry, rz)\nwhere (dx, dy, dz) is its real measured size in world units (its bounding box, "
           "never a scale factor), (x, y, z) its position and rot its rotation in degrees. Import what it needs. "
           "Only the code.")
    snippet, problem = "", ""
    for attempt in range(4):  # like any script: fixed from its own error (live: "print(m buddy_line)")
        # The failing line is named, and a retry may vary: live, the same
        # broken line (`r.degrees`) came back three times at temperature 0.
        bad = failing_line(problem) if problem else ""
        snippet = code_from(_ask(system, ask + (f"\n\nYour last snippet:\n```\n{snippet}\n```\nIt failed: {problem}\n"
                                                + (f"The line that failed: `{bad}`. Change or remove it.\n" if bad else "")
                                                + "Reply with the whole corrected snippet." if problem else ""),
                                 400, 0.3 * min(attempt, 2)))
        problem = unsafe_python(snippet) if snippet else "it came back empty"
        if problem:
            continue
        ok, error, _where = _test(name, driver, exe, "", snippet)
        if ok and REPORT_TAG in getattr(_test, "last_output", ""):
            break
        problem = error[-600:] if not ok else f"it printed no line starting with {REPORT_TAG}"
    else:
        log.info(f"[Apps] {name}: couldn't learn how to check results ({problem[:120]!r})")
        return ""
    vault = brain_store.get_vault()
    from brain.obsidian_vault import END_MARK, START_MARK, safe_name
    path = vault.base / "Apps" / f"{safe_name(name)}.md" if vault else None
    if path is not None and path.is_file():
        text = vault.read(path)[1]
        start, end = text.find(START_MARK), text.find(END_MARK)
        managed = text[start + len(START_MARK):end].strip() if 0 <= start < end else text
        managed = re.sub(re.escape(INSPECT_HEADING) + r".*?(?=\n## |\Z)", "", managed, flags=re.S).strip()
        vault.upsert("Apps", name, f"{managed}\n\n{INSPECT_HEADING}\n```\n{snippet}\n```")
    log.info(f"[Apps] {name}: learned how to check results")
    return snippet


def _ask(system: str, user: str, num_predict: int, temperature: float = 0, think: bool = False) -> str:
    from brain.agent import _chat_model, _ollama_chat_url
    body = {"model": _chat_model(), "stream": False, "think": think,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": temperature, "num_predict": num_predict}, "keep_alive": "30m"}
    try:
        # Strip <think>...</think> from response so only the actual answer is returned
        raw = str((requests.post(_ollama_chat_url("job"), json=body, timeout=180).json().get("message") or {})
                  .get("content") or "").strip()
        return re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    except Exception:
        return ""


def plan_result(name: str, task: str, knowledge: str, example: str) -> str:
    """Before any code: what the result must contain so it really is what was
    asked, the way the real thing is (S110: "ran without errors" made a cube
    with four upright cylinders, not a car)."""
    return _ask(f"You plan what to make in {name} before writing a script."
                + (f"\nWhat you learned about {name}:\n{knowledge}" if knowledge else "")
                + (f"\nSomething you made before:\n{example}" if example else ""),
                f"Task: {task}\nBefore any code, plan the result. Each line: the exact object name you will assign "
                f"in the script (e.g. 'BaseSlab', 'FirstFloor', 'Roof', 'Door', 'Window'), then a colon, then its "
                "shape, size, and position. Use real-world proportions. The names must be the actual names you will "
                "give to objects in the script — they will be used to verify the result. "
                "At most 10 lines, only the list, no explanations.",
                350, 0.2, think=False)


class _Cleaner(ast.NodeTransformer):
    """Takes the docstrings out of an example, and every statement MBUDDY's own
    check would refuse to run (saving, files, system modules), so the model
    isn't shown lines it may not use."""

    def generic_visit(self, node):
        super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list) or not stmts or not isinstance(stmts[0], ast.stmt):
                continue
            kept = [s for s in stmts if not _unsafe_node(s)
                    and not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
            setattr(node, field, kept or ([] if isinstance(node, ast.Module) or field != "body" else [ast.Pass()]))
        return node


def clean_example(code: str, language: str) -> str:
    """An example made compact for the model's small window: no comments or
    blank lines, and for Python no docstrings and nothing it would refuse."""
    if not language.lower().startswith("python"):
        return "\n".join(ln.rstrip() for ln in code.splitlines()
                         if ln.strip() and not ln.strip().startswith(("#", "//", "--", ";")))
    try:
        cleaned = ast.unparse(_Cleaner().visit(ast.parse(code))).strip()
        ast.parse(cleaned)  # still valid after lines were taken out
        return cleaned
    except (SyntaxError, ValueError, RecursionError):
        return ""


def find_examples(name: str, driver: dict, task: str, plan: str = "") -> list[tuple[str, str]]:
    """Example scripts for this kind of task found online, as a developer looks
    them up before writing (S111): (link, compact code), the closest to the
    task and plan first. Only code that uses the app's own scripting modules."""
    from concurrent.futures import ThreadPoolExecutor
    from brain import brain_store
    language, mods = driver["language"], [m for m in driver.get("modules") or [] if m]
    wrote = _ask("You find example code on the web.",
                 f"Write one web search query, at most 8 words, that a programmer would type to find example "
                 f"{language} code for {name}{' (' + ', '.join(mods) + ')' if mods else ''} that does this: {task}. "
                 "Reply with only the query.", 30).strip().strip('"').splitlines()
    queries = list(dict.fromkeys(q for q in (wrote[0][:120] if wrote else "",
                                             f"{mods[0] if mods else name} {language} script {task}") if q))
    links = []
    for query in queries:
        links += [r["href"] for r in brain_store._search_raw(query)[:5] if r["href"] not in links]
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(brain_store.read_code_blocks, links))
    words = {re.sub(r"(?<=[a-z]{3})(es|s)$", "", w) for w in re.findall(r"[a-z]{3,}", f"{task} {plan}".lower())}
    found = []
    for link, blocks in zip(links, pages):
        # A tutorial splits one script over many boxes: a page's code is one example.
        codes = [c for c in (clean_example(b, language) for b in blocks)
                 if c and (not mods or any(re.search(rf"\b{re.escape(m)}\b", c) for m in mods))]
        if language.lower().startswith("python"):
            # A snippet from a tutorial often leaves the import out (live: "name 'bpy' is not defined").
            codes = ["".join(f"import {m}\n" for m in mods if re.search(rf"\b{re.escape(m)}\.", c)
                             and not re.search(rf"^\s*(import|from)\s+{re.escape(m)}\b", c, re.M)) + c for c in codes]
        code = "\n\n".join(codes)
        if len(code.splitlines()) >= 4:
            # The closest to the task first; then the fuller one, which shows more.
            found.append((sum(w in code.lower() for w in words), len(code), link, code))
    found.sort(reverse=True)
    log.info(f"[Apps] {name}: searched {queries}; usable examples on {len(found)} of {len(links)} pages")
    return [(link, code) for _score, _size, link, code in found[:3]]


def verify_examples(name: str, driver: dict, exe: str, found: list[tuple[str, str]], inspect: str) -> list[tuple[str, str]]:
    """The examples found online, run hidden in the app first (S111): an
    example written for another version may not work. Only the ones that
    really run are kept, each with what it made (the app's own report), so
    the model sees working calls and their real effect."""
    kept = []
    for link, code in found:
        if driver["language"].lower().startswith("python") and unsafe_python(code):
            continue
        ok, error, _where = _test(name, driver, exe, code, inspect)
        log.info(f"[Apps] {name}: example from {link} " + ("works" if ok else f"fails here: {error.strip()[-120:]!r}"))
        if not ok and "\n\n" in code:
            # A page's boxes were joined; one bad box shouldn't sink the good
            # ones (live: a fragment of a class failed the whole page).
            parts = [p for p in code.split("\n\n")[:6] if _test(name, driver, exe, p, inspect)[0]]
            log.info(f"[Apps] {name}: {len(parts)} of its boxes work on their own")
            if parts:  # together if they still work together, else the first; run last so its report matches
                code = "\n\n".join(parts)
                ok = _test(name, driver, exe, code, inspect)[0]
                if not ok:
                    code = parts[0]
                    ok = _test(name, driver, exe, code, inspect)[0]
        if ok:
            made = _report(getattr(_test, "last_output", ""))[:400]
            kept.append((link, code + (f"\n# What it made in {name}:\n# " + made.replace("\n", "\n# ") if made else "")))
    return kept





_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "pair": 2, "both": 2}
# "4 cylinders" is a count; "radius 2.0", "0.6 m" and "12x10x0.4m" are measurements (F54: a radius read as a count)
_DIGIT_COUNT = re.compile(r"(?<![\d.])([2-9]|1\d)\s+(?!(?:m|cm|mm|meters?|metres?|units?|deg|degrees)\b)[a-z]")
# A part the plan gives a body (a cube, a 12x10x0.4 slab) can't be flat; a plane or a circle may be.
_SOLID = re.compile(r"\b(cube|box|cuboid|slab|block|cylinder|sphere|cone|torus|\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?\s*x\s*\d)", re.I)
_TRIPLE = r"\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)"


def _sizes(report: str) -> dict[str, tuple[float, float, float]]:
    """Each object's world size from the report: the first (dx, dy, dz) on its line, before 'at (x, y, z)'."""
    out = {}
    for line in report.splitlines():
        m = re.match(re.escape(REPORT_TAG) + r"\s*([^\s(|,:]+)(.*)", line.strip())
        if not m:
            continue
        size = (re.search(r"\bsize\s*=?\s*" + _TRIPLE, m.group(2))
                or re.search(_TRIPLE, re.split(r"\bat\b", m.group(2))[0]))
        if size:
            try:
                out[m.group(1).lower()] = tuple(abs(float(v)) for v in size.groups())
            except ValueError:
                pass
    return out


def coverage(plan: str, report: str) -> str:
    """Plain code, before any model judges: every part the plan names must
    exist in the app's real state, as many as the plan says ("four wheels":
    four), and a part the plan gives a body must not be flat. "" when covered,
    else the problems (live check: a lone cube was approved by the small
    model, S110)."""
    names = []
    for line in report.splitlines():
        m = re.match(re.escape(REPORT_TAG) + r"\s*([^\s(|,:]+)", line.strip())
        if m:
            names.append(m.group(1).lower())
    sizes = _sizes(report)
    problems, keys = [], []
    for line in plan.splitlines():
        m = re.match(r"\s*[-*\d.)]*\s*([A-Za-z][A-Za-z ]{1,40}?)\s*:", line)
        if not m:
            continue
        words = [w for w in re.findall(r"[a-z]+", m.group(1).lower()) if len(w) > 2]
        if not words:
            continue
        last = words[-1]
        plural = last.endswith("s") and not last.endswith(("ss", "us", "is")) and len(last) > 3  # "chassis" isn't
        key = last[:-1] if plural else last
        found = sum(1 for n in names if key in n)
        rest = line[m.end():].lower()
        count = next((v for k, v in _NUMBERS.items() if re.search(rf"\b{k}\b", line.lower())), 0)
        if not count and (d := _DIGIT_COUNT.search(rest)):
            count = int(d.group(1))
        # a plural with no number only says the part exists: "Leaves: a sphere" may be one object
        need = max(count, 1)
        if found < need:
            problems.append(f"- The plan's {m.group(1).strip().lower()} ({need}) isn't in the scene: found {found}.")
        elif _SOLID.search(rest):
            flat = [(n, s) for n, s in sizes.items() if key in n and min(s) < 1e-4]
            for n, s in flat[:3]:
                problems.append(f"- {n} is collapsed or flat: its size is {s[0]:.2f} × {s[1]:.2f} × {s[2]:.2f} m, "
                                f"but the plan gives it a body.")
        keys.append(key)
    # A default object the script never removed hid the whole model in a live
    # check (a lone Cube). Only the app's default primitives left behind count;
    # sub-parts the plan does not name (an armrest, a branch) are fine.
    default = re.compile(r"(cube|plane|sphere|cylinder|cone|torus|circle|icosphere|suzanne|monkey)(\.\d+)?")
    extra = [n for n in names if default.fullmatch(n) and not any(k in n for k in keys)]
    if extra:
        problems.append(f"- The scene has uncleaned default primitives: {', '.join(extra[:5])}. Remove or transform them.")
    return "\n".join(problems)


_WORD_NUMBERS = {**_NUMBERS, "one": 1, "seven": 7, "nine": 9, "ten": 10, "twelve": 12}


def against_request(task: str, report: str) -> str:
    """The user's own words, not the plan (F53: work that missed the request 'passed against my plan'): every
    "four legs" / "3 windows" in the request is counted in the scene. A part the scene names nothing like is left
    to the plan's check, since the script may call it something else."""
    names = [m.group(1).lower() for line in report.splitlines()
             if (m := re.match(re.escape(REPORT_TAG) + r"\s*([^\s(|,:]+)", line.strip()))]
    problems = []
    unit = r"(?:m|cm|mm|meters?|metres?|units?|degrees?|inch(?:es)?|feet|foot|ft)\b"
    # "four wooden legs" counts legs; "5 cm legs" and "2.5 m" are sizes, not counts
    for m in re.finditer(r"(?<![\d.])(\d{1,2}|\b(?:" + "|".join(_WORD_NUMBERS) + r"))\s+(?!" + unit + r")"
                         r"([a-z]{3,})(?:\s+([a-z]{3,}))?", task.lower()):
        count = int(m.group(1)) if m.group(1).isdigit() else _WORD_NUMBERS[m.group(1)]
        if count < 2 or m.group(2) in ("times", "minutes", "seconds", "hours", "days"):
            continue
        for noun in filter(None, (m.group(2), m.group(3))):
            if not noun.endswith("s") or noun.endswith(("ss", "us", "is")):
                continue                    # what's counted is plural: "two storey house" isn't two houses
            key = noun[:-1]
            if key.endswith("ie"):          # "bodies" -> "body"
                key = key[:-2] + "y"
            found = sum(1 for n in names if key in n)
            if found:
                if found < count:
                    problems.append(f"- You asked for {count} {noun}; the scene has {found}.")
                break
    return "\n".join(problems)


def review_result(name: str, task: str, plan: str, report: str) -> str:
    """Checks what the app reports it made against the plan and the request.

    Two-tier check:
    1. coverage()  — deterministic: every plan part must exist in the report
       by name and count. If coverage passes, we trust the result and skip
       the LLM judge (which was hallucinating failures even for correct scenes).
    2. LLM reviewer — only runs when coverage is uncertain (no plan or no
       MBUDDY_STATE lines), as a fallback judge.
    """
    if not report.strip():
        return "- Nothing exists in the scene after the script: it made nothing that can be seen."

    # Code first: every part the plan names must exist, as many as it says. A
    # model is only asked about what code cannot check (the geometry's sense).
    missing = "\n".join(p for p in (against_request(task, report), coverage(plan, report)) if p)
    if missing:
        return missing

    try:
        from brain.verification import VerificationEngine, VerificationStatus
        ve = VerificationEngine()
        res = ve.verify(task=task, plan=plan, execution_data={"exit_code": 0, "stdout": report, "report": report})
        if res.status in (VerificationStatus.VERIFIED_FAILURE, VerificationStatus.PARTIAL_SUCCESS):
            return res.details
    except Exception as e:
        return f"- Verification engine crashed: {str(e)}"

    # Code checked the geometry when the app reported real sizes (parts and counts, solid parts, legs on the ground,
    # the seat on the legs). The model only judges what code couldn't measure: live 2026-10-20 it failed a correct
    # chair three times ("the seat thickness extends above the legs"), the random verdicts of R3.
    if _sizes(report):
        return ""
    answer = _ask(f"You are a strict 3D geometry reviewer. You check what a script made in {name} against what was asked.",
                  f"Request: {task}\n\nWhat {name} reports the script made (dimensions and positions):\n{report}\n\n"
                  "Based ONLY on the (dx, dy, dz) dimensions and (x, y, z) positions listed above, evaluate if this forms a coherent object. "
                  "Check if legs are properly spaced at corners (not overlapping in the center), if the top plate is correctly positioned on the Z-axis, and if the proportions make sense. "
                  "Answer exactly OK if the geometry forms a structurally sound object. "
                  "Otherwise list the spatial flaws, one per line, each starting with '- '.", 300)
    problems = [ln.strip() for ln in answer.splitlines() if ln.strip().startswith("- ")]
    return "" if answer.upper().startswith("OK") and not problems else "\n".join(problems) or answer[:600]


def _report(output: str) -> str:
    return "\n".join(ln.strip() for ln in (output or "").splitlines() if ln.strip().startswith(REPORT_TAG))


_EXAMPLE_HEADING = "An example script found online"


def _tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def _fitted(parts: list[tuple[str, str, str]], room: int) -> list[tuple[str, str]]:
    """The optional parts of a prompt as (where, heading, text), most important
    first, each cut to what still fits in `room` tokens; one too short to help
    is left out. Returns (where, text) of the parts that fit."""
    kept, used = [], 0
    for where, heading, text in parts:
        left = room - used
        if not text or left < 150:
            continue
        part = f"\n\n{heading}:\n{text}"
        if _tokens(part) > left:
            part = part[:int(left * CHARS_PER_TOKEN)].rsplit("\n", 1)[0]
        kept.append((where, part))
        used += _tokens(part)
    return kept


def _compose(name: str, driver: dict, knowledge: str, task: str, example: str, last: str, error: str,
             diagnosis: str = "", failing: str = "", research: str = "", plan: str = "",
             found: list[tuple[str, str]] | tuple = (), base: str = "") -> str:
    from brain.agent import _chat_model, _ollama_chat_url
    from brain.model_capabilities import (
        detect_model_capabilities,
        derive_execution_policy,
        TaskComplexity,
        PriorityContextBuilder,
        preserve_structured_script,
        preserve_structured_error,
    )

    model_name = _chat_model()
    caps = detect_model_capabilities(model_name)
    
    # Classify task complexity dynamically
    if error:
        complexity = TaskComplexity.DEBUGGING
    elif len(task) > 150 or (plan and len(plan) > 300) or "villa" in task.lower() or "house" in task.lower() or "tree" in task.lower():
        complexity = TaskComplexity.COMPLEX
    elif len(task) > 50:
        complexity = TaskComplexity.MODERATE
    else:
        complexity = TaskComplexity.SIMPLE

    policy = derive_execution_policy(caps, complexity, is_retry=bool(error))

    # System instruction focuses on correctness and outcome completeness rather than token minimization
    system = (f"You write {driver['language']} scripts that {name} runs itself to achieve the user's exact requested outcome. "
              f"Use what you learned about {name} below. Do what was asked thoroughly and completely. "
              "Prefer concise, organized code, but correctness, visual quality, structural completeness, and task fidelity "
              "take priority over minimum token count. Give each component a clear name. Reply with only the complete script, no explanation.")
    from brain import outcomes
    system += outcomes.lessons_for(f"{name} {task}", "use_app")

    if driver['language'].lower().startswith("python") and "blender" in name.lower():
        system += ("\n\nBLENDER 3D MODELING & PROCEDURAL SCRIPT RULES:\n"
                   "1. REAL 3D VOLUME: Give EVERY object real 3D volume (dx >= 0.5m, dy >= 0.5m, dz >= 0.5m). NEVER scale any dimension to 0.\n"
                   "2. GROUND POSITIONING: Position objects so base sits on or above ground level (Z >= height / 2).\n"
                   "3. BUILDINGS / HOUSES / VILLAS:\n"
                   "   - Create a base slab: BaseSlab (dx=12, dy=10, dz=0.4) at Z=0.2.\n"
                   "   - Create 1st floor 3D walls: FirstFloor (dx=10, dy=8, dz=3) at Z=1.7.\n"
                   "   - Create 2nd floor 3D walls: SecondFloor (dx=10, dy=8, dz=3) at Z=4.7.\n"
                   "   - Create a roof on top: Roof (dx=11, dy=9, dz=1.5) at Z=6.95.\n"
                   "   - Add visible Door (dx=1.2, dy=0.2, dz=2.2) and Window objects.\n"
                   "4. TREES (Trunk + Canopy):\n"
                   "   - Every tree MUST be built of TWO distinct meshes: a TreeTrunk (cylinder, dx=0.4, dy=0.4, dz=3.0) AND a TreeCanopy (icosphere or cone, dx=2.5, dy=2.5, dz=3.0) positioned at top of trunk.\n"
                   "5. STICKMAN / HUMANOID FIGURES:\n"
                   "   - Create a stickman as a group of connected 3D primitives (head sphere, torso cylinder, 4 limb cylinders for arms and legs).\n"
                   "   - Head: Sphere (radius=0.35m) at Z = target_floor_Z + 2.4m.\n"
                   "   - Torso: Cylinder (radius=0.1m, depth=1.2m) at Z = target_floor_Z + 1.5m.\n"
                   "   - Limbs: 4 x Cylinders (radius=0.06m, depth=0.8m) placed at shoulders and hip joints.\n"
                   "6. CRITICAL NAMING: When you are given a plan, name each Blender object with the EXACT name from the plan line (e.g. if plan says 'BaseSlab:', name the object 'BaseSlab'). "
                   "Use obj.name = 'ExactPlanName' immediately after creating each object. These names are used to verify your result.\n"
                   "7. CLEANUP: At the start of every script, delete all existing mesh objects with: "
                   "[bpy.data.objects.remove(o, do_unlink=True) for o in list(bpy.data.objects) if o.type == 'MESH']")

    builder = PriorityContextBuilder(caps, policy)

    # P0 — Execution Critical
    builder.add_item(0, "user_task", f"Task to accomplish in {name}: {task}")
    if plan:
        builder.add_item(0, "plan", f"Your plan (build exactly this):\n{plan}")
    if error:
        struct_script = preserve_structured_script(last, max_tokens=1500, failing_line=failing)
        struct_err = preserve_structured_error(error, max_tokens=800)
        err_msg = f"Your last script:\n```\n{struct_script}\n```\nIt failed with:\n{struct_err}"
        if failing:
            err_msg += f"\nThe line that failed: `{failing}`. Correct or update it."
        if diagnosis:
            err_msg += f"\nYour diagnosis:\n{diagnosis}"
        builder.add_item(0, "failing_context", err_msg)

    # P1 — Implementation Critical
    if base:
        # F12: a follow-up used to start from an empty scene in a new window. The earlier script now runs first.
        sess_code = preserve_structured_script(base, max_tokens=1000)
        builder.add_item(1, "active_session",
                         f"This changes what you already made in {name}. This script has ALREADY run, so the scene "
                         f"has everything it made:\n```\n{sess_code}\n```\nWrite ONLY the change, using the object "
                         "names above. Don't repeat that script, don't clear the scene, don't remake what exists.")
    if found:
        ex_text = "\n\n".join(f"Working example ({url}):\n```\n{code}\n```" for url, code in found[:2])
        builder.add_item(1, "verified_examples", ex_text)

    # P2 — Useful Knowledge & Research
    if research and error:
        builder.add_item(2, "web_research", f"What you found online about this error:\n{research}")
    if knowledge:
        builder.add_item(2, "learned_knowledge", f"What you learned about {name}:\n{knowledge}")
    if example:
        builder.add_item(2, "past_skill", f"A working script from an earlier task in {name}:\n{example}")

    user_context, report, gen_budget = builder.build()

    body = {
        "model": model_name,
        "stream": False,
        "think": policy.enable_thinking,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_context + ("\n\nFix it and reply with the whole corrected script." if error else "")},
        ],
        "options": {
            "temperature": policy.temperature,
            "num_predict": gen_budget,
        },
        "keep_alive": "30m",
    }

    try:
        data = requests.post(_ollama_chat_url("job"), json=body, timeout=300).json()
    except Exception as exc:
        return fail(f"couldn't write the script ({type(exc).__name__})")

    log.info(f"[Apps] {name}: prompt={report.used_input_tokens}t, gen_budget={gen_budget}t, think={policy.enable_thinking} (window {report.context_window})")

    if data.get("done_reason") == "length" and body["think"]:
        # Live 2026-10-20: a small model's thinking used the whole budget four tries running, before any script.
        # The script itself is short; the same try is asked once more without thinking.
        log.info(f"[Apps] {name}: thinking used the whole budget; asking again without thinking")
        try:
            data = requests.post(_ollama_chat_url("job"), json={**body, "think": False}, timeout=300).json()
        except Exception as exc:
            return fail(f"couldn't write the script ({type(exc).__name__})")
    if data.get("done_reason") == "length":
        return fail("the script generation hit model length limit. Optimize reusable loops or functions while maintaining full task functionality")

    code = code_from(str((data.get("message") or {}).get("content") or ""))
    return code if code and not data.get("error") else fail("the script came back empty")



_ASKS_FOR_FILE = re.compile(
    r"\b(render|save|export|write (?:it )?(?:out|to)|as an? (?:image|picture|file|png|jpe?g)|to my folder|into my folder)\b"
    r"|\.(png|jpe?g|blend|stl|obj|fbx|glb|gltf|step|stp|fcstd|svg|pdf)\b", re.I)
_PATHLIKE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|~|\\\\)|\.(png|jpe?g|exr|tiff?|blend|stl|obj|fbx|glb|gltf|step|stp|fcstd|svg|pdf)$", re.I)


def wants_file(task: str) -> bool:
    """The user's own words ask for a file (render it, save it, an .stl): only then may a script write one (F4)."""
    return bool(_ASKS_FOR_FILE.search(task or ""))


def output_dir(name: str) -> Path:
    """Where a file the user asked for goes: Documents\\MBUDDY\\<app>."""
    from brain import pc_actions
    return pc_actions._base_dir() / slug(name)


def unsafe_python(code: str, _learned_modules: set[str] | None = None, out_dir: Path | None = None) -> str:
    """Why this Python script may not run, or "" when it may. With out_dir (the user asked for a file), saving,
    rendering and exporting are allowed, and every path the script names must be inside out_dir."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"it isn't valid Python (line {exc.lineno}: {exc.msg})"
    return _unsafe_node(tree, out_dir)


def _outside(value: str, out_dir: Path) -> bool:
    try:
        return not Path(value).expanduser().resolve().is_relative_to(out_dir.resolve())
    except (OSError, ValueError):
        return True


def _unsafe_node(tree: ast.AST, out_dir: Path | None = None) -> str:
    """Why this part of a Python script may not run, or "" when it may."""
    for node in ast.walk(tree):
        if out_dir is not None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _PATHLIKE.search(node.value.strip()):
                if _outside(node.value.strip(), out_dir):
                    return (f"it names the path {node.value.strip()[:80]!r}; a file you asked for may only go in "
                            f"{out_dir} (write the full path)")
                continue
            if isinstance(node, ast.Attribute) and re.match(r"(save|export|write)", node.attr):
                continue
            if isinstance(node, ast.keyword) and node.arg and re.match(r"(save|export|write)", node.arg):
                continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in names:
                if not mod or mod.split(".")[0] in _BANNED_MODULES:
                    return f"it imports {mod or 'a relative module'}, which isn't allowed"
        elif isinstance(node, ast.Name) and (node.id in _BANNED_NAMES or node.id in _BANNED_MODULES):
            return f"it uses {node.id}, which isn't allowed"
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"it reaches into {node.attr}, which isn't allowed"
        elif isinstance(node, ast.Attribute) and re.match(r"(save|export|quit|open_mainfile|write)", node.attr):
            return f"it calls {node.attr}, which isn't allowed (nothing is saved or closed without you asking)"
        elif isinstance(node, ast.keyword) and node.arg and re.match(r"(save|export|write)", node.arg):
            # e.g. render(write_still=True) writes a file (seen in an example found online)
            return f"it uses {node.arg}=, which isn't allowed (nothing is saved without you asking)"
    return ""


def _command(template: list[str], exe: str, file: str) -> list[str]:
    import os
    import sys
    from pathlib import Path
    if exe.lower() == "python" or exe.lower() == "python.exe":
        exe = sys.executable
    elif exe.lower().endswith("freecad.exe"):
        fc_cmd = Path(exe).parent / "freecadcmd.exe"
        if fc_cmd.is_file():
            exe = str(fc_cmd)

    cmd = [exe if a == "{exe}" else a.replace("{file}", file) for a in template]
    
    seen_flags = set()
    cleaned = []
    for arg in cmd:
        if arg.startswith("-"):
            if arg in seen_flags:
                continue
            if arg == "-c" and "--console" in seen_flags:
                continue
            if arg == "--console" and "-c" in seen_flags:
                continue
            seen_flags.add(arg)
        cleaned.append(arg)
    return cleaned


def _script_file(name: str, driver: dict, code: str, tag: str) -> Path:
    folder = Path(tempfile.gettempdir()) / "mbuddy" / slug(name)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{tag}_{int(time.time() * 1000)}{driver['file_ext']}"
    path.write_text(code, encoding="utf-8")
    return path


QUIET_WINDOW_S = 5.0   # a window up and the app idle this long: it's waiting on a message


def _kill_tree(pid: int) -> None:
    import psutil
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=3)


def _run_watched(cmd: list[str], timeout: float) -> tuple[int | None, str, str]:
    """Run hidden and watch it: (exit code, or None when it was stopped;
    its output; what its window said). An app that puts its error in a dialog
    never prints it and never exits: live, the runner waited 120 s and debugged
    a timeout while the real message ("option '--console' cannot be specified
    more than once") sat on screen (S116 B-A). A visible window with the app
    idle for QUIET_WINDOW_S is read and the run stopped; a stopped run's whole
    process tree is killed so nothing is left on the user's screen."""
    import psutil
    from brain.pc_windows import window_text, windows_of
    from core import jobs
    out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=out, stderr=err, creationflags=_NO_WINDOW)
    started, quiet_since, last_cpu, shown, code = time.monotonic(), None, None, "", None
    try:
        while True:
            try:
                code = proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                pass
            try:
                root = psutil.Process(proc.pid)
                tree = [root, *root.children(recursive=True)]
            except psutil.Error:
                tree = []
            cpu = 0.0
            for p in tree:
                try:
                    t = p.cpu_times()
                    cpu += t.user + t.system
                except psutil.Error:
                    pass
            hwnds = windows_of({p.pid for p in tree})
            idle = last_cpu is not None and cpu - last_cpu < 0.05
            last_cpu = cpu
            quiet_since = (quiet_since or time.monotonic()) if hwnds and idle else None
            now = time.monotonic()
            if jobs.cancelled_current():   # the job was ended as stalled: its app goes too (F52)
                code = None
                break
            if (quiet_since and now - quiet_since >= QUIET_WINDOW_S) or now - started > timeout:
                shown = "\n\n".join(t for t in (window_text(h) for h in hwnds) if t)
                code = None
                break
    finally:
        if proc.poll() is None:
            _kill_tree(proc.pid)
    texts = []
    for f in (out, err):
        f.seek(0)
        texts.append(f.read().decode("utf-8", errors="replace"))
        f.close()
    return code, "\n".join(texts), shown


def _test(name: str, driver: dict, exe: str, code: str, inspect: str = "") -> tuple[bool, str, str]:
    """(worked, error, "script" or "command"), running the app where the user
    doesn't see it. A "command" error means the app never ran the script (no
    traceback from our file): the way of driving it is wrong, not the script."""
    # The learned inspection runs after the script: the app's real state.
    script = _script_file(name, driver, code + (f"\n\n{inspect}\n" if inspect else ""), "test")
    try:
        returncode, output, shown = _run_watched(_command(driver["test"], exe, str(script)), TEST_TIMEOUT_S)
    finally:
        script.unlink(missing_ok=True)
    _test.last_output = output  # what the script reported it made (RESULT lines)
    ran_ours = script.stem in output
    if returncode is None:
        if shown:
            return False, f"{name} showed a message instead of finishing:\n{shown}", \
                "script" if (ran_ours or script.stem in shown) else "command"
        return False, (f"{name} didn't finish within {TEST_TIMEOUT_S} seconds and showed no message (it was "
                       f"stopped): {output.strip()[-600:]}"), "command"
    done = SimpleNamespace(returncode=returncode)
    for marker in ("Traceback", "Error:", "Exception"):
        if marker in output:
            text = output[output.find(marker) - 400 if output.find(marker) > 400 else 0:].strip()[-1500:]
            return False, text, "script" if ran_ours and "Traceback" in output else "command"
    if done.returncode != 0:
        return False, f"{name} exited with code {done.returncode}: {output.strip()[-800:]}", \
            "script" if ran_ours else "command"
    return True, "", ""


def _fix_command(name: str, driver: dict, knowledge: str, error: str, diagnosis: str = "") -> dict | None:
    """Ask the model to correct how the app is driven, from the app's own error and its diagnosis."""
    from brain.agent import _chat_model, _ollama_chat_url
    ask = (f"Running a script file in {name} with the arguments {json.dumps(driver['show'])} failed. {name} said:\n"
           f"{error[-800:]}\n" + (f"Your diagnosis:\n{diagnosis}\n" if diagnosis else "")
           + f"\nReply with ONLY a JSON list of the correct arguments that make {name} open and run "
           f"the script file, writing {{exe}} for the program and {{file}} for the script.")
    body = {"model": _chat_model(), "stream": False, "think": False,
            "messages": [{"role": "system", "content": f"What you learned about {name}:\n{knowledge}"},
                         {"role": "user", "content": ask}],
            "options": {"temperature": 0, "num_predict": 120}, "keep_alive": "30m"}
    try:
        text = str((requests.post(_ollama_chat_url("job"), json=body, timeout=120).json().get("message") or {})
                   .get("content") or "")
        m = re.search(r"\[.*?\]", text, re.S)
        show = json.loads(m.group(0)) if m else None
    except Exception:
        return None
    fixed = validate_driver({**driver, "show": show, "test": show})
    if fixed is None:
        return None
    extra = [a for a in driver["test"] if a not in driver["show"]]  # the hidden-mode flag it had
    return validate_driver({**fixed, "test": [fixed["show"][0], *extra, *fixed["show"][1:]]})


def _save_driver(name: str, driver: dict) -> None:
    """Keep a corrected driver in the app note, so the correction is learned."""
    from brain import brain_store
    from brain.obsidian_vault import END_MARK, START_MARK, safe_name
    vault = brain_store.get_vault()
    path = vault.base / "Apps" / f"{safe_name(name)}.md" if vault else None
    if path is None or not path.is_file():
        return
    body = vault.read(path)[1]
    start, end = body.find(START_MARK), body.find(END_MARK)
    managed = body[start + len(START_MARK):end].strip() if 0 <= start < end else ""
    block = f"{DRIVER_HEADING}\n```json\n{json.dumps(driver, indent=1)}\n```"
    new = re.sub(re.escape(DRIVER_HEADING) + r"\s*```json\s*\{.*?\}\s*```", lambda _m: block, managed, flags=re.S)
    if new != managed:
        vault.upsert("Apps", name, new)


def _show(name: str, driver: dict, exe: str, code: str) -> bool:
    """Run the working script in front of the user; True when the app's window shows."""
    from brain.pc_windows import list_windows
    script = _script_file(name, driver, code, "run")
    before = {w.hwnd for w in list_windows()}
    from core.launch import open_for_user  # the shown result outlives MBUDDY (U1)
    open_for_user(_command(driver["show"], exe, str(script)), cwd=str(Path(exe).parent))
    words = {name.split()[0].lower(), Path(exe).stem.lower()}
    for _ in range(60):
        time.sleep(1)
        new = [w for w in list_windows() if w.hwnd not in before and any(x in (w.title + " " + w.app).lower() for x in words)]
        if new:
            _SHOWN[slug(name)] = new[0].hwnd
            return True
    return False


# ── the ability ───────────────────────────────────────────────────────────
def use_app(args: dict) -> str:
    from core import ui_settings
    wanted, task = str(args.get("app", "")).strip(), str(args.get("task", "")).strip()
    app = ui_settings.connected_app(wanted)
    if app is None:
        names = ", ".join(a["name"] for a in ui_settings.connected_apps()) or "none yet"
        return fail(f"{wanted or 'that app'} isn't connected. Connect it in Extensions first (connected: {names}).")
    name = app["name"]
    if not task:
        return fail(f"nothing was described to do in {name}.")
    exe = program(app["path"])
    if exe is None:
        return fail(f"I couldn't find {name}'s program at {app['path']}.")
    with _LOCK:
        if name in _busy:
            return f"I'm still working in {name}. I'll tell you when it's done."
        _busy.add(name)
    _knowledge, driver, _path = learned(name)
    reused = _skill(name, task) if driver else None
    # the model says whether this changes what was just made ('now add a roof'); only a recent result can be built on
    session = get_active_session(name) if str(args.get("builds_on_last", "")).lower() in ("true", "1", "yes") else None
    threading.Thread(target=_job, args=(name, exe, task, session), name=f"use:{slug(name)}", daemon=True).start()
    if session:
        return f"Changing what I made in {name} ({session['task'][:60]}). It will open in front of you."
    if reused:
        done = "done" if reused[2] else "tried"  # an unverified skill is only an earlier attempt (U2)
        return f"I've {done} something like this in {name} before. Doing it now; it will open in front of you."
    if driver:
        return f"I know how to use {name}. Doing it now; it will open in front of you."
    return f"That's new to me. Give me a little time to learn how to use {name}, and I'll come back to you."


def _job(name: str, exe: str, task: str, session: dict | None = None) -> None:
    from brain import brain_store
    from core import jobs
    topic = f"{name}: {task}"
    jobs.begin("app", name, task, total=MAX_TRIES)  # what the reply is told while this runs (W1)
    try:
        knowledge, driver, note = learned(name)
        if driver is None:
            jobs.step_current(doing=f"learning how {name} can be driven", phase="planning", act="search")
            log.info(f"[Apps] learning how to drive {name}")
            brain_store.learn_app_now(name)
            knowledge, driver, note = learned(name)
        if driver is None:
            brain_store._announce(f"[NORMAL] I learned about {name} and put it in my notes, but I didn't find a way "
                                  f"to drive it with a script yet, so I can't do that in it myself.", topic, ok=False)
            return
        if not knowledge.startswith("Scripting notes:"):
            jobs.step_current(doing=f"learning how to write scripts for {name}", phase="planning", act="search")
            log.info(f"[Apps] learning how to write scripts for {name}")
            brain_store.learn_scripting_now(name, driver["language"])
            knowledge, driver, note = learned(name)
        reused = _skill(name, task)
        example = ""
        if reused:
            example = reused[1] if reused[2] else (
                "# An earlier attempt of yours, never verified by a check: it may be wrong.\n" + reused[1])
        base = (session or {}).get("code", "")
        code, last, errors, driver, extra = attempts(name, driver, exe, knowledge, task, example, base)
        if jobs.cancelled_current():
            return   # already told it was stopped: nothing is shown or said after that
        if not code:
            if note is not None:
                brain_store.note_used([note], success=False)  # the learning rate: what it knew didn't work
            if extra["runnable"]:
                # It runs but the review says it isn't right: shown, and said plainly.
                shown = _show(name, driver, exe, extra["runnable"])
                _keep(name, task, extra["runnable"], len(errors), errors, worked=False, plan=extra["plan"],
                      problems=extra["problems"], sources=extra["sources"])
                first = extra["problems"].splitlines()[0].lstrip("- ") if extra["problems"] else ""
                brain_store._announce(f"[NORMAL] I got it to run in {name}, but it isn't right yet: {first} "
                                      f"{'It is open so you can see where I got to. ' if shown else ''}"
                                      f"I kept what I learned for next time.", topic, ok=False)
                return
            brain_store._announce(f"[NORMAL] I tried {len(errors)} times, but I couldn't get it to work in {name} yet. "
                                  f"I kept what went wrong in my notes.", topic, ok=False)
            _keep(name, task, last, len(errors), errors, worked=False, plan=extra["plan"], sources=extra["sources"])
            return
        tries = len(errors) + 1
        earlier = _SHOWN.get(slug(name)) if base else None
        shown = _show(name, driver, exe, code)
        # Verified only when code checked the result (coverage of the plan in the
        # app's own report). Without a way to see it, the skill is kept as
        # unverified and nothing is reinforced (U2).
        verified = not extra.get("unchecked")
        _keep(name, task, code, tries, errors, worked=True, plan=extra["plan"], lessons=extra["lessons"],
              sources=extra["sources"], verified=verified)
        brain_store.note_used([p for p in (note, reused[0] if reused else None) if p],
                              success=True if verified else None)
        how = "on the first try" if tries == 1 else f"after {tries} tries"
        where = f"It's open in {name} in front of you now." if shown else f"I started {name}, but its window didn't show up."
        checked = ("and checked it against my plan" if not extra.get("unchecked")
                   else "but I couldn't check how it looks, so please have a look")
        saved = (" The file is in " + ", ".join(str(p) for p in extra["files"][:3]) + "."
                 if extra.get("files") else "")
        if earlier and shown and earlier != _SHOWN.get(slug(name)):
            # one window per task: the earlier one is closed only on the user's yes (closing is a critical action)
            from brain import pc_windows
            if pc_windows.request_hwnd("close", earlier):
                saved += f" The earlier {name} window is still open; shall I close it?"
        brain_store._announce(f"[HAPPY] I'm back. I made it in {name} {how}, {checked}. {where}{saved}", topic, ok=shown)
    except Exception as exc:
        log.warn(f"[Apps] {name} failed: {exc}")
        brain_store._announce(f"[NORMAL] Something went wrong while I was working in {name}.", topic, ok=False)
    finally:
        jobs.finish_current(False, "it stopped unexpectedly")  # only if nothing ended it
        with _LOCK:
            _busy.discard(name)


# ── debugging experience (S108) ───────────────────────────────────────────
DEBUG_KIND = "Debugging experience"


def signature(error: str) -> str:
    """The telling line of an error (the last line naming an error), without
    paths or numbers, so the same problem is recognised next time."""
    lines = [ln.strip() for ln in (error or "").splitlines() if ln.strip()]
    telling = [ln for ln in lines if re.search(r"error|exception|failed|not found|invalid|unknown", ln, re.I)]
    line = (telling or lines or ["unknown error"])[-1]
    line = re.sub(r"[A-Za-z]:\\[^\s'\"]+|/[^\s'\"]+/[^\s'\"]+", "<path>", line)
    return re.sub(r"\d+", "N", line)[:90]


def past_debugging(name: str, error: str) -> str:
    """What MBUDDY remembers about similar problems with this app, fixes that
    didn't work included: those say what not to try again. Read from the
    folder itself, since a failed fix lowers a note below what recall shows
    (live: both notes of the first run fell to 0.2 and went unseen, S109)."""
    from brain import brain_store
    vault = brain_store.get_vault()
    folder = vault.base / DEBUG_KIND if vault else None
    if folder is None or not folder.is_dir():
        return ""
    want = set(re.findall(r"[a-z_]{3,}", signature(error).lower()))
    scored = []
    for path in folder.glob("*.md"):
        meta, body, _ = vault.read(path)
        if not meta or f"mbuddy/app/{slug(name)}" not in [str(t) for t in meta.get("tags") or []]:
            continue
        words = set(re.findall(r"[a-z_]{3,}", (path.stem + " " + body).lower()))
        fix = re.search(r"Fix:\s*(.+)", body)
        result = "worked" if "The fix worked." in body else "did NOT work"
        scored.append((len(want & words), f"- {path.stem.split(' - ', 1)[-1]}: tried \"{(fix.group(1) if fix else '?')[:160]}\", "
                                          f"which {result}"))
    scored.sort(key=lambda s: -s[0])
    return "\n".join(line for score, line in scored[:3] if score > 0)


def failing_line(error: str, script_stem: str = "") -> str:
    """The line of our own script that failed, from the traceback ("" if none)."""
    frames = re.findall(r'File "([^"]+)", line (\d+)[^\n]*\n\s+([^\n]+)', error or "")
    ours = [f for f in frames if (script_stem and script_stem in f[0]) or re.search(r"[\\/](test|run)_\d+\.", f[0])]
    return ours[-1][2].strip() if ours else ""


def _code_part(line: str) -> str:
    return re.sub(r"\s+", "", line.split("#", 1)[0])


def research_error(name: str, driver: dict, error: str) -> tuple[str, list[str]]:
    """Look the error up online, as a developer would: (what the pages say
    about it, their links). Used when an error repeats (S108, S109)."""
    from brain import brain_store
    found = brain_store._search_raw(f"{name} {driver['language']} {signature(error)}")
    if not found:
        return "", []
    pages = {r["href"]: brain_store._read_page(r["href"]) for r in found[:2]}
    text = "\n\n".join(f"[{r.get('title', '')}]\n{brain_store._focus(pages.get(r['href']) or r.get('body', ''), 900)}"
                       for r in found[:2])
    return text, [r["href"] for r in found[:2]]


def diagnose(name: str, driver: dict, knowledge: str, code: str, error: str, past: str, research: str = "") -> str:
    """The model reads the app's own output (and what it found online, and its
    own experience) and works out what went wrong and how to fix it."""
    from brain.agent import _chat_model, _ollama_chat_url
    from brain.model_capabilities import (
        detect_model_capabilities,
        derive_execution_policy,
        TaskComplexity,
        preserve_structured_script,
        preserve_structured_error,
    )

    model_name = _chat_model()
    caps = detect_model_capabilities(model_name)
    policy = derive_execution_policy(caps, TaskComplexity.DEBUGGING, is_retry=True)

    struct_code = preserve_structured_script(code, max_tokens=1500)
    struct_err = preserve_structured_error(error, max_tokens=1000)

    system = (f"You are debugging your own attempt to use {name}.\nWhat you learned about {name}:\n{knowledge}"
              + (f"\n\nYour debugging experience with similar problems (don't repeat fixes that did NOT work):\n{past}"
                 if past else "")
              + (f"\n\nWhat you found online about this error:\n{research}" if research else ""))
    user = (f"You ran this {driver['language']} script in {name} with the arguments {json.dumps(driver['test'])}:\n"
            f"```\n{struct_code}\n```\nIt failed. {name}'s output was:\n{struct_err}\n\n"
            "Read the output and find what went wrong. Answer in exactly three lines:\n"
            "What went wrong: ...\nWhy: ...\nFix: ...")
    body = {
        "model": model_name,
        "stream": False,
        "think": policy.enable_thinking,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"temperature": policy.temperature, "num_predict": 512},
        "keep_alive": "30m",
    }
    try:
        return str((requests.post(_ollama_chat_url("job"), json=body, timeout=120).json().get("message") or {})
                   .get("content") or "").strip()
    except Exception:
        return ""


def remember_debugging(name: str, error: str, diagnosis: str, fixed: bool, research: tuple[str, list[str]] = ("", [])) -> None:
    """Keep the symptom, the diagnosis, what was found online, and whether the
    fix worked; a fix that worked makes the note stronger, one that didn't
    makes it weaker."""
    from brain import brain_store
    vault = brain_store.get_vault()
    if vault is None or not diagnosis:
        return
    tail = "\n".join(error.strip().splitlines()[-6:])
    found = (f"\n\n## Found online\n{research[0]}\n" + "\n".join(f"- {u}" for u in research[1])) if research[0] else ""
    body = (f"## Symptom\n{tail}\n\n## Diagnosis\n{diagnosis}{found}\n\n## Result\n"
            + ("The fix worked." if fixed else "The fix didn't work yet."))
    path = vault.upsert(DEBUG_KIND, f"{name} - {signature(error)}"[:80], body, source="self",
                        tags=["mbuddy/debugging", f"mbuddy/app/{slug(name)}"])
    brain_store.note_used([path], success=fixed)


def attempts(name: str, driver: dict, exe: str, knowledge: str, task: str, example: str, base: str = ""):
    """The learning curve: write, check, test hidden, and fix, up to MAX_TRIES.
    A script error rewrites the script with the error in hand; a command error
    (the app never ran the script) corrects how the app is driven, keeps that
    correction in the app note, and tests the same script again.
    Running without errors isn't success (S110): the script reports what it
    made, and the model reviews that against its plan and the request; a
    mismatch is fixed like an error.
    Returns (checked script or "", last script, errors, driver, extra) where
    extra has the plan, the lessons, and the last script that ran but didn't
    match yet with its problems."""
    from brain import brain_store
    from core import jobs
    errors: list[str] = []
    code, last, diagnosis, failing = "", "", "", ""
    research: tuple[str, list[str]] = ("", [])
    seen_signatures: list[str] = []
    pending = None  # (error, diagnosis) of the last script failure: did the next try fix it?
    check_python = driver["language"].lower().startswith("python")
    started = time.time()
    brain_store._wait_until_idle()
    plan = plan_result(name, task, knowledge, example)
    log.info(f"[Apps] {name}: plan: {plan[:200]!r}")
    # How to see the app's real state: learned once per app and kept.
    inspect = inspector(name) or learn_inspector(name, driver, exe, knowledge)
    # Like a developer, it looks up how others wrote this kind of script first
    # (S111), unless a script of its own for this already worked.
    found = [] if example else verify_examples(name, driver, exe, find_examples(name, driver, task, plan), inspect)
    extra = {"plan": plan, "lessons": [], "runnable": "", "problems": "", "unchecked": not inspect,
             "sources": [link for link, _code in found], "files": []}
    # A file the user asked for (F4: 'render to my folder' was refused every time) may be written, only there
    out = output_dir(name) if wants_file(task) else None
    asked = task
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        asked = (f"{task}\n(The user asked for a file: write it with its full path inside {out} and nowhere else, "
                 f"e.g. {out / 'result'}.<ext>.)")
    seen_scripts: set[str] = set()
    stalled_count = 0
    mismatch = ""  # the review's problems for the last script that ran
    for attempt in range(1, MAX_TRIES + 1):
        if jobs.cancelled_current():
            log.info(f"[Apps] {name}: the job was ended as stalled; stopping")
            break
        if time.time() - started > TIME_BUDGET_S:
            log.info(f"[Apps] {name}: out of time after {attempt - 1} tries")
            break
        if stalled_count >= 2:
            log.info(f"[Apps] {name}: stalled by repeating identical scripts twice. Aborting.")
            break
        brain_store._wait_until_idle()
        jobs.step_current(attempt, doing="writing the script", phase="planning", act="write")
        draft = None
        for _again in range(3):  # a rewrite that keeps the failing line is sent back before it runs
            draft = _compose(name, driver, knowledge, asked, example, last, errors[-1] if errors else "", diagnosis,
                             failing, research[0], plan, found, base)
            if isinstance(draft, ToolFailure) or not failing or _code_part(failing) not in \
                    {_code_part(ln) for ln in draft.splitlines()}:
                break
            log.info(f"[Apps] {name} try {attempt}: the rewrite kept the failing line; asking again")
            diagnosis = (f"{diagnosis}\nYou kept the line that failed: `{failing}`. Change or remove that line; "
                         f"don't just add a comment to it.").strip()
        if isinstance(draft, ToolFailure):
            log.info(f"[Apps] {name} try {attempt}: no script: {str(draft)[:160]}")
            errors.append(str(draft))
            diagnosis, failing = f"What went wrong: {str(draft).removeprefix('FAILED: ')}.", ""
            continue
            
        if draft in seen_scripts:
            stalled_count += 1
            log.info(f"[Apps] {name} try {attempt}: generated exact same script as before (stalled {stalled_count}/2)")
        else:
            seen_scripts.add(draft)
        last = draft
        problem = unsafe_python(draft, out_dir=out) if check_python else "I only run scripts I can check first (Python)"
        if problem:
            log.info(f"[Apps] {name} try {attempt}: refused: {problem}")
            # Show the broken line itself: "invalid syntax at line 52" alone
            # was fixed six times in a row without the model seeing line 52.
            at = re.search(r"line (\d+)", problem)
            bad = draft.splitlines()[int(at.group(1)) - 1].strip() if at and 0 < int(at.group(1)) <= len(draft.splitlines()) else ""
            errors.append(f"The script was refused before running: {problem}." + (f" That line is: `{bad}`" if bad else ""))
            diagnosis = (f"What went wrong: the script was refused.\nWhy: {problem}"
                         + (f", at `{bad}`" if bad else "") + ".\nFix: rewrite that line correctly.")
            failing = bad
            if not check_python:
                break
            continue
        jobs.step_current(attempt, doing="running it hidden to test it", phase="executing", act="")
        run = base + "\n\n" + draft if base else draft   # a follow-up runs on top of what was made
        ok, error, where = _test(name, driver, exe, run, inspect)
        if pending:
            remember_debugging(name, pending[0], pending[1], fixed=ok, research=pending[2])  # judged by the result
            pending = None
        if not ok:
            # MBUDDY debugs itself: the brain first; an error seen before is
            # looked up online; then it reads the output and diagnoses.
            sig = signature(error)
            if sig in seen_signatures or len(seen_signatures) >= 1:
                research = research_error(name, driver, error)
                if research[0]:
                    log.info(f"[Apps] {name} try {attempt}: looked the error up online ({len(research[1])} pages)")
            seen_signatures.append(sig)
            failing = failing_line(error)
            diagnosis = diagnose(name, driver, knowledge, draft, error, past_debugging(name, error), research[0])
            log.info(f"[Apps] {name} try {attempt}: {where} problem at `{failing[:80]}`; diagnosis: {diagnosis[:200]!r}")
            if where == "command":
                fixed = _fix_command(name, driver, knowledge, error, diagnosis)
                if fixed and fixed != driver:
                    log.info(f"[Apps] {name}: corrected how to drive it: {fixed['test']} / {fixed['show']}")
                    driver = fixed
                    _save_driver(name, driver)
                    ok, error2, where2 = _test(name, driver, exe, run, inspect)
                    remember_debugging(name, error, diagnosis, fixed=ok, research=research)
                    if not ok:
                        error, where = error2, where2
                        failing = failing_line(error)
                        diagnosis = diagnose(name, driver, knowledge, draft, error, past_debugging(name, error),
                                             research[0])
                else:
                    remember_debugging(name, error, diagnosis, fixed=False, research=research)
            if not ok and where == "script":
                pending = (error, diagnosis, research)
        log.info(f"[Apps] {name} try {attempt}: {'ran' if ok else 'failed'} ({where}) {error[-160:]!r}")
        jobs.step_current(attempt, doing="checking the result" if ok else "working out what went wrong",
                          phase="evaluating", act="",
                          last="it ran" if ok else "it failed: " + (error.strip().splitlines() or ["unknown"])[-1][:120])
        if ok:
            # It ran. Is it what was asked? The app's own report, checked.
            # Only the app's own report counts; without a way to see it, the
            # result is left unchecked (and said so), never assumed right.
            problems = review_result(name, task, plan, _report(getattr(_test, "last_output", ""))) if inspect else ""
            if out is not None and not problems:
                # read back from the disk, not from the script's word
                extra["files"] = sorted((p for p in out.iterdir() if p.is_file() and p.stat().st_mtime >= started),
                                        key=lambda p: p.stat().st_mtime)
                if not extra["files"]:
                    problems = f"- You asked for a file, but nothing was written to {out}."
            if not problems:
                if mismatch:
                    extra["lessons"].append(mismatch)  # what the review taught, now fixed
                code = run
                record_active_session(name, task, code)
                break
            log.info(f"[Apps] {name} try {attempt}: ran, but the review found: {problems[:240]!r}")
            extra["runnable"], extra["problems"] = run, problems
            mismatch = problems
            error = f"It ran, but it isn't what was asked yet:\n{problems}"
            diagnosis = f"What went wrong: the result doesn't match the request.\nFix these:\n{problems}"
            failing = ""
        errors.append(error)
    if pending:
        remember_debugging(name, pending[0], pending[1], fixed=False, research=pending[2])
    return code, last, errors, driver, extra


def _keep(name: str, task: str, code: str, tries: int, errors: list[str], worked: bool, plan: str = "",
          lessons: list[str] | None = None, problems: str = "", sources: list[str] | None = None,
          verified: bool = False) -> None:
    """The skill note: the task, the plan, how each try went, the lessons the
    review taught, the examples it learned from, and the script (the one that
    was checked, or the last one)."""
    from brain import brain_store
    vault = brain_store.get_vault()
    if vault is None or not code:
        return
    lines = [f"## Task\n{task}"] + ([f"## Plan\n{plan}"] if plan else []) + ["## How it went"]
    for i, e in enumerate(errors, 1):
        what = e.strip().splitlines() or ["unknown error"]
        lines.append(f"- Try {i}: " + ("ran, but it wasn't what was asked yet" if what[0].startswith("It ran")
                                       else f"failed: {what[-1][:160]}"))
    if worked:
        lines.append(f"- Try {tries}: worked, and matched the plan.")
    if lessons:
        lines += ["## Lessons", *[ln for block in lessons for ln in block.splitlines() if ln.strip()]]
    if problems:
        lines += ["## Still wrong", problems]
    if sources:
        lines += ["## Examples it learned from", *[f"- {link}" for link in sources]]
    lines += ["## Script" if worked else "## Last script (not right yet)", "```", code.strip(), "```"]
    if worked and not verified:
        lines.insert(0, "Not verified by a check: MBUDDY couldn't see the result, so this may be wrong.")
    path = vault.upsert("Skills", f"{name} - {task}"[:80], "\n".join(lines), source="self",
                        tags=["mbuddy/skill", f"mbuddy/app/{slug(name)}"] + ([] if worked else ["mbuddy/unfinished"]))
    if worked:
        vault.mark_checked(path, verified)
        # tags only ever merge: a task that failed before and works now isn't 'unfinished' any more
        vault._update_meta(path, lambda m: {**m, "tags": [t for t in m.get("tags") or [] if t != "mbuddy/unfinished"]})


ABILITIES = [
    Ability(
        name="use_app",
        topic="create",
        description="Do or make something inside an app the user connected (learns it first). Not for things no connected app does.",
        params={
            "app": {"type": "string", "description": "the connected app"},
            "task": {"type": "string", "description": "what to do or make in it, in a few words"},
            "builds_on_last": {"type": "boolean",
                               "description": "true when it changes or adds to what was just made in this app "
                                              "('now add a roof'); false for something new"},
        },
        required=["app", "task"],
        risk="low",
        execute=use_app,
        preview=lambda args: f"{args.get('task', '')} in {args.get('app', '')}",
        says_all=True,
    ),
]
