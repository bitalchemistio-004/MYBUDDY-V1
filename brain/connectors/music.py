"""
Music connector: what's playing, and playing or searching for music in the app
the USER chose (core/roles.py). No music service is built in as the default.

- What's playing, pause/skip: Windows' media session (GSMTC), read by
  brain/pc_windows.now_playing. Any player that has a media session works,
  whichever one it is.
- Searching opens the chosen provider's search (a web address or a program's
  link) through the Windows shell. It does not press play.
- Playing one specific song works only where the provider can find that song
  by itself (a resolver, part of the provider's data). MBUDDY then says
  "Playing" only when Windows' media controls show that new song playing.
  Everywhere else it says plainly that it opened a search and you pick.
- No app is chosen until the user chooses one (Apps I can use, or "use <app>
  for music"). Until then the abilities say so and list what is on this PC.
- Every verified play is noted locally (~/.buddy/activity/plays.jsonl) so that
  later suggestions come from what the user really plays.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from urllib.parse import quote, quote_plus

from brain.connectors import Ability, fail
from brain.tools import ToolFailure
from core import roles

WINDOW_WAIT_S = 6.0
PLAY_WAIT_S = 20.0   # live: a cold browser took longer than 12 s to show the song

# Opens outside MBUDDY's job, so the music keeps playing if MBUDDY closes (U1).
from core.launch import open_for_user as _startfile  # noqa: E402  (tests replace this)
_sleep = time.sleep


def _windows():
    from brain.pc_windows import list_windows
    return list_windows()


def _now_playing():
    from brain.pc_windows import now_playing
    return now_playing()


def _playing_now() -> list[dict]:
    return [m for m in _now_playing() if m.get("status") == "playing"]


# ── resolvers: how a provider finds ONE song (data names them) ───────────────
_VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')


def _http_get(url: str) -> str:
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
               "Accept-Language": "en-US,en;q=0.9"}
    return requests.get(url, headers=headers, timeout=8).text


def find_video_id(query: str) -> str | None:
    """The first video the site's own search returns for the query."""
    found = _VIDEO_ID.search(_http_get("https://www.youtube.com/results?search_query=" + quote_plus(query)))
    return found.group(1) if found else None


def _resolve_youtube_music_watch(query: str) -> str | None:
    video = find_video_id(query)
    return f"https://music.youtube.com/watch?v={video}" if video else None


RESOLVERS = {"youtube_music_watch": _resolve_youtube_music_watch}


# ── the chosen provider ──────────────────────────────────────────────────────
def _needs_choice() -> ToolFailure:
    roles.note_needs_choice("music")
    ok = [p["name"] for p in roles.providers("music") if roles.available(p)[0]]
    listed = ", ".join(ok[:8]) if ok else "none found"
    return fail("NEEDS CHOICE: no music app is chosen yet. Ask the user which one to use for music "
                f"(available on this PC: {listed}). They can pick it in Apps I can use, or tell you "
                "\"use <name> for music\" and you set it with choose_app.")


def search_uri(provider: dict, query: str) -> str | None:
    template = provider.get("search")
    if not template:
        return None
    quoter = quote if template.startswith(("spotify:", "music:")) else quote_plus
    return template.replace("{q}", quoter(query, safe=""))


def _open(provider: dict, query: str, target: str | None) -> None:
    """Open the provider's link, or start its program with the search."""
    if target:
        exe = roles.browser_exe() if target.startswith(("http://", "https://")) else None
        _startfile([exe, target] if exe else target)
    elif provider.get("exe") and provider.get("exe_args"):
        _startfile([provider["exe"], provider["exe_args"].replace("{q}", query)])
    else:
        _startfile([provider["exe"]])


def _window_seen(provider: dict) -> bool:
    hint = provider.get("window") or {}
    for w in _windows():
        if hint.get("process") and w.app.lower() == hint["process"]:
            return True
        if hint.get("title") and hint["title"] in w.title.lower():
            return True
    return False


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def _log_play(query: str, provider: dict, item: dict) -> None:
    """A verified play, for the taste profile later. Local only; never raises."""
    try:
        from core import activity
        line = {"t": datetime.now().isoformat(timespec="seconds"), "query": query, "provider": provider["id"],
                "title": item.get("title", ""), "artist": item.get("artist", "")}
        with open(activity.root() / "plays.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def search_music(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return fail("No song, artist or album was given to search for.")
    provider = roles.chosen("music")
    if provider is None:
        return _needs_choice()
    ok, why = roles.available(provider)
    if not ok:
        return fail(f"{provider['name']} is chosen for music but {why} on this PC. Choose another app in Apps I can use.")
    target = search_uri(provider, query)
    if not target and not provider.get("exe"):
        return fail(f"{provider['name']} can't be searched from here. It can be controlled (pause, skip) when it is playing.")
    _open(provider, query, target)
    waited = 0.0
    while waited < WINDOW_WAIT_S:
        if _window_seen(provider):
            return (f"Opened a {provider['name']} search for \"{query}\". It isn't playing yet; pick a result there.")
        _sleep(0.5)
        waited += 0.5
    return fail(f"I asked Windows to open a {provider['name']} search for \"{query}\", but no {provider['name']} window appeared.")


def play_music(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return fail("No song, artist or album was given to play.")
    provider = roles.chosen("music")
    if provider is None:
        return _needs_choice()
    ok, why = roles.available(provider)
    if not ok:
        return fail(f"{provider['name']} is chosen for music but {why} on this PC. Choose another app in Apps I can use.")
    resolver = RESOLVERS.get(provider.get("resolver") or "")
    if resolver is None:
        # This provider can't start one specific song by itself: open its search, honestly.
        return search_music(args)
    try:
        target = resolver(query)
    except Exception as exc:
        return fail(f"I couldn't reach {provider['name']} to find \"{query}\" ({type(exc).__name__}).")
    if not target:
        return fail(f"{provider['name']} found nothing for \"{query}\".")
    before = {(m.get("title"), m.get("app")) for m in _playing_now()}
    _open(provider, query, target)
    waited = 0.0
    while waited < PLAY_WAIT_S:
        _sleep(0.5)
        waited += 0.5
        new = [m for m in _playing_now() if (m.get("title"), m.get("app")) not in before]
        if new:
            m = new[0]
            by = f" by {m['artist']}" if m.get("artist") else ""
            _log_play(query, provider, m)
            return f"Playing \"{m['title']}\"{by} in {provider['name']}."
    return (f"NOT PLAYING: I opened \"{query}\" in {provider['name']}, but Windows doesn't show it playing, "
            f"so the browser may have held it back. Press play there.")


def choose_app(args: dict) -> str:
    """The user said which app to use for a job ("use Spotify for music")."""
    role = str(args.get("role") or "music").strip().lower()
    name = str(args.get("app", "")).strip()
    if role not in roles.ROLES:
        return fail(f"I can only set: {', '.join(roles.ROLES)}.")
    p = roles.match_name(role, name)
    if p is None:
        options = ", ".join(x["name"] for x in roles.providers(role))
        return fail(f"I don't know an app called \"{name}\" for {roles.ROLES[role]['label'].lower()}. Options: {options}. "
                    "The user can also add their own in Apps I can use.")
    ok, why = roles.available(p)
    if not ok:
        return fail(f"{p['name']} {why} on this PC.")
    roles.choose(role, p["id"])
    return f"From now on {roles.ROLES[role]['label'].lower()} uses {p['name']}."


def now_playing_text(args: dict) -> str:
    items = _now_playing()
    if not items:
        return "Windows' media controls report nothing playing."
    return "\n".join(
        f"\"{m['title']}\"" + (f" by {m['artist']}" if m.get("artist") else "") + f" in {m['app']} ({m['status']})"
        for m in items)


def _name() -> str:
    p = roles.chosen("music")
    return p["name"] if p else "your music app"


def _preview_search(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    p = roles.chosen("music")
    if p is None:
        return f"search for \"{query}\" (it will ask which music app to use first)"
    return f"open {p['name']} and search for \"{query}\""


ABILITIES = [
    Ability(
        name="music_play",
        topic="music",
        description="Play a song, artist or kind of music now, in the music app the user chose.",
        params={"query": {"type": "string", "description": "song, artist, or kind of music"}},
        required=["query"],
        risk="low",
        execute=play_music,
        preview=lambda args: f"play \"{str(args.get('query', '')).strip()}\" in {_name()}",
    ),
    Ability(
        name="music_now_playing",
        topic="music",
        description="Say which song is playing and in which app, as Windows' media controls report it.",
        risk="read",
        execute=now_playing_text,
    ),
    Ability(
        name="music_search",
        topic="music",
        description="Only open a search results page (plays nothing) for a song, artist or album, "
                    "when the user wants to browse or choose.",
        params={"query": {"type": "string", "description": "song, artist or album"}},
        required=["query"],
        risk="low",
        execute=search_music,
        preview=_preview_search,
    ),
    Ability(
        name="choose_app",
        topic="music",
        description="Set which app to use for music (or the browser) from now on, when the user says so.",
        params={"app": {"type": "string", "description": "the app or service the user named"},
                "role": {"type": "string", "enum": list(roles.ROLES), "description": "default music"}},
        required=["app"],
        risk="low",
        execute=choose_app,
        preview=lambda args: f"use {str(args.get('app', '')).strip()} for {str(args.get('role') or 'music')}",
    ),
]
