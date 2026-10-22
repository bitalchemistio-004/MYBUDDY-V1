"""Which app does which job on this PC: the user chooses, MBUDDY never assumes.

A role (music, browser) has a chosen provider. A provider is DATA: a name, how
to open it, how to search it, and how much of it can really be controlled. No
provider is preferred in code: the catalog below is just a list the user can
extend, and until they choose, nothing is chosen.

What a provider can do is stated as capability tiers and shown as they are:
  control   what is playing, pause, next, previous (any player that has a
            Windows media session, whatever it is)
  search    open a search for a song or artist in it
  play      start one specific song, only where MBUDDY can find the song and
            then SEE it playing in Windows' media controls

The choice and the user's own providers are saved in ~/.buddy/config/roles.json.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

_lock = threading.RLock()

ROLES = {
    "music": {"label": "Music", "hint": "plays songs, artists and playlists"},
    "browser": {"label": "Browser", "hint": "opens web pages and web players"},
}

# Data, not code paths: {q} is the URL-quoted search text.
CATALOG = [
    {"id": "spotify_app", "role": "music", "name": "Spotify (desktop app)", "kind": "app",
     "detect": {"scheme": "spotify"}, "search": "spotify:search:{q}", "window": {"process": "spotify"}},
    {"id": "spotify_web", "role": "music", "name": "Spotify (web)", "kind": "web",
     "search": "https://open.spotify.com/search/{q}", "window": {"title": "spotify"}},
    {"id": "youtube_music_web", "role": "music", "name": "YouTube Music (web)", "kind": "web",
     "search": "https://music.youtube.com/search?q={q}", "resolver": "youtube_music_watch",
     "window": {"title": "youtube music"}},
    {"id": "apple_music_web", "role": "music", "name": "Apple Music (web)", "kind": "web",
     "search": "https://music.apple.com/search?term={q}", "window": {"title": "apple music"}},
    {"id": "soundcloud_web", "role": "music", "name": "SoundCloud (web)", "kind": "web",
     "search": "https://soundcloud.com/search?q={q}", "window": {"title": "soundcloud"}},
    {"id": "deezer_web", "role": "music", "name": "Deezer (web)", "kind": "web",
     "search": "https://www.deezer.com/search/{q}", "window": {"title": "deezer"}},
    {"id": "tidal_web", "role": "music", "name": "Tidal (web)", "kind": "web",
     "search": "https://listen.tidal.com/search?q={q}", "window": {"title": "tidal"}},
    {"id": "vlc", "role": "music", "name": "VLC media player", "kind": "app",
     "detect": {"exe": [r"VideoLAN\VLC\vlc.exe"]}, "window": {"process": "vlc"}},
    {"id": "foobar2000", "role": "music", "name": "foobar2000", "kind": "app",
     "detect": {"exe": [r"foobar2000\foobar2000.exe"]}, "window": {"process": "foobar2000"}},
    {"id": "chrome", "role": "browser", "name": "Google Chrome", "kind": "app",
     "detect": {"exe": [r"Google\Chrome\Application\chrome.exe"]}, "exe_name": "chrome.exe"},
    {"id": "edge", "role": "browser", "name": "Microsoft Edge", "kind": "app",
     "detect": {"exe": [r"Microsoft\Edge\Application\msedge.exe"]}, "exe_name": "msedge.exe"},
    {"id": "firefox", "role": "browser", "name": "Firefox", "kind": "app",
     "detect": {"exe": [r"Mozilla Firefox\firefox.exe"]}, "exe_name": "firefox.exe"},
    {"id": "brave", "role": "browser", "name": "Brave", "kind": "app",
     "detect": {"exe": [r"BraveSoftware\Brave-Browser\Application\brave.exe"]}, "exe_name": "brave.exe"},
]


def _path() -> Path:
    override = os.environ.get("MBUDDY_ROLES_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "config" / "roles.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("chosen", {})
    data.setdefault("custom", [])
    return data


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ── detecting what is on this PC ─────────────────────────────────────────
def _scheme_registered(scheme: str) -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{scheme}\shell\open\command") as key:
            return bool(winreg.QueryValue(key, None))
    except OSError:
        return False


def _exe_path(relative: str) -> str | None:
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(var)
        if base and os.path.exists(os.path.join(base, relative)):
            return os.path.join(base, relative)
    return None


def available(provider: dict) -> tuple[bool, str]:
    """(is it on this PC, where/why)."""
    if provider.get("kind") == "web":
        return True, "opens in your browser"
    detect = provider.get("detect") or {}
    if "scheme" in detect:
        ok = _scheme_registered(detect["scheme"])
        return ok, "installed" if ok else "not installed"
    for rel in detect.get("exe", []):
        path = _exe_path(rel)
        if path:
            return True, path
    if provider.get("exe") and os.path.exists(provider["exe"]):
        return True, provider["exe"]
    if provider.get("kind") == "custom-web":
        return True, "opens in your browser"
    return (False, "not installed") if detect else (True, "your own")


def tiers(provider: dict) -> dict:
    return {"control": True,                       # any player with a media session
            "search": bool(provider.get("search") or provider.get("exe_args")),
            "play": bool(provider.get("resolver"))}


# ── the catalog plus the user's own ─────────────────────────────────────────
def providers(role: str) -> list[dict]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    out = [dict(p) for p in CATALOG if p["role"] == role]
    out += [dict(p) for p in _load()["custom"] if p.get("role") == role]
    return out


def find(provider_id: str) -> dict | None:
    for p in CATALOG + _load()["custom"]:
        if p["id"] == provider_id:
            return dict(p)
    return None


def chosen(role: str) -> dict | None:
    pid = _load()["chosen"].get(role)
    return find(pid) if pid else None


def choose(role: str, provider_id: str | None) -> dict | None:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    with _lock:
        data = _load()
        if provider_id is None:
            data["chosen"].pop(role, None)
            _save(data)
            return None
        p = find(provider_id)
        if not p or p.get("role") != role:
            raise ValueError("that app isn't an option for this job")
        data["chosen"][role] = provider_id
        _save(data)
        return p


def browser_exe() -> str | None:
    """The program of the browser the user chose (None: the system default opens links)."""
    p = chosen("browser")
    if not p:
        return None
    ok, where = available(p)
    return where if ok and where.lower().endswith(".exe") else None


def match_name(role: str, spoken: str) -> dict | None:
    """The provider the user means by a spoken/typed name ("spotify", "youtube music", "spotify web")."""
    said = set(re.findall(r"[a-z0-9]+", (spoken or "").lower()))
    want_web = bool(said & {"web", "browser", "website", "online"})
    want_app = bool(said & {"app", "desktop", "program"})
    words = said - {"the", "app", "web", "desktop", "player", "music", "browser", "website", "online", "program"}
    if not words:
        return None
    best, best_key = None, (0, 0, 0)
    for p in providers(role):
        pw = set(re.findall(r"[a-z0-9]+", p["name"].lower())) - {"web", "desktop", "app", "player", "media"}
        n = len(words & pw)
        if not n:
            continue
        kind_ok = 1 if (want_web and p["kind"] == "web") or (want_app and p["kind"] == "app") else 0
        prefer = 1 if p["kind"] == "app" and available(p)[0] else 0     # an installed app beats its web version
        key = (n, kind_ok, prefer)
        if key > best_key:
            best, best_key = p, key
    return best


def add_custom(role: str, name: str, kind: str, search: str = "", exe: str = "", exe_args: str = "") -> dict:
    """The user's own provider: a web address with {q}, or a program (and how to give it a search)."""
    name = (name or "").strip()
    if role not in ROLES or not 1 <= len(name) <= 60:
        raise ValueError("a name of 1 to 60 characters is needed")
    if kind == "web":
        if not re.match(r"^https?://", search or "") or "{q}" not in search:
            raise ValueError("a web address that starts with https:// and contains {q} is needed")
        p = {"kind": "web", "search": search.strip(), "window": {"title": name.lower()}}
    elif kind == "app":
        if not exe or not os.path.isfile(exe) or not exe.lower().endswith(".exe"):
            raise ValueError("pick an .exe program that exists")
        p = {"kind": "app", "exe": exe, "window": {"process": Path(exe).stem.lower()}}
        if exe_args.strip():
            if "{q}" not in exe_args:
                raise ValueError("the program's arguments must contain {q}")
            p["exe_args"] = exe_args.strip()
    else:
        raise ValueError("kind must be web or app")
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "app"
    with _lock:
        data = _load()
        ids = {x["id"] for x in data["custom"]} | {x["id"] for x in CATALOG}
        pid, n = f"custom_{slug}", 1
        while pid in ids:
            n += 1
            pid = f"custom_{slug}_{n}"
        p.update({"id": pid, "role": role, "name": name, "custom": True})
        data["custom"].append(p)
        _save(data)
    return p


def remove_custom(provider_id: str) -> bool:
    with _lock:
        data = _load()
        keep = [p for p in data["custom"] if p["id"] != provider_id]
        if len(keep) == len(data["custom"]):
            return False
        data["custom"] = keep
        data["chosen"] = {r: pid for r, pid in data["chosen"].items() if pid != provider_id}
        _save(data)
    return True


_asked: dict[str, float] = {}
ASK_WINDOW_S = 10 * 60


def note_needs_choice(role: str) -> None:
    """A tool needed this role's app and none was chosen (the buddy offers the choice)."""
    import time
    _asked[role] = time.time()


def pending_choice() -> dict | None:
    """{"role": ...} while a role was asked for in the last 10 minutes and still has no chosen app."""
    import time
    for role, at in list(_asked.items()):
        if time.time() - at > ASK_WINDOW_S or chosen(role):
            _asked.pop(role, None)
            continue
        return {"role": role, "label": ROLES[role]["label"]}
    return None


def overview() -> dict:
    """Everything the Apps screen shows: each role, what is chosen, and the honest options."""
    data = _load()
    roles = []
    for rid, meta in ROLES.items():
        options = []
        for p in providers(rid):
            ok, note = available(p)
            options.append({"id": p["id"], "name": p["name"], "kind": p["kind"], "available": ok, "note": note,
                            "custom": bool(p.get("custom")), "tiers": tiers(p)})
        pid = data["chosen"].get(rid)
        roles.append({"id": rid, "label": meta["label"], "hint": meta["hint"], "chosen": pid, "tiers_shown": rid == "music",
                      "options": options})
    return {"roles": roles}
