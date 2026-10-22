import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import roles_api
from core import roles


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("MBUDDY_ROLES_PATH", str(tmp_path / "roles.json"))
    monkeypatch.setattr(roles, "_scheme_registered", lambda scheme: False)
    monkeypatch.setattr(roles_api, "_require_local", lambda request: None)


def test_nothing_is_chosen_at_first_and_every_option_states_what_it_can_do():
    assert roles.chosen("music") is None and roles.chosen("browser") is None
    view = {r["id"]: r for r in roles.overview()["roles"]}
    assert view["music"]["chosen"] is None
    by = {o["id"]: o for o in view["music"]["options"]}
    assert by["youtube_music_web"]["tiers"] == {"control": True, "search": True, "play": True}
    assert by["deezer_web"]["tiers"]["play"] is False           # never claims more than it can verify
    assert by["spotify_app"]["available"] is False and by["spotify_app"]["note"] == "not installed"
    assert by["vlc"]["tiers"]["search"] is False                  # control only


def test_choosing_and_the_names_people_say():
    assert roles.match_name("music", "youtube music")["id"] == "youtube_music_web"
    assert roles.match_name("music", "the Spotify app")["id"] == "spotify_app"
    assert roles.match_name("music", "spotify web")["id"] == "spotify_web"
    assert roles.match_name("music", "banana") is None
    roles.choose("music", "soundcloud_web")
    assert roles.chosen("music")["name"] == "SoundCloud (web)"
    with pytest.raises(ValueError):
        roles.choose("music", "chrome")                          # a browser is not a music app
    roles.choose("music", None)
    assert roles.chosen("music") is None


def test_the_users_own_apps_are_validated_and_removable(tmp_path):
    with pytest.raises(ValueError):
        roles.add_custom("music", "Bad", "web", search="http://x/y")          # no {q}
    with pytest.raises(ValueError):
        roles.add_custom("music", "Bad", "app", exe=str(tmp_path / "missing.exe"))
    p = roles.add_custom("music", "My Radio", "web", search="https://radio.example/?q={q}")
    roles.choose("music", p["id"])
    assert roles.remove_custom(p["id"]) and roles.chosen("music") is None
    assert roles.remove_custom(p["id"]) is False


def test_the_chosen_browser_opens_links_and_none_means_the_system_default(monkeypatch, tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert roles.browser_exe() is None
    (tmp_path / "Google" / "Chrome" / "Application").mkdir(parents=True)
    (tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe").write_bytes(b"")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "none"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "none"))
    roles.choose("browser", "chrome")
    assert roles.browser_exe().endswith("chrome.exe")


def test_the_api_lists_chooses_and_refuses_what_is_not_there(monkeypatch):
    app = web.Application()
    roles_api.register(app)

    async def go():
        async with TestClient(TestServer(app)) as c:
            r = await (await c.get("/ui/roles")).json()
            assert r["ok"] and [x["id"] for x in r["roles"]] == ["music", "browser"]
            ok = await (await c.post("/ui/roles", json={"role": "music", "provider": "deezer_web"})).json()
            assert next(x for x in ok["roles"] if x["id"] == "music")["chosen"] == "deezer_web"
            gone = await c.post("/ui/roles", json={"role": "music", "provider": "spotify_app"})
            assert gone.status == 400                                      # not installed here
            assert (await c.post("/ui/roles", json={"role": "music", "provider": "chrome"})).status == 400
            add = await (await c.post("/ui/roles/custom", json={"role": "music", "name": "My Radio", "kind": "web",
                                                                 "search": "https://radio.example/?q={q}"})).json()
            assert add["ok"] and add["added"] == "custom_my_radio"
            assert (await c.post("/ui/roles/custom", json={"role": "music", "name": "x", "kind": "web", "search": "no"})).status == 400
            rm = await c.delete("/ui/roles/custom", json={"id": "custom_my_radio"})
            assert rm.status == 200
            assert (await c.delete("/ui/roles/custom", json={"id": "custom_my_radio"})).status == 404
    asyncio.run(go())


def test_a_pending_choice_is_offered_until_an_app_is_chosen():
    assert roles.pending_choice() is None
    roles.note_needs_choice("music")
    assert roles.pending_choice() == {"role": "music", "label": "Music"}
    roles.choose("music", "deezer_web")
    assert roles.pending_choice() is None
    roles.choose("music", None)
    roles.note_needs_choice("music")
    roles._asked["music"] -= roles.ASK_WINDOW_S + 1                 # asked long ago: not offered any more
    assert roles.pending_choice() is None
