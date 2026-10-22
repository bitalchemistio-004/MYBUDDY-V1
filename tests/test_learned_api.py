import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import learned_api
from brain import brain_store
from brain.obsidian_vault import Vault


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_MUSIC_DIR", str(tmp_path / "music"))      # never the real listens
    vault = Vault(tmp_path / "vault")
    vault.init()
    vault.upsert("Facts", "Pet", "The cat is called Biscuit.", source="user")
    vault.upsert("Debugging experience", "Lists", "Lists go to text files.", source="mbuddy")
    monkeypatch.setattr(brain_store, "get_vault", lambda: vault)
    monkeypatch.setattr(learned_api, "_require_local", lambda request: None)
    monkeypatch.setattr(learned_api, "BENCH", tmp_path / "none.json")
    app = web.Application()
    learned_api.register(app)
    return app, vault


def test_lists_real_notes_with_their_evidence_and_can_forget_one(tmp_path, monkeypatch):
    app, vault = _app(tmp_path, monkeypatch)

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await (await c.get("/ui/learned")).json()
            assert r["ok"] and r["linked"] and r["score"] is None
            titles = {i["title"]: i for i in r["items"]}
            assert set(titles) == {"Pet", "Lists"}
            assert titles["Pet"]["group"] == "facts" and titles["Pet"]["source"] == "user"
            assert titles["Lists"]["group"] == "lessons" and titles["Lists"]["checked"] == 0
            assert r["week"]["new"] == 2 and r["week"]["from_user"] == 1
            bad = await c.delete("/ui/learned", json={"id": "../../x.md"})
            assert bad.status == 400
            assert (await c.delete("/ui/learned", json={"id": "Facts/Nope.md"})).status == 404
            ok = await (await c.delete("/ui/learned", json={"id": titles["Pet"]["id"]})).json()
            assert ok["ok"]
            r2 = await (await c.get("/ui/learned")).json()
            assert [i["title"] for i in r2["items"]] == ["Lists"]
            assert (vault.base / titles["Pet"]["id"]).exists()          # nothing is deleted
    asyncio.run(go())


def test_the_score_comes_from_the_benchmark_file_only(tmp_path, monkeypatch):
    app, _ = _app(tmp_path, monkeypatch)
    f = tmp_path / "res.json"
    f.write_text(json.dumps({"summary": {"off": {"held_out": [2, 14], "brain_tokens_max": 0, "secs_per_turn": 2.8},
                                         "full": {"held_out": [7, 14], "brain_tokens_max": 92, "secs_per_turn": 2.2}}}))
    monkeypatch.setattr(learned_api, "BENCH", f)
    s = learned_api.score()
    assert [m["mode"] for m in s["modes"]] == ["off", "full"] and s["modes"][1]["passed"] == 7


def test_music_taste_appears_only_when_learning_is_on_and_can_be_forgotten(tmp_path, monkeypatch):
    from core import music_store
    app, vault = _app(tmp_path, monkeypatch)
    monkeypatch.setenv("MBUDDY_MUSIC_DIR", str(tmp_path / "music"))
    lines = [{"t": "2026-09-30T21:00:00", "title": t, "artist": "Kaya", "app": "p", "listened_s": 190, "duration_s": 200,
              "finished": True, "skipped": False} for t in ("a", "b", "c")]
    (tmp_path / "music").mkdir()
    (tmp_path / "music" / "listens.jsonl").write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await (await c.get("/ui/learned")).json()
            assert not [i for i in r["items"] if i["source"] == "your music"]      # off: nothing is used or shown
            music_store.set_learn(True)
            r = await (await c.get("/ui/learned")).json()
            taste = [i for i in r["items"] if i["source"] == "your music"]
            assert taste and taste[0]["title"] == "Kaya" and taste[0]["successes"] == 3 and taste[0]["group"] == "about you"
            gone = await (await c.delete("/ui/learned", json={"id": taste[0]["id"]})).json()
            assert gone["ok"]
            r = await (await c.get("/ui/learned")).json()
            assert not [i for i in r["items"] if i["source"] == "your music"]
    asyncio.run(go())
