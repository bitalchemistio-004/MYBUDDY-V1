"""
brain/pc_actions.py - things MBUDDY can do on the PC, in one registry the model can explore.

Each ability has a name, a one-line description for the model, its parameters
(Ollama tool format, like brain/agent.py TOOLS), a risk and an executor:

- read: only looks (read a file, find an app). Runs at once.
- low: small and easy to undo (open an app, a web page, a settings page,
  create a new file that never overwrites anything). Runs at once.
- critical: changes the system or loses something (delete, force-close,
  lock, brightness, power). Never runs on the model's call: it is stored as
  *pending* with a plain question, like brain/pc_windows.py, and only the
  user's yes carries it out. There is no remembered approval: every call asks.

Every result says what really happened, checked where possible (a window
appeared, the file exists, the process is gone). A failure always starts with
"FAILED:" and is a ToolFailure, so the router can tell it from data.

`list_abilities` is the discovery tool: the model calls it to see everything
it can do (these abilities and the existing agent tools) instead of guessing.
Nothing here duplicates the agent's existing tools (volume, media keys,
window focus/minimize/close, reminders, internet search); they are listed
by name so the model knows they exist.
"""
from __future__ import annotations

import difflib
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus, urlparse

from brain.tools import ToolFailure

CONFIRM_WINDOW_S = 60          # same as pc_windows: an unanswered question expires
READ_LIMIT = 4000              # characters of a document handed to the model
_NO_WINDOW = 0x08000000        # CREATE_NO_WINDOW for PowerShell helpers
_S = {"type": "string"}

# Tests (and the lead) can point file creation somewhere else.
BASE_DIR: Path | None = None


def _fail(message: str) -> ToolFailure:
    return ToolFailure(f"FAILED: {message}")


class NeedsConfirmation(str):
    """The text the reply gets for a critical call ("NOT DONE YET ..."), which
    is still a plain str for existing callers; `.pending` is the stored action."""
    pending: "Pending"


# ── registry ────────────────────────────────────────────────────────────
@dataclass
class Ability:
    name: str
    description: str
    risk: str                                   # "read" | "low" | "critical"
    topic: str                                  # apps, web, files, settings, system, power, discovery
    execute: Callable[[dict], str]
    params: dict = field(default_factory=dict)  # JSON-schema properties
    required: list = field(default_factory=list)
    # critical only: args -> (question, describe, plan) or a ToolFailure. It
    # may read the PC but never changes anything.
    prepare: Callable[[dict], object] | None = None

    def spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": list(self.required)},
        }}


REGISTRY: dict[str, Ability] = {}

# Everything opened for the user leaves MBUDDY's job, so closing MBUDDY never
# closes the user's apps (U1). Tests replace this.
from core.launch import open_for_user as _open  # noqa: E402


def _register(name, description, risk, topic, execute, params=None, required=None, prepare=None) -> None:
    assert risk in ("read", "low", "critical")
    assert risk != "critical" or prepare is not None, "critical abilities must ask first"
    params = params or {}
    REGISTRY[name] = Ability(name, description, risk, topic, execute, params,
                             list(params) if required is None else required, prepare)


# The agent's own tools (brain/agent.py), listed so the model knows they exist.
# Read from agent._ABILITY when it can be imported; this copy is the fallback.
_EXISTING_FALLBACK = {
    "adjust_volume": "change the PC volume (up, down, set, mute other apps)",
    "media_control": "pause, resume or skip the music that's playing (asks the user first)",
    "window_action": "bring up, minimize or close an open window (asks the user first)",
    "pc_status": "see which apps are open, what's playing, the volume and battery",
    "add_reminder": "put reminders and calendar events on the user's schedule",
    "set_timer": "start a timer",
    "add_task": "add to the task or shopping list",
    "complete_task": "mark a task or reminder as done",
    "change_reminder": "move a reminder to another time, or make it repeat or happen once",
    "list_tasks": "read the task list",
    "get_time": "tell the time and date",
    "get_weather": "get the weather",
    "web_search": "search the internet and read the results yourself",
    "remember": "remember facts the user tells you",
}


def existing_tools() -> dict[str, str]:
    try:
        from brain import agent
        return dict(agent._ABILITY)
    except Exception:
        return dict(_EXISTING_FALLBACK)


# ── helpers: windows, paths, fuzzy names ────────────────────────────────
def _windows() -> list:
    try:
        from brain.pc_windows import list_windows
        return list_windows()
    except Exception:
        return []


def _wait_for_window(before: set[int], words: list[str], timeout: float = 5.0):
    """A window that appeared after `before`, or one whose app/title has the
    words; None when nothing showed up in time."""
    deadline = time.time() + timeout
    while True:
        for w in _windows():
            hay = f"{w.app} {w.title}".lower()
            if w.hwnd not in before and (not words or any(x in hay for x in words)):
                return w
        for w in _windows():
            hay = f"{w.app} {w.title}".lower()
            if words and all(x in hay for x in words) and w.focused:
                return w
        if time.time() >= deadline:
            return None
        time.sleep(0.25)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_JUNK = ("uninstall", "readme", "release notes", "documentation", "help", "website", "license", "manual")


def _best_match(query: str, names) -> str | None:
    """The name the user most likely means: exact, then prefix, then all the
    words, then a close spelling. Uninstallers and readmes only on request."""
    q = _norm(query)
    if not q:
        return None
    best, best_score = None, 0.0
    for name in names:
        n = _norm(name)
        if not n or (any(j in n for j in _JUNK) and not any(j in q for j in _JUNK)):
            continue
        if n == q:
            score = 1.0
        elif n.startswith(q):
            score = 0.9
        elif all(w in n.split() for w in q.split()):
            score = 0.8
        elif q in n:
            score = 0.7
        else:
            ratio = difflib.SequenceMatcher(None, q, n).ratio()
            score = ratio * 0.8 if ratio >= 0.75 else 0.0
        if score > best_score or (score == best_score and score and len(n) < len(_norm(best))):
            best, best_score = name, score
    return best


def _base_dir(create: bool = False) -> Path:
    """Documents\\MBUDDY (the real Documents folder, even when OneDrive moved it).
    Only made on disk when a file is written into it."""
    if BASE_DIR is not None:
        base = Path(BASE_DIR)
    else:
        docs = None
        try:
            from win32com.shell import shell, shellcon
            docs = shell.SHGetFolderPath(0, shellcon.CSIDL_PERSONAL, None, 0)
        except Exception:
            pass
        base = Path(docs or Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents") / "MBUDDY"
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve_path(raw: str) -> Path | None:
    """An existing file or folder: absolute, or a name in MBUDDY's folder,
    Desktop, Documents or Downloads."""
    text = os.path.expandvars(os.path.expanduser(str(raw or "").strip().strip('"').strip("'")))
    if not text:
        return None
    p = Path(text)
    if p.is_absolute():
        return p if p.exists() else None
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    base = _base_dir()
    docs = base.parent                                   # the real Documents (OneDrive may have moved it)
    parts = p.parts
    # "Documents\\MBUDDY", "MBUDDY\\x", "Documents\\x": a path that starts with a known folder's own name is under it
    # (the field test: "Documents\\MBUDDY" was looked for inside Documents, so MBUDDY said its own folder didn't exist)
    if parts and parts[0].lower() in ("mbuddy",):
        cand = base.joinpath(*parts[1:]) if len(parts) > 1 else base
        if cand.exists():
            return cand
    if parts and parts[0].lower() in ("documents", "my documents"):
        cand = docs.joinpath(*parts[1:]) if len(parts) > 1 else docs
        if cand.exists():
            return cand
    for folder in (base, home / "Desktop", docs, home / "Documents", home / "Downloads", home):
        if (folder / p).exists():
            return folder / p
    return None


def _powershell(script: str, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)


# ── apps ────────────────────────────────────────────────────────────────
def _start_menu_dirs() -> list[Path]:
    return [Path(os.environ.get(var, "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for var in ("ProgramData", "AppData") if os.environ.get(var)]


def _shortcuts() -> dict[str, str]:
    found: dict[str, str] = {}
    for folder in _start_menu_dirs():
        if folder.is_dir():
            for lnk in folder.rglob("*.lnk"):
                found.setdefault(lnk.stem, str(lnk))
    return found


def _app_paths() -> dict[str, str]:
    """HKLM/HKCU ...\\App Paths: e.g. chrome -> C:\\...\\chrome.exe."""
    import winreg
    found: dict[str, str] = {}
    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    sub = winreg.EnumKey(root, i)
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            value = str(winreg.QueryValue(k, None) or "").strip('"')
                        if value and os.path.exists(value):
                            found.setdefault(re.sub(r"\.exe$", "", sub, flags=re.I), value)
                    except OSError:
                        continue
        except OSError:
            continue
    return found


_start_apps_cache: tuple[float, dict] = (0.0, {})


def _start_apps() -> dict[str, str]:
    """Store/UWP apps too (Calculator, Settings, new Notepad): Get-StartApps,
    cached for five minutes; launched through shell:AppsFolder."""
    global _start_apps_cache
    if time.time() - _start_apps_cache[0] < 300 and _start_apps_cache[1]:
        return _start_apps_cache[1]
    found: dict[str, str] = {}
    try:
        out = _powershell("Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }", timeout=15)
        for line in out.stdout.splitlines():
            if "\t" in line:
                name, app_id = line.split("\t", 1)
                if name.strip() and app_id.strip():
                    found.setdefault(name.strip(), "shell:AppsFolder\\" + app_id.strip())
    except Exception:
        pass
    _start_apps_cache = (time.time(), found)
    return found


def app_index() -> dict[str, str]:
    """Display name -> what to launch: Start-menu shortcuts, then App Paths,
    then Store apps."""
    index: dict[str, str] = {}
    for source in (_shortcuts, _app_paths, _start_apps):
        try:
            for name, target in source().items():
                index.setdefault(name, target)
        except Exception:
            continue
    return index


# ── the user's app switches (S93) ─────────────────────────────────────────
def _identity(target: str) -> str:
    """Which program a launch target really is: the executable's name, or a
    Store app's ID. One program has several names in the index ("chrome",
    "Google Chrome"), so switches compare programs, not names."""
    t = str(target or "")
    if t.lower().endswith(".lnk"):
        try:
            import win32com.client
            t = win32com.client.Dispatch("WScript.Shell").CreateShortcut(t).TargetPath or t
        except Exception:
            pass
    if t.lower().startswith("shell:appsfolder"):
        return t.lower()
    return Path(t).stem.lower() if t else ""


def blocked_app(name: str, identity: str = "") -> str | None:
    """The switched-off app (as the user named it in Extensions) that this
    name or program is, or None. Never raises."""
    try:
        from core import ui_settings
        off = [k for k, v in (ui_settings.get("app_rules") or {}).items() if v is False]
        if not off:
            return None
        index = app_index()
        ident = identity.lower().removesuffix(".exe") if identity else ""
        if not ident and name:
            m = _best_match(name, index)
            ident = _identity(index[m]) if m else ""
        for rule in off:
            if _norm(rule) == _norm(name or ""):
                return rule
            key = _norm(rule) if _norm(rule) in index else _best_match(rule, index)
            if key and ident and _identity(index[key]) == ident:
                return rule
    except Exception:
        return None
    return None


def _find_apps(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    names = sorted(app_index(), key=str.lower)
    if not names:
        return _fail("I couldn't read the Start menu.")
    if query:
        q = _norm(query)
        hits = [n for n in names if q in _norm(n)]
        best = _best_match(query, names)
        if best and best not in hits:
            hits.insert(0, best)
        if not hits:
            return f"No installed app matches '{query}'. {len(names)} apps are installed."
        return f"Installed apps matching '{query}': " + ", ".join(hits[:25]) + "."
    return f"{len(names)} installed apps, first 60: " + ", ".join(names[:60]) + "."


def _open_app(args: dict) -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        return _fail("no app name was given.")
    index = app_index()
    match = _best_match(name, index)
    if match is None:
        return _fail(f"no installed app matches '{name}'. Try find_apps to see what's installed.")
    off = blocked_app(match, _identity(index[match]))
    if off:
        return _fail(f"{off} is switched off in your extensions, so I won't open it.")
    before = {w.hwnd for w in _windows()}
    try:
        _open(index[match])
    except OSError as exc:
        return _fail(f"Windows couldn't start {match}: {exc}")
    words = [w for w in _norm(match).split() if len(w) > 2][:2]
    w = _wait_for_window(before, words)
    seen = f"yes, \"{w.title[:60]}\"" if w else "no, not within 5 seconds"
    return f"Opened {match} (window appeared: {seen})."


# ── web ─────────────────────────────────────────────────────────────────
_BROWSER_EXE = {"chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe", "brave": "brave.exe"}


def _browser_exe(browser: str) -> str | None:
    exe = _BROWSER_EXE.get((browser or "chrome").lower().strip())
    if exe is None:
        return None
    try:
        path = _app_paths().get(exe[:-4])
        if path:
            return path
    except Exception:
        pass
    vendor = {"chrome.exe": r"Google\Chrome\Application", "msedge.exe": r"Microsoft\Edge\Application",
              "firefox.exe": "Mozilla Firefox", "brave.exe": r"BraveSoftware\Brave-Browser\Application"}[exe]
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(var)
        if base and os.path.exists(os.path.join(base, vendor, exe)):
            return os.path.join(base, vendor, exe)
    return None


def search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query.strip())


def _open_in_browser(url: str, browser: str | None) -> tuple[bool, str]:
    """(ok, where): the named browser when found, else the default one."""
    from core import roles
    exe = _browser_exe(browser) if browser else roles.browser_exe()
    if exe:
        try:
            _open([exe, url])
            return True, browser.capitalize()
        except OSError:
            pass
    try:
        _open(url)
        ok = True
    except OSError:
        ok = False
    where = "the default browser" + (f" ({browser.capitalize()} wasn't found)" if browser and not exe else "")
    return bool(ok), where


def _browser_search(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return _fail("no search words were given.")
    url = search_url(query)
    before = {w.hwnd for w in _windows()}
    ok, where = _open_in_browser(url, str(args.get("browser") or "") or None)
    if not ok:
        return _fail(f"no browser accepted the search for '{query}'.")
    w = _wait_for_window(before, ["google search"], timeout=4)
    seen = "yes" if w else "not confirmed"
    return f"Opened a Google search for '{query}' in {where} (search tab seen: {seen})."


def _open_url(args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if url and "://" not in url and re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", url, re.I):
        url = "https://" + url
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return _fail(f"'{url}' isn't a web address (only http and https links are opened).")
    ok, where = _open_in_browser(url, None)
    return f"Opened {url} in {where}." if ok else _fail(f"no browser accepted {url}.")


# ── files ───────────────────────────────────────────────────────────────
_KINDS = ("txt", "md", "docx")


def _safe_name(name: str, kind: str) -> str:
    stem = Path(str(name or "").strip()).name           # never a path: always inside the MBUDDY folder
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", stem).strip(" .")
    if stem.lower().endswith("." + kind):
        stem = stem[: -len(kind) - 1]
    return (stem or f"MBUDDY note {time.strftime('%Y-%m-%d %H%M')}")[:120]


def _free_path(folder: Path, stem: str, kind: str) -> Path:
    """report.txt, then report (2).txt, report (3).txt ...: never an existing file."""
    path, n = folder / f"{stem}.{kind}", 2
    while path.exists():
        path, n = folder / f"{stem} ({n}).{kind}", n + 1
    return path


def _create_file(args: dict) -> str:
    kind = str(args.get("kind") or "txt").lower().lstrip(".")
    if kind not in _KINDS:
        return _fail(f"I can create txt, md or docx files, not '{kind}'.")
    content = str(args.get("content") or "")
    if not content.strip() and str(args.get("write_about") or "").strip():
        from brain import composer  # new text is written with room for it (S98)
        content = composer.compose(str(args["write_about"]))
        if isinstance(content, ToolFailure):
            return content
    folder = _base_dir(create=True)
    stem = _safe_name(str(args.get("name", "")), kind)
    for _ in range(20):  # another writer could take the name between the check and the write
        path = _free_path(folder, stem, kind)
        try:
            if kind == "docx":
                from docx import Document
                doc = Document()
                for line in content.splitlines() or [""]:
                    heading = re.match(r"^(#{1,3})\s+(.*)", line)
                    if heading:
                        doc.add_heading(heading.group(2), level=len(heading.group(1)))
                    else:
                        doc.add_paragraph(line)
                with open(path, "xb") as fh:
                    doc.save(fh)
            else:
                with open(path, "x", encoding="utf-8", newline="") as fh:
                    fh.write(content)
            break
        except FileExistsError:
            continue
    else:
        return _fail(f"couldn't find a free name for {stem}.{kind}.")
    if not path.exists():
        return _fail(f"the file {path} wasn't there after saving.")
    return f"Created {path} ({path.stat().st_size} bytes)."


def _read_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(str(path)).paragraphs)
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if ext in (".txt", ".md", ".csv", ".log", ".json", ".ini"):
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"{ext or 'this kind of'} files can't be read (txt, md, docx and pdf can)")


def _read_file(args: dict) -> str:
    raw = str(args.get("path", ""))
    path = _resolve_path(raw)
    if path is None or not path.is_file():
        return _fail(f"there's no file at '{raw}'.")
    try:
        text = _read_text(path).strip()
    except Exception as exc:
        return _fail(f"couldn't read {path.name}: {exc}")
    if not text:
        return f"{path} has no readable text (it may be scanned images)."
    cut = f"\n[truncated: showing {READ_LIMIT} of {len(text)} characters]" if len(text) > READ_LIMIT else ""
    return f"Contents of {path}:\n{text[:READ_LIMIT]}{cut}"


def _list_folder(args: dict) -> str:
    raw = str(args.get("path") or "")
    path = _resolve_path(raw) if raw else _base_dir()
    if not raw and not path.exists():
        return f"{path} doesn't exist yet: MBUDDY hasn't created any files."
    if path is None or not path.is_dir():
        return _fail(f"there's no folder at '{raw}'.")
    # Office's lock files (~$name.docx) are not the user's files
    items = sorted((p for p in path.iterdir() if not p.name.startswith("~$")), key=lambda p: (not p.is_dir(), p.name.lower()))
    shown = [f"{p.name}{'/' if p.is_dir() else ''}" for p in items[:50]]
    more = f" (and {len(items) - 50} more)" if len(items) > 50 else ""
    return f"{path} has {len(items)} items: " + (", ".join(shown) or "nothing") + more + "."


def _take_screenshot(args: dict) -> str:
    """A picture of the whole screen (every monitor), saved as a new PNG in Documents\\MBUDDY\\Screenshots. Nothing
    is overwritten, sent anywhere or opened; the answer names the file so it can be opened or shared."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
    except Exception as exc:
        return _fail(f"Windows wouldn't let me capture the screen: {type(exc).__name__}.")
    folder = _base_dir(create=True) / "Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    path = folder / f"Screenshot {stamp}.png"
    n = 2
    while path.exists():
        path = folder / f"Screenshot {stamp} ({n}).png"
        n += 1
    try:
        img.save(path, "PNG")
    except OSError as exc:
        return _fail(f"I couldn't save the picture: {exc}")
    return f"Saved a screenshot of the whole screen to {path}."


def _cloud_would_see() -> bool:
    """With the cloud brain, what a tool returns goes to the cloud model. Messages and window contents are only given
    to it when the user allowed that in Settings, Messages."""
    from core import cloud_brain, notif_watch
    return bool(cloud_brain.active and not notif_watch.settings()["share_with_cloud"])


def _read_window(args: dict) -> str:
    """What a window on screen shows (its texts, fields and buttons), read through Windows' accessibility interface.
    Read only. With no name it is the window in front of the user."""
    from core import window_reader
    target = str(args.get("app") or args.get("window") or "").strip()
    w = _windows_for_reading(target)
    if w is None:
        return _fail(f"I can't find a window for '{target}'." if target else "I can't see an open window to read.")
    why = window_reader.refuses(w.title, w.app)
    if why:
        return _fail(why)
    if _cloud_would_see():
        return _fail("The cloud brain would see what that window shows, so I don't read it. You can allow that in Settings, Messages.")
    return window_reader.describe(w.title, w.app, window_reader.read(w.hwnd))


def _windows_for_reading(target: str):
    if target:
        from brain.pc_windows import find_window
        return find_window(target)
    own = ("mbuddy", "python", "mbuddy-shell")
    return next((w for w in _windows() if str(w.app).lower().removesuffix(".exe") not in own and w.title.strip()), None)


def _missed_messages(args: dict) -> str:
    """The messages still waiting for an answer, from the apps the user chose in Settings, Messages."""
    from core import notif_watch
    cfg = notif_watch.settings()
    if not cfg["enabled"]:
        return "Watching messages is off. The user can switch it on in Settings, Messages."
    waiting = notif_watch.missed()
    if not waiting:
        return "No messages are waiting."
    names_only = _cloud_would_see()
    lines = []
    for r in waiting[:15]:
        mins = max(1, int((time.time() - r["arrived"]) / 60))
        what = "" if names_only else f": {r['text'][:140]}"
        lines.append(f"{r['app']}, {r['title']}{what} ({mins} min ago{', important' if r['important'] else ''})")
    note = " (Their text isn't shown because the cloud brain is on.)" if names_only else ""
    return f"{len(waiting)} message(s) waiting{note}:\n" + "\n".join(lines)


def _phone_do(args: dict) -> str:
    """Put one action on the user's phone (an alarm, a timer, a calendar event, a call, a message, directions, a search).
    The phone shows it as a button and one tap there makes Android do it. Nothing happens on the phone by itself."""
    from core import phone_actions
    kind = str(args.get("kind", "")).strip().lower()
    params = {k: v for k, v in args.items() if k != "kind"}
    for key in ("hour", "minute", "seconds"):                      # numbers the model wrote as text
        if isinstance(params.get(key), str) and params[key].strip().isdigit():
            params[key] = int(params[key])
    try:
        row = phone_actions.add(kind, params, source="mbuddy")
    except ValueError as exc:
        return _fail(str(exc))
    return f"Put on the phone: {phone_actions.describe(row)}. The user taps it on the phone to do it."


# Opening one of these runs a program or changes the system: never from "open
# this file" (docs/audit/SENIOR_REVIEW.md, C2). Apps go through open_app.
_RUNNABLE = {".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
             ".msi", ".msp", ".scr", ".lnk", ".url", ".hta", ".reg", ".cpl", ".jar", ".pif", ".appref-ms"}


# Only these open from "open this file" (an allow list: many other types run
# code, install or mount when opened; docs/audit/SENIOR_REVIEW.md, M6).
_SAFE_TO_OPEN = {".txt", ".md", ".pdf", ".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ods",
                 ".ppt", ".pptx", ".odp", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".svg",
                 ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".mp4", ".mkv", ".avi", ".mov", ".webm",
                 ".log", ".json", ".xml", ".epub", ".zip"}


def _open_file_or_folder(args: dict) -> str:
    raw = str(args.get("path", ""))
    path = _resolve_path(raw)
    if path is None:
        return _fail(f"there's no file or folder at '{raw}'.")
    if path.is_file() and (path.suffix.lower() in _RUNNABLE or path.suffix.lower() not in _SAFE_TO_OPEN):
        return _fail(f"{path.name} is a program or script, so I won't run it from here. "
                     f"To start an app, ask me to open it by name.")
    before = {w.hwnd for w in _windows()}
    try:
        _open(str(path))
    except OSError as exc:
        return _fail(f"Windows couldn't open {path}: {exc}")
    w = _wait_for_window(before, [], timeout=4)
    seen = f"yes, \"{w.title[:60]}\"" if w else "no, not within 4 seconds"
    return f"Opened {path} (window appeared: {seen})."


# ── settings ────────────────────────────────────────────────────────────
SETTINGS_PAGES = {
    "home": "ms-settings:", "display": "ms-settings:display", "sound": "ms-settings:sound",
    "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi",
    "network": "ms-settings:network-status", "vpn": "ms-settings:network-vpn",
    "notifications": "ms-settings:notifications", "focus": "ms-settings:focus",
    "battery": "ms-settings:batterysaver", "power": "ms-settings:powersleep",
    "storage": "ms-settings:storagesense", "apps": "ms-settings:appsfeatures",
    "default apps": "ms-settings:defaultapps", "startup apps": "ms-settings:startupapps",
    "windows update": "ms-settings:windowsupdate", "privacy": "ms-settings:privacy",
    "background": "ms-settings:personalization-background", "colors": "ms-settings:colors",
    "themes": "ms-settings:themes", "lock screen": "ms-settings:lockscreen",
    "taskbar": "ms-settings:taskbar", "mouse": "ms-settings:mousetouchpad",
    "keyboard": "ms-settings:typing", "printers": "ms-settings:printers",
    "date and time": "ms-settings:dateandtime", "language": "ms-settings:regionlanguage",
    "accounts": "ms-settings:yourinfo", "about": "ms-settings:about",
    "night light": "ms-settings:nightlight", "microphone": "ms-settings:privacy-microphone",
    "camera": "ms-settings:privacy-webcam",
}


def _open_settings(args: dict) -> str:
    page = str(args.get("page") or "home").strip()
    key = _best_match(page, SETTINGS_PAGES)
    if key is None:
        return _fail(f"there's no settings page I know called '{page}'. Pages: {', '.join(SETTINGS_PAGES)}.")
    before = {w.hwnd for w in _windows()}
    try:
        _open(SETTINGS_PAGES[key])
    except OSError as exc:
        return _fail(f"Windows couldn't open the {key} settings: {exc}")
    w = _wait_for_window(before, ["settings"], timeout=4)
    return f"Opened Settings > {key} (Settings window seen: {'yes' if w else 'no, not within 4 seconds'})."


# ── critical: asked first, every time ───────────────────────────────────
def _brightness_now() -> int | None:
    out = _powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
    digits = re.findall(r"\d+", out.stdout or "")
    return int(digits[0]) if digits else None


def _prepare_brightness(args: dict):
    try:
        percent = int(round(float(args.get("percent"))))
    except (TypeError, ValueError):
        return _fail("no brightness level was given.")
    if not 0 <= percent <= 100:
        return _fail(f"brightness goes from 0 to 100, not {percent}.")
    return (f"Change the screen brightness to {percent}%?",
            f"change the screen brightness to {percent}%", {"percent": percent, "target": "the screen brightness"})


def _set_brightness(plan: dict) -> str:
    percent = int(plan["percent"])
    out = _powershell("(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                      f".WmiSetBrightness(1, {percent})")
    if out.returncode != 0:
        return _fail("Windows wouldn't change the brightness (external monitors often don't allow it).")
    time.sleep(0.5)
    now = _brightness_now()
    if now is None:
        return _fail("the brightness couldn't be read back, so I can't say it changed.")
    if abs(now - percent) > 5:
        return _fail(f"I asked for {percent}%, but the screen reports {now}%.")
    return f"Screen brightness is now {now}%."


_PROTECTED_DIRS = ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")


def _prepare_delete(args: dict):
    raw = str(args.get("path", ""))
    path = _resolve_path(raw)
    if path is None:
        return _fail(f"there's no file at '{raw}', so nothing would be deleted.")
    if not path.is_file():
        return _fail(f"{path} is a folder; I only delete single files.")
    resolved = str(path.resolve()).lower()
    for var in _PROTECTED_DIRS:
        root = os.environ.get(var)
        if root and resolved.startswith(str(Path(root).resolve()).lower() + os.sep):
            return _fail(f"{path.name} is a system or program file, so I won't delete it.")
    return (f"Delete {path.name} from {path.parent}? It goes to the Recycle Bin.",
            f"delete {path.name} (it goes to the Recycle Bin)", {"path": str(path), "target": path.name})


def _recycle(path: str) -> None:
    try:
        from send2trash import send2trash
        send2trash(path)
        return
    except ImportError:
        pass
    from win32com.shell import shell, shellcon
    flags = (shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT
             | shellcon.FOF_NOERRORUI | shellcon.FOF_WANTNUKEWARNING)   # warn if it can't be recycled
    code, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, path, None, flags, None, None))
    if code != 0 or aborted:
        raise OSError(f"the shell returned {code}{' (cancelled)' if aborted else ''}")


def _delete_file(plan: dict) -> str:
    path = plan["path"]
    if not os.path.exists(path):
        return _fail(f"{plan['target']} was already gone.")
    try:
        _recycle(path)
    except Exception as exc:
        return _fail(f"couldn't move {plan['target']} to the Recycle Bin: {exc}")
    if os.path.exists(path):
        return _fail(f"{plan['target']} is still there after the delete.")
    return f"Moved {path} to the Recycle Bin."


_PROTECTED_PROCS = {"system", "registry", "idle", "csrss", "wininit", "winlogon", "services", "lsass", "smss",
                    "svchost", "explorer", "dwm", "fontdrvhost", "sihost", "ctfmon", "memory compression",
                    "python", "pythonw", "mbuddy-shell", "msedgewebview2", "ollama", "ollama app"}


def _processes(target: str) -> tuple[str, list]:
    """(process name, processes) for "Word", "chrome", "spotify.exe": the open
    window's app first (friendly names), then the process name itself."""
    import psutil
    t = re.sub(r"\.exe$", "", (target or "").strip().lower())
    exe = t
    try:
        from brain.pc_windows import find_window
        w = find_window(target)
        if w is not None and w.app:
            exe = w.app.lower()
    except Exception:
        pass
    procs = []
    for p in psutil.process_iter(["name", "pid"]):
        name = re.sub(r"\.exe$", "", (p.info.get("name") or ""), flags=re.I).lower()
        if name == exe:
            procs.append(p)
    return exe, procs


def _prepare_kill(args: dict):
    target = str(args.get("name", "")).strip()
    if not target:
        return _fail("no app name was given.")
    exe, procs = _processes(target)
    if exe in _PROTECTED_PROCS:
        return _fail(f"{exe} is part of Windows or of MBUDDY itself, so I won't force it closed.")
    if not procs:
        return _fail(f"no running app matches '{target}'.")
    off = blocked_app(target, exe)
    if off:
        return _fail(f"{off} is switched off in your extensions, so I won't touch it.")
    count = f" ({len(procs)} processes)" if len(procs) > 1 else ""
    return (f"Force-close {exe}{count}? Anything unsaved in it will be lost.",
            f"force-close {exe}{count} (unsaved work in it is lost)",
            {"name": exe, "pids": [p.pid for p in procs], "target": exe})


def _kill_app(plan: dict) -> str:
    import psutil
    alive = []
    for pid in plan["pids"]:
        try:
            p = psutil.Process(pid)
            if re.sub(r"\.exe$", "", p.name(), flags=re.I).lower() == plan["name"]:  # not a reused pid
                alive.append(p)
        except psutil.Error:
            continue
    if not alive:
        return _fail(f"{plan['name']} had already closed.")
    for p in alive:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _gone, still = psutil.wait_procs(alive, timeout=3)
    if still:
        return _fail(f"{len(still)} of {len(alive)} {plan['name']} processes are still running.")
    return f"Force-closed {plan['name']} ({len(alive)} process{'es' if len(alive) > 1 else ''} ended)."


def _prepare_lock(args: dict):
    return ("Lock the PC now?", "lock the PC", {"target": "the PC"})


def _lock_screen(plan: dict) -> str:
    import ctypes
    if not ctypes.windll.user32.LockWorkStation():
        return _fail("Windows refused to lock the PC.")
    return "Windows accepted the lock request; the PC is locking."


_POWER = {"shutdown": ("Shut down the PC in 30 seconds?", "shut down the PC in 30 seconds"),
          "restart": ("Restart the PC in 30 seconds?", "restart the PC in 30 seconds"),
          "sleep": ("Put the PC to sleep now?", "put the PC to sleep")}


def _prepare_power(args: dict):
    action = str(args.get("action", "")).lower().strip()
    if action not in _POWER:
        return _fail(f"power can shutdown, restart or sleep, not '{action}'.")
    question, describe = _POWER[action]
    extra = " Save your work first." if action != "sleep" else ""
    return question + extra, describe, {"action": action, "target": "the PC"}


def _power(plan: dict) -> str:
    """Off unless MBUDDY_ALLOW_POWER=1 is set, and never under pytest."""
    if os.environ.get("MBUDDY_ALLOW_POWER") != "1" or "PYTEST_CURRENT_TEST" in os.environ:
        return _fail("shutting down, restarting and sleeping aren't switched on in MBUDDY yet.")
    command = {"shutdown": ["shutdown", "/s", "/t", "30"], "restart": ["shutdown", "/r", "/t", "30"],
               "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]}[plan["action"]]
    done = subprocess.run(command, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if done.returncode != 0:
        return _fail(f"Windows refused to {plan['action']}: {(done.stderr or done.stdout).strip()[:120]}")
    return ("The PC shuts down in 30 seconds (\"shutdown /a\" cancels it)." if plan["action"] == "shutdown" else
            "The PC restarts in 30 seconds (\"shutdown /a\" cancels it)." if plan["action"] == "restart" else
            "Windows accepted the sleep request.")


# ── discovery ───────────────────────────────────────────────────────────
def _list_abilities(args: dict) -> str:
    topic = _norm(str(args.get("topic") or ""))
    items = list(REGISTRY.values())
    if topic:
        chosen = [a for a in items if topic in a.topic or topic in _norm(a.name) or topic in _norm(a.description)]
        note = "" if chosen else f"(nothing matched '{topic}', so here is everything)\n"
        items = chosen or items
    else:
        note = ""
    lines = [note + "What you can do on this PC. risk: read = only looks; low = runs at once; "
             "critical = the user is asked yes or no every single time before anything happens."]
    for topic_name in dict.fromkeys(a.topic for a in items):
        lines.append(f"[{topic_name}]")
        for a in (a for a in items if a.topic == topic_name):
            params = ", ".join(a.params)
            lines.append(f"- {a.name}({params}) [{a.risk}]: {a.description}")
    tools = existing_tools()
    lines.append("Your other tools, use them directly: " + "; ".join(f"{n} ({d})" for n, d in tools.items()) + ".")
    if topic and "setting" in topic:
        lines.append("Settings pages: " + ", ".join(SETTINGS_PAGES) + ".")
    lines.append("Not possible yet: playing a specific song, sending messages or email, reading the screen, "
                 "clicking inside other apps. Say so plainly if asked.")
    return "\n".join(lines)


# ── the registry itself ─────────────────────────────────────────────────
_register("list_abilities", "See everything you can do on this PC, with risks. Call it first when you're "
          "not sure whether or how you can do something. Optional topic: apps, web, files, settings, system, power.",
          "read", "discovery", _list_abilities,
          {"topic": {"type": "string", "description": "optional: apps, web, files, settings, system or power"}},
          required=[])
_register("find_apps", "List the apps installed on this PC, or those matching a name.", "read", "apps",
          _find_apps, {"query": {"type": "string", "description": "part of an app name; empty lists them all"}},
          required=[])
_register("open_app", "Open an installed app by name, e.g. 'Notepad', 'Calculator'.",
          "low", "apps", _open_app, {"name": _S})
_register("browser_search", "Open a Google search in the user's browser for them to look at. "
          "To read results yourself, use web_search instead.", "low", "web", _browser_search,
          {"query": _S, "browser": {"type": "string", "enum": list(_BROWSER_EXE),
                                    "description": "only if the user named one"}}, required=["query"])
_register("open_url", "Open a web address (http or https) in the default browser.", "low", "web", _open_url,
          {"url": _S})
_register("create_file", "Create a new file in Documents\\MBUDDY; never overwrites. kind: txt, md or docx "
          "(Word). content = the exact short text; for something new and long, describe it in write_about.",
          "low", "files", _create_file,
          {"name": {"type": "string", "description": "file name without folder"},
           "content": {"type": "string", "description": "the exact text (short, like a list)"},
           "write_about": {"type": "string", "description": "something new to write (a story, a letter), in a few words"},
           "kind": {"type": "string", "enum": list(_KINDS)}},
          required=["name"])
_register("read_file", "Read the text of a txt, md, docx or pdf file (a full path, or a name in "
          "Documents\\MBUDDY, Desktop, Documents or Downloads).", "read", "files", _read_file, {"path": _S})
_register("list_folder", "List the files in a folder (default: Documents\\MBUDDY).", "read", "files",
          _list_folder, {"path": {"type": "string", "description": "folder path; empty for Documents\\MBUDDY"}},
          required=[])
_register("take_screenshot", "Take a screenshot of the whole screen and save it as a new picture in Documents\\MBUDDY\\Screenshots "
          "(it is not sent anywhere).", "low", "files", _take_screenshot, {}, required=[])
_register("read_window", "Read what a window on screen shows (its texts, fields and buttons). Give the app name, or leave it empty "
          "for the window in front. Read only.", "read", "messages", _read_window,
          {"app": {"type": "string", "description": "app or window name; empty for the window in front"}}, required=[])
_register("missed_messages", "Which messages are waiting for an answer (from the apps the user chose to watch), who wrote and "
          "how long ago.", "read", "messages", _missed_messages, {}, required=[])
_register("phone_do", "Put an action on the user's PHONE: kind is alarm (hour, minute, label), timer (seconds, label), calendar "
          "(title, start as 2026-10-09T15:30, end, place, notes), call, sms or whatsapp (number, body), navigate (place), search (query), "
          "email (to, subject, body). The user taps it on the phone.", "low", "phone", _phone_do,
          {"kind": {"type": "string", "enum": list(__import__("core.phone_actions", fromlist=["KINDS"]).KINDS)},
           "hour": {"type": "number"}, "minute": {"type": "number"}, "seconds": {"type": "number"}, "label": {"type": "string"},
           "title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "place": {"type": "string"}, "notes": {"type": "string"},
           "number": {"type": "string"}, "body": {"type": "string"}, "query": {"type": "string"}, "to": {"type": "string"}, "subject": {"type": "string"}},
          required=["kind"])
_register("open_file_or_folder", "Open a file in its usual app, or a folder in Explorer.", "low", "files",
          _open_file_or_folder, {"path": _S})
_register("open_settings", "Open a Windows Settings page: display, sound, bluetooth, wifi, notifications, "
          "battery, power, apps, windows update, privacy, background, themes, mouse, keyboard and more.",
          "low", "settings", _open_settings, {"page": _S})
_register("set_brightness", "Set the laptop screen brightness (0-100). Asks the user first.", "critical",
          "system", _set_brightness, {"percent": {"type": "number"}}, prepare=_prepare_brightness)
_register("delete_file", "Move a file to the Recycle Bin. Asks the user first.", "critical", "files",
          _delete_file, {"path": _S}, prepare=_prepare_delete)
_register("kill_app", "Force-close a running app that is stuck. Asks the user first. "
          "To close a window normally, use window_action.", "critical", "system", _kill_app,
          {"name": _S}, prepare=_prepare_kill)
_register("lock_screen", "Lock the PC. Asks the user first.", "critical", "system", _lock_screen,
          prepare=_prepare_lock)
_register("power", "Shut down, restart or sleep the PC. Asks the user first.", "critical", "power", _power,
          {"action": {"type": "string", "enum": list(_POWER)}}, prepare=_prepare_power)


# The connectors (brain/connectors: clipboard, calendar, music) use the same
# entry shape, so they share this registry, its gate and list_abilities.
_ASK_FIRST = {"clipboard_write", "calendar_import_ics"}  # overwrites what's copied; adds many reminders
# Not offered to the model (S91): for "play it" it picked music_search, which
# only opens a results page, and the reply said "*plays*". music_play plays.
_NOT_OFFERED = {"music_search"}


def _add_connectors() -> None:
    from brain import connectors
    for ability in connectors.all_abilities():
        if ability.name in _NOT_OFFERED:
            continue
        if ability.name in _ASK_FIRST:  # a copy: the connector module keeps its own entry
            import dataclasses
            ability = dataclasses.replace(ability, risk="critical", prepare=ability.prepare or connectors.prepare_from_preview(ability))
        REGISTRY.setdefault(ability.name, ability)


_add_connectors()


def tool_specs() -> list[dict]:
    """The abilities in brain/agent.py TOOLS format, for the model."""
    return [a.spec() for a in REGISTRY.values()]


# Names the agent should not write to action_log when the call returns:
# reads aren't actions, and a critical call has only asked (resolve() logs it).
NOT_LOGGED = tuple(n for n, a in REGISTRY.items() if a.risk in ("read", "critical"))


# ── the gate ────────────────────────────────────────────────────────────
@dataclass
class Pending:
    action: str        # ability name
    args: dict         # the plan prepare() made; exactly what runs on yes
    question: str      # "Delete report.txt? It goes to the Recycle Bin."
    text: str          # "delete report.txt (it goes to the Recycle Bin)"
    created: float
    expires: float

    def describe(self) -> str:
        """Verb phrase, like pc_windows.Pending.describe ("Want me to {describe()}?")."""
        return self.text


_pending: Pending | None = None
_lock = threading.Lock()


def _publish() -> None:
    """Show the question in the app, like pc_windows; when this module has
    nothing waiting, leave pc_windows' own question showing."""
    try:
        from core.status import runtime_status
        p = pending()
        if p is None:
            from brain import pc_windows
            p = pc_windows.pending()
        runtime_status.update(pending_action={"text": p.describe(), "expires": p.expires} if p else {})
    except Exception:
        pass


def pending() -> Pending | None:
    global _pending
    with _lock:
        if _pending and time.time() > _pending.expires:
            _pending = None
        return _pending


def cancel() -> None:
    global _pending
    with _lock:
        _pending = None
    _publish()


def run(name: str, args: dict | None = None, confirmed: bool = False) -> str:
    """
    Run one ability. read/low run now and report what really happened.
    critical: without confirmed=True nothing is done; the action is stored
    as pending and a NeedsConfirmation text tells the reply to ask. The model's
    own calls must always come here with confirmed=False; confirmed=True is
    only for code that has the user's yes for this exact call (resolve()).
    """
    ability = REGISTRY.get(name)
    if ability is None:
        return _fail(f"there is no ability called {name}. Call list_abilities to see what exists.")
    args = dict(args or {})
    if not _extension_on(ability):
        return _fail(f"that is switched off in your extensions ({ability.topic}).")
    missing = [k for k in ability.required if str(args.get(k, "")).strip() == ""]
    if missing:
        return _fail(f"{name} needs {', '.join(missing)}.")
    try:
        if ability.risk != "critical":
            return ability.execute(args)
        prepared = ability.prepare(args)
        if isinstance(prepared, ToolFailure):
            return prepared
        question, text, plan = prepared
        if not confirmed:
            return _ask(name, question, text, plan)
        return ability.execute(plan)
    except Exception as exc:
        return _fail(f"{name} hit an error: {exc}")


def _extension_on(ability) -> bool:
    try:
        from core import ui_settings
        return ui_settings.extension_enabled(ability.topic)
    except Exception:
        return True


def drop_pending() -> None:
    """Forget the waiting question without publishing (pc_windows asked a newer one)."""
    global _pending
    with _lock:
        _pending = None


def _ask(name: str, question: str, text: str, plan: dict) -> NeedsConfirmation:
    global _pending
    try:
        from brain import pc_windows
        pc_windows.drop_pending()  # one question at a time: the newest (C3)
    except Exception:
        pass
    now = time.time()
    p = Pending(name, plan, question, text, now, now + CONFIRM_WINDOW_S)
    with _lock:
        _pending = p
    _publish()
    out = NeedsConfirmation(f"NOT DONE YET - waiting for the user's yes. Ask exactly: \"{question}\" "
                            f"Do not say it is done.")
    out.pending = p
    return out


def resolve(text_or_decision: str) -> tuple[str, dict, str] | None:
    """
    Settle the pending action with the user's answer, like pc_windows.resolve:
    a clear yes runs exactly what was asked about, a no cancels it, anything
    else returns None and the question keeps waiting until it expires.
    Returns (ability name, args with a "target", result).
    """
    global _pending
    p = pending()
    if p is None:
        return None
    from brain.pc_windows import decision
    d = text_or_decision if text_or_decision in ("yes", "no") else decision(text_or_decision)
    if d is None:
        return None
    with _lock:
        # Two answers at once (voice and the app's button) run it only once,
        # and answering this question never drops a newer one.
        if getattr(p, "claimed", False):
            return None
        p.claimed = True
        if _pending is p:
            _pending = None
    _publish()
    args = dict(p.args)
    args.setdefault("target", p.text)
    if d == "no":
        return (p.action, args, f"Cancelled: the user said no, so nothing was done ({p.text}).")
    try:
        if not _extension_on(REGISTRY[p.action]):  # switched off while the question waited
            return (p.action, args, _fail("that was switched off in your extensions, so nothing was done."))
        result = REGISTRY[p.action].execute(p.args)
    except Exception as exc:
        result = _fail(f"{p.action} hit an error: {exc}")
    try:
        from brain import action_log
        action_log.record(p.action, args, result)
    except Exception:
        pass
    return (p.action, args, result)
