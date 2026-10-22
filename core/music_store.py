"""What the user plays: listens, a taste profile, playlists, and suggestions.

All of it is learned from real behaviour and kept on this PC (~/.buddy/music/):

- listens.jsonl: one line per track the user listened to, in ANY player that has
  a Windows media session: how long it was played, whether it was finished or
  skipped. Only recorded when the user turned "learn my taste" on.
- The taste profile is computed from those lines, never from a guess. An item
  (an artist, a time of day) is only shown once it has repeated evidence, and
  every item shows its evidence. The user can forget any item; a forgotten item
  is never used or shown again.
- Playlists are the user's own lists of tracks (a title and artist, with the
  words to find them). They work with any player: playing one plays its tracks
  one by one through the chosen app.
- Suggestions: the local model proposes tracks like the ones the user finishes,
  and each one is CHECKED before it is offered as playable: only where the
  chosen app can find a song by itself (a "play" tier) is a suggestion marked
  found. Otherwise it is offered as an idea to search, labelled unchecked.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

_lock = threading.RLock()

SKIP_S = 30              # left before this many seconds: a skip
FINISHED = 0.8           # played this share of the track: finished
MIN_EVIDENCE = 2         # an item needs this many listens before it is shown as learned
SETTINGS_DEFAULT = {"learn": False}


def root() -> Path:
    override = os.environ.get("MBUDDY_MUSIC_DIR")
    path = Path(override) if override else Path.home() / ".buddy" / "music"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(name: str, default):
    try:
        return json.loads((root() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(name: str, data) -> None:
    path = root() / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def settings() -> dict:
    out = dict(SETTINGS_DEFAULT)
    saved = _read("settings.json", {})
    if isinstance(saved.get("learn"), bool):
        out["learn"] = saved["learn"]
    return out


def set_learn(on: bool) -> dict:
    if not isinstance(on, bool):
        raise ValueError("learn must be true or false")
    with _lock:
        _write("settings.json", {"learn": on})
    return settings()


# ── listens ─────────────────────────────────────────────────────────────
def _key(title: str, artist: str) -> str:
    return re.sub(r"\s+", " ", f"{artist} - {title}".strip().lower())


def load_listens(limit: int = 3000) -> list[dict]:
    path = root() / "listens.jsonl"
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _append(line: dict) -> None:
    with _lock, open(root() / "listens.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


class Watcher:
    """Turns a stream of media-session snapshots into listens.

    tick(now, sessions) is given the sessions as pure data
    ([{app, title, artist, status, position_s, duration_s}]) so it is tested
    with a fake clock. A listen is closed when the track changes or stops."""

    def __init__(self):
        self.cur: dict | None = None

    def tick(self, now: float, sessions: list[dict]) -> None:
        playing = next((s for s in sessions if s.get("status") == "playing" and s.get("title")), None)
        key = _key(playing["title"], playing.get("artist", "")) if playing else None
        if self.cur and key != self.cur["key"]:
            self._close(now)
        if playing and not self.cur:
            self.cur = {"key": key, "title": playing["title"], "artist": playing.get("artist", ""), "app": playing.get("app", ""),
                        "start": now, "played": 0.0, "last": now, "duration": 0, "pos": 0}
        if playing and self.cur:
            self.cur["played"] += max(0.0, min(now - self.cur["last"], 15.0))
            self.cur["last"] = now
            self.cur["duration"] = int(playing.get("duration_s") or self.cur["duration"] or 0)
            self.cur["pos"] = int(playing.get("position_s") or self.cur["pos"] or 0)
        elif self.cur:
            self.cur["last"] = now

    def _close(self, now: float) -> None:
        c, self.cur = self.cur, None
        if not c or c["played"] < 3:
            return
        duration = c["duration"]
        share = (min(c["pos"], duration) / duration) if duration else None
        listened = c["played"]
        finished = bool(share is not None and (share >= FINISHED or (duration and listened >= FINISHED * duration)))
        skipped = bool(not finished and listened < SKIP_S)
        _append({"t": datetime.fromtimestamp(c["start"]).isoformat(timespec="seconds"), "title": c["title"], "artist": c["artist"],
                 "app": c["app"], "listened_s": int(listened), "duration_s": duration, "finished": finished, "skipped": skipped})

    def flush(self, now: float) -> None:
        if self.cur:
            self._close(now)


WATCHER = Watcher()
_thread: threading.Thread | None = None
_stop = threading.Event()


def _loop() -> None:
    from brain.pc_windows import now_playing
    while not _stop.wait(5):
        try:
            if not settings()["learn"]:
                WATCHER.cur = None
                continue
            WATCHER.tick(time.time(), now_playing())
        except Exception:
            pass
    WATCHER.flush(time.time())


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="mbuddy-music-watch", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


# ── taste ──────────────────────────────────────────────────────────────
def _blocked() -> set[str]:
    return set(_read("blocked.json", []))


def forget(item_id: str) -> None:
    with _lock:
        blocked = _blocked() | {item_id}
        _write("blocked.json", sorted(blocked))


def unforget_all() -> None:
    with _lock:
        _write("blocked.json", [])


def profile(listens: list[dict] | None = None) -> dict:
    """The user's taste, from listens. Every item carries its evidence."""
    listens = load_listens() if listens is None else listens
    blocked = _blocked()
    by_artist: dict[str, dict] = defaultdict(lambda: {"listens": 0, "finished": 0, "skipped": 0, "tracks": Counter()})
    hours = Counter()
    for l in listens:
        a = (l.get("artist") or "").strip()
        if a:
            e = by_artist[a]
            e["listens"] += 1
            e["finished"] += bool(l.get("finished"))
            e["skipped"] += bool(l.get("skipped"))
            e["tracks"][l.get("title", "")] += 1
        try:
            hours[datetime.fromisoformat(l["t"]).hour] += bool(l.get("finished"))
        except (KeyError, ValueError):
            pass
    liked, disliked = [], []
    for name, e in by_artist.items():
        iid = f"artist:{name.lower()}"
        if iid in blocked or e["listens"] < MIN_EVIDENCE:
            continue
        item = {"id": iid, "kind": "artist", "name": name, "listens": e["listens"], "finished": e["finished"],
                "skipped": e["skipped"], "top_track": e["tracks"].most_common(1)[0][0]}
        if e["skipped"] > e["finished"] and e["skipped"] >= MIN_EVIDENCE:
            disliked.append(item)
        elif e["finished"] >= 1:
            liked.append(item)
    liked.sort(key=lambda x: (-x["finished"], -x["listens"], x["name"]))
    disliked.sort(key=lambda x: (-x["skipped"], x["name"]))
    times = []
    total = sum(hours.values())
    if total >= 6:
        parts = {"morning": range(5, 12), "afternoon": range(12, 18), "evening": range(18, 23), "night": list(range(23, 24)) + list(range(0, 5))}
        for name, hs in parts.items():
            n = sum(hours[h] for h in hs)
            iid = f"time:{name}"
            if n >= max(MIN_EVIDENCE, 0.5 * total) and iid not in blocked:
                times.append({"id": iid, "kind": "time", "name": name, "finished": n})
    return {"liked": liked[:12], "disliked": disliked[:8], "times": times, "listens": len(listens),
            "finished": sum(bool(l.get("finished")) for l in listens), "skipped": sum(bool(l.get("skipped")) for l in listens)}


# ── playlists ──────────────────────────────────────────────────────────
def playlists() -> list[dict]:
    data = _read("playlists.json", [])
    return data if isinstance(data, list) else []


def _new_id(existing: list[dict]) -> str:
    n = 1
    ids = {p["id"] for p in existing}
    while f"pl{n}" in ids:
        n += 1
    return f"pl{n}"


def create_playlist(name: str, tracks: list[dict] | None = None) -> dict:
    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("a playlist needs a name of 1 to 60 characters")
    with _lock:
        lists = playlists()
        if any(p["name"].lower() == name.lower() for p in lists):
            raise ValueError("a playlist with that name exists")
        pl = {"id": _new_id(lists), "name": name, "created": datetime.now().isoformat(timespec="seconds"), "tracks": []}
        lists.append(pl)
        _write("playlists.json", lists)
    for t in tracks or []:
        pl = add_track(pl["id"], t.get("title", ""), t.get("artist", ""))
    return pl


def add_track(pl_id: str, title: str, artist: str = "") -> dict:
    title, artist = (title or "").strip(), (artist or "").strip()
    if not title or len(title) > 200 or len(artist) > 200:
        raise ValueError("a track needs a title (up to 200 characters)")
    with _lock:
        lists = playlists()
        pl = next((p for p in lists if p["id"] == pl_id), None)
        if pl is None:
            raise KeyError(pl_id)
        if not any(_key(t["title"], t.get("artist", "")) == _key(title, artist) for t in pl["tracks"]):
            pl["tracks"].append({"title": title, "artist": artist, "added": datetime.now().isoformat(timespec="seconds")})
        _write("playlists.json", lists)
        return pl


def remove_track(pl_id: str, index: int) -> dict:
    with _lock:
        lists = playlists()
        pl = next((p for p in lists if p["id"] == pl_id), None)
        if pl is None or not 0 <= index < len(pl["tracks"]):
            raise KeyError(pl_id)
        pl["tracks"].pop(index)
        _write("playlists.json", lists)
        return pl


def delete_playlist(pl_id: str) -> bool:
    with _lock:
        lists = playlists()
        keep = [p for p in lists if p["id"] != pl_id]
        if len(keep) == len(lists):
            return False
        _write("playlists.json", keep)
        return True


def query_for(track: dict) -> str:
    return f"{track.get('artist', '')} {track.get('title', '')}".strip()


# ── suggestions ─────────────────────────────────────────────────────────
def _ask_model(liked: list[dict], avoid: list[dict], n: int) -> list[dict]:
    """The local model proposes tracks like the ones the user finishes. Returns [{title, artist}]."""
    import requests
    from brain.agent import _chat_model, _ollama_chat_url
    likes = "; ".join(f"{a['name']} (e.g. {a['top_track']})" for a in liked[:6])
    dislikes = ", ".join(a["name"] for a in avoid[:4]) or "none"
    schema = {"type": "object", "properties": {"tracks": {"type": "array", "maxItems": n, "items": {
        "type": "object", "properties": {"title": {"type": "string"}, "artist": {"type": "string"}},
        "required": ["title", "artist"]}}}, "required": ["tracks"]}
    body = {"model": _chat_model(), "stream": False, "think": False, "format": schema,
            "messages": [{"role": "system", "content": "You suggest real, well-known songs. Only real songs that exist; if unsure, leave it out."},
                         {"role": "user", "content": f"The listener finishes songs by: {likes}. They skip: {dislikes}. "
                                                     f"Suggest {n} different real songs they might like, from other artists than the ones listed."}],
            "options": {"temperature": 0.4, "num_predict": 300}, "keep_alive": "30m"}
    try:
        reply = requests.post(_ollama_chat_url("background"), json=body, timeout=120).json().get("message", {}).get("content", "")
        tracks = json.loads(reply).get("tracks", [])
    except Exception:
        return []
    out = []
    for t in tracks:
        if isinstance(t, dict) and str(t.get("title", "")).strip() and str(t.get("artist", "")).strip():
            out.append({"title": t["title"].strip()[:120], "artist": t["artist"].strip()[:120]})
    return out[:n]


def suggestions(n: int = 4, ask=_ask_model, verify=None) -> dict:
    """Ideas from the profile. `verify(query)` returns True/False where the chosen app can check a song
    exists, or None when it cannot: then the idea is offered as unchecked. Never invents evidence."""
    p = profile()
    if len(p["liked"]) < 2:
        return {"ok": True, "items": [], "why": "I need to see you finish songs by at least two artists first."}
    already = {_key(l.get("title", ""), l.get("artist", "")) for l in load_listens()}
    items = []
    for t in ask(p["liked"], p["disliked"], n + 2):
        if _key(t["title"], t["artist"]) in already:
            continue
        seen = verify(query_for(t)) if verify else None
        if seen is False:
            continue                                   # the app can't find it: not offered
        items.append({**t, "checked": seen is True,
                      "why": "like " + ", ".join(a["name"] for a in p["liked"][:2])})
    return {"ok": True, "items": items[:n], "why": ""}
