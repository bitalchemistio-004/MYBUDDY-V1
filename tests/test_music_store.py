import asyncio
from datetime import datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import music_api
from core import music_store as ms
from core import roles


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("MBUDDY_MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("MBUDDY_ROLES_PATH", str(tmp_path / "roles.json"))
    monkeypatch.setattr(ms, "WATCHER", ms.Watcher())
    monkeypatch.setattr(music_api, "_require_local", lambda request: None)
    monkeypatch.setattr(music_api.music_store, "start", lambda: None)


def song(title, artist, pos, dur=200, status="playing"):
    return [{"app": "player", "title": title, "artist": artist, "status": status, "position_s": pos, "duration_s": dur}]


def play(w, t0, title, artist, seconds, dur=200, end_pos=None):
    t = t0
    while t < t0 + seconds:
        w.tick(t, song(title, artist, int(t - t0), dur))
        t += 5
    w.tick(t0 + seconds, song(title, artist, end_pos if end_pos is not None else int(seconds), dur))
    return t0 + seconds


T0 = datetime(2026, 9, 30, 21, 0).timestamp()


def test_finished_and_skipped_listens_come_from_real_time_played():
    w = ms.Watcher()
    t = play(w, T0, "Slow River", "Kaya", 190, end_pos=190)               # played to the end
    t = play(w, t, "Loud One", "Rex", 12, end_pos=12)                     # left after 12 s
    w.tick(t + 5, [])                                                       # nothing playing: closes the last listen
    rows = ms.load_listens()
    assert [(r["title"], r["finished"], r["skipped"]) for r in rows] == [("Slow River", True, False), ("Loud One", False, True)]


def test_a_paused_track_is_not_counted_as_listened_time():
    w = ms.Watcher()
    w.tick(T0, song("A", "X", 0))
    w.tick(T0 + 10, song("A", "X", 10))
    w.tick(T0 + 20, song("A", "X", 10, status="paused"))
    w.tick(T0 + 5000, song("A", "X", 10, status="paused"))
    w.tick(T0 + 5010, [])
    rows = ms.load_listens()
    assert rows and rows[0]["listened_s"] <= 25


def test_taste_needs_repeated_evidence_shows_it_and_can_be_forgotten():
    w = ms.Watcher()
    t = T0
    for title in ("One", "Two", "Three"):
        t = play(w, t, title, "Kaya", 190, end_pos=190) + 5
    t = play(w, t, "Solo", "Once", 190, end_pos=190) + 5           # a single listen is not a taste
    for i in range(3):
        t = play(w, t, f"Nope{i}", "Rex", 8, end_pos=8) + 5
    w.tick(t + 10, [])
    p = ms.profile()
    assert [a["name"] for a in p["liked"]] == ["Kaya"] and p["liked"][0]["finished"] == 3
    assert [a["name"] for a in p["disliked"]] == ["Rex"]
    assert "Once" not in [a["name"] for a in p["liked"]]
    ms.forget("artist:kaya")
    assert ms.profile()["liked"] == []
    ms.unforget_all()
    assert ms.profile()["liked"][0]["name"] == "Kaya"


def test_nothing_is_suggested_without_evidence_and_unfindable_ideas_are_dropped():
    assert ms.suggestions()["items"] == []
    w = ms.Watcher()
    t = T0
    for who in ("Kaya", "Lumi"):
        for title in ("a", "b"):
            t = play(w, t, f"{who}{title}", who, 190, end_pos=190) + 5
    w.tick(t + 10, [])
    ideas = [{"title": "Real Song", "artist": "Other"}, {"title": "Made Up", "artist": "Nobody"}, {"title": "Fresh", "artist": "Kaya"}, {"title": "Kayaa", "artist": "Kaya"}]
    got = ms.suggestions(3, ask=lambda liked, avoid, n: ideas, verify=lambda q: "Made Up" not in q)
    assert [i["title"] for i in got["items"]] == ["Real Song", "Fresh"]      # a song already listened to is never suggested
    assert all(i["checked"] for i in got["items"])
    unchecked = ms.suggestions(3, ask=lambda liked, avoid, n: ideas, verify=lambda q: None)
    assert len(unchecked["items"]) == 3 and not any(i["checked"] for i in unchecked["items"])


def test_playlists_work_for_any_provider():
    pl = ms.create_playlist("Focus")
    with pytest.raises(ValueError):
        ms.create_playlist("focus")
    ms.add_track(pl["id"], "Slow River", "Kaya")
    ms.add_track(pl["id"], "slow river", "kaya")                     # the same track is not added twice
    assert [t["title"] for t in ms.playlists()[0]["tracks"]] == ["Slow River"]
    assert ms.query_for(ms.playlists()[0]["tracks"][0]) == "Kaya Slow River"
    ms.remove_track(pl["id"], 0)
    assert ms.playlists()[0]["tracks"] == []
    assert ms.delete_playlist(pl["id"]) and not ms.delete_playlist(pl["id"])


def test_the_api_plays_through_the_chosen_app_and_learning_is_opt_in(monkeypatch):
    calls = []
    from brain.connectors import music
    monkeypatch.setattr(music, "play_music", lambda args: calls.append(args["query"]) or "NEEDS CHOICE: none chosen")
    app = web.Application()
    music_api.register(app)

    async def go():
        async with TestClient(TestServer(app)) as c:
            s = await (await c.get("/ui/music")).json()
            assert s["ok"] and s["learn"] is False and s["provider"] is None and s["playlists"] == []
            assert (await (await c.post("/ui/music/learn", json={"on": True})).json())["learn"] is True
            assert (await c.post("/ui/music/learn", json={"on": "yes"})).status == 400
            r = await (await c.post("/ui/music/play", json={"query": "jazz"})).json()
            assert calls == ["jazz"] and r["ok"] is True                       # the connector's text decides, not the API
            assert (await c.post("/ui/music/play", json={"query": " "})).status == 400
            made = await (await c.post("/ui/music/playlists", json={"name": "Focus"})).json()
            pid = made["created"]
            await c.post(f"/ui/music/playlists/{pid}/tracks", json={"title": "Slow River", "artist": "Kaya"})
            played = await (await c.post(f"/ui/music/playlists/{pid}/play", json={"index": 0})).json()
            assert calls[-1] == "Kaya Slow River" and "text" in played
            assert (await c.post(f"/ui/music/playlists/{pid}/play", json={"index": 5})).status == 404
            assert (await c.post("/ui/music/playlists", json={"name": ""})).status == 400
            assert (await c.delete("/ui/music/playlists", json={"id": "nope"})).status == 404
            sug = await (await c.get("/ui/music/suggestions")).json()
            assert sug["items"] == [] and "at least two artists" in sug["why"]
    asyncio.run(go())
