"""api/music_api.py - the Music page: play, playlists, what MBUDDY learned of the
user's taste, and suggestions (core/music_store.py).

Playing here is the user's own click. It goes through the same music ability
the model uses, so it plays in the app the user chose and says only what it can
verify.
"""
from __future__ import annotations

import asyncio

from aiohttp import web

from api.device_control import _require_local
from core import music_store, roles


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Send a JSON object.")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Send a JSON object.")
    return data


def _provider() -> dict | None:
    p = roles.chosen("music")
    if not p:
        return None
    return {"id": p["id"], "name": p["name"], "tiers": roles.tiers(p), "available": roles.available(p)[0]}


def _state() -> dict:
    return {"ok": True, "learn": music_store.settings()["learn"], "profile": music_store.profile(),
            "playlists": music_store.playlists(), "provider": _provider()}


def _play(query: str) -> dict:
    from brain.connectors import music
    from brain.tools import ToolFailure
    out = music.play_music({"query": query})
    return {"ok": not isinstance(out, ToolFailure) and not str(out).startswith("NOT PLAYING"), "text": str(out)}


def _verify(query: str):
    """True/False where the chosen app can find a song by itself; None where it cannot."""
    from brain.connectors import music
    p = roles.chosen("music")
    resolver = music.RESOLVERS.get((p or {}).get("resolver") or "")
    if resolver is None:
        return None
    try:
        return bool(resolver(query))
    except Exception:
        return None


async def state_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json(await asyncio.to_thread(_state))


async def learn_handler(request: web.Request) -> web.Response:
    _require_local(request)
    try:
        await asyncio.to_thread(music_store.set_learn, (await _body(request)).get("on"))
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json(await asyncio.to_thread(_state))


async def play_handler(request: web.Request) -> web.Response:
    _require_local(request)
    query = str((await _body(request)).get("query", "")).strip()
    if not query or len(query) > 200:
        return _json({"ok": False, "error": "Type a song, an artist or a kind of music (up to 200 characters)."}, 400)
    return _json(await asyncio.to_thread(_play, query))


async def playlists_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await _body(request)
    if request.method == "DELETE":
        done = await asyncio.to_thread(music_store.delete_playlist, str(data.get("id") or ""))
        return _json(await asyncio.to_thread(_state) if done else {"ok": False, "error": "No such playlist."}, 200 if done else 404)
    try:
        pl = await asyncio.to_thread(music_store.create_playlist, data.get("name"))
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({**(await asyncio.to_thread(_state)), "created": pl["id"]})


async def tracks_handler(request: web.Request) -> web.Response:
    _require_local(request)
    pl_id = request.match_info["pl_id"]
    data = await _body(request)
    try:
        if request.method == "DELETE":
            await asyncio.to_thread(music_store.remove_track, pl_id, data.get("index", -1))
        else:
            await asyncio.to_thread(music_store.add_track, pl_id, data.get("title"), data.get("artist") or "")
    except KeyError:
        return _json({"ok": False, "error": "No such playlist or track."}, 404)
    except (ValueError, TypeError) as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json(await asyncio.to_thread(_state))


async def play_playlist_handler(request: web.Request) -> web.Response:
    """Play one track of a playlist (the first by default) through the chosen app."""
    _require_local(request)
    data = await _body(request)
    pl = next((p for p in music_store.playlists() if p["id"] == request.match_info["pl_id"]), None)
    index = data.get("index", 0)
    if pl is None or isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(pl["tracks"]):
        return _json({"ok": False, "error": "No such track."}, 404)
    return _json(await asyncio.to_thread(_play, music_store.query_for(pl["tracks"][index])))


async def add_current_handler(request: web.Request) -> web.Response:
    """Put what is playing now into a playlist (created by name if it doesn't exist)."""
    _require_local(request)
    data = await _body(request)

    def run():
        from brain.pc_windows import now_playing
        now = next((m for m in now_playing() if m.get("status") == "playing"), None) or next(iter(now_playing()), None)
        if not now:
            return {"ok": False, "error": "Nothing is playing right now."}, 400
        lists = music_store.playlists()
        pl = next((p for p in lists if p["id"] == data.get("playlist")), None)
        if pl is None:
            name = str(data.get("name") or "").strip()
            if not name:
                return {"ok": False, "error": "Pick a playlist or name a new one."}, 400
            pl = next((p for p in lists if p["name"].lower() == name.lower()), None) or music_store.create_playlist(name)
        music_store.add_track(pl["id"], now["title"], now.get("artist", ""))
        return {**_state(), "added": {"title": now["title"], "playlist": pl["name"]}}, 200
    try:
        body, status = await asyncio.to_thread(run)
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json(body, status)


async def forget_handler(request: web.Request) -> web.Response:
    _require_local(request)
    if request.method == "DELETE":
        await asyncio.to_thread(music_store.unforget_all)
    else:
        item = str((await _body(request)).get("id") or "")
        if not item or len(item) > 120:
            return _json({"ok": False, "error": "id must be an item of your taste."}, 400)
        await asyncio.to_thread(music_store.forget, item)
    return _json(await asyncio.to_thread(_state))


async def suggestions_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json(await asyncio.to_thread(lambda: music_store.suggestions(4, verify=_verify)))


def register(app: web.Application) -> None:
    app.router.add_get("/ui/music", state_handler)
    app.router.add_post("/ui/music/learn", learn_handler)
    app.router.add_post("/ui/music/play", play_handler)
    app.router.add_post("/ui/music/playlists", playlists_handler)
    app.router.add_delete("/ui/music/playlists", playlists_handler)
    app.router.add_post("/ui/music/playlists/{pl_id}/tracks", tracks_handler)
    app.router.add_delete("/ui/music/playlists/{pl_id}/tracks", tracks_handler)
    app.router.add_post("/ui/music/playlists/{pl_id}/play", play_playlist_handler)
    app.router.add_post("/ui/music/add-current", add_current_handler)
    app.router.add_post("/ui/music/forget", forget_handler)
    app.router.add_delete("/ui/music/forget", forget_handler)
    app.router.add_get("/ui/music/suggestions", suggestions_handler)

    async def start(_app):
        music_store.start()

    async def stop(_app):
        music_store.stop()
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
