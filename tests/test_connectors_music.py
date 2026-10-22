"""Music connector: the provider is the user's choice; nothing is opened for real."""
from types import SimpleNamespace

import pytest

import brain.connectors as conn
import brain.connectors.music as music
from brain.tools import ToolFailure
from core import roles


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("MBUDDY_ROLES_PATH", str(tmp_path / "roles.json"))
    state = SimpleNamespace(opened=[], windows=[], appear_after=0, polls=0)

    def fake_windows():
        state.polls += 1
        return state.windows if state.polls > state.appear_after else []

    monkeypatch.setattr(music, "_startfile", lambda target: state.opened.append(target))
    monkeypatch.setattr(music, "_windows", fake_windows)
    monkeypatch.setattr(music, "_sleep", lambda s: None)
    monkeypatch.setattr(roles, "_scheme_registered", lambda scheme: scheme == "spotify")
    return state


def _win(app, title):
    return SimpleNamespace(app=app, title=title)


def test_nothing_is_assumed_until_the_user_chooses(env):
    out = conn.run("music_search", {"query": "fairuz"})
    assert isinstance(out, ToolFailure) and "NEEDS CHOICE" in out and "Spotify (desktop app)" in out
    assert env.opened == []
    assert isinstance(conn.run("music_play", {"query": "fairuz"}), ToolFailure)


def test_uris_are_encoded_for_the_chosen_provider():
    spotify = roles.find("spotify_app")
    web = roles.find("youtube_music_web")
    assert music.search_uri(spotify, "AC/DC back in black") == "spotify:search:AC%2FDC%20back%20in%20black"
    assert music.search_uri(web, "fairuz & co") == "https://music.youtube.com/search?q=fairuz+%26+co"


def test_search_opens_the_chosen_web_provider_and_says_it_is_not_playing(env):
    roles.choose("music", "deezer_web")
    env.windows = [_win("chrome", "fairuz - Deezer - Google Chrome")]
    out = conn.run("music_search", {"query": "fairuz"})
    assert env.opened == ["https://www.deezer.com/search/fairuz"]
    assert out.startswith("Opened a Deezer (web) search") and "isn't playing yet" in out


def test_the_desktop_app_is_used_when_that_is_the_choice(env):
    roles.choose("music", "spotify_app")
    env.windows = [_win("Spotify", "Spotify Premium")]
    out = conn.run("music_search", {"query": "daft punk"})
    assert env.opened == ["spotify:search:daft%20punk"] and "Spotify (desktop app) search" in out


def test_a_chosen_app_that_is_not_installed_fails_without_opening(env, monkeypatch):
    roles.choose("music", "spotify_app")
    monkeypatch.setattr(roles, "_scheme_registered", lambda scheme: False)
    out = conn.run("music_search", {"query": "x"})
    assert isinstance(out, ToolFailure) and "not installed" in out and env.opened == []


def test_window_that_never_appears_is_a_failure(env):
    roles.choose("music", "soundcloud_web")
    env.windows = [_win("notepad", "notes")]
    out = conn.run("music_search", {"query": "x"})
    assert isinstance(out, ToolFailure) and "no SoundCloud (web) window appeared" in out


def test_a_provider_the_user_adds_works_with_no_code_change(env):
    p = roles.add_custom("music", "My Radio", "web", search="https://radio.example/find?q={q}")
    roles.choose("music", p["id"])
    env.windows = [_win("chrome", "my radio - Chrome")]
    out = conn.run("music_search", {"query": "jazz"})
    assert env.opened == ["https://radio.example/find?q=jazz"] and out.startswith("Opened a My Radio search")
    assert roles.tiers(p) == {"control": True, "search": True, "play": False}


def test_a_control_only_program_is_honest_about_it(env, tmp_path):
    exe = tmp_path / "player.exe"
    exe.write_bytes(b"")
    p = roles.add_custom("music", "Old Player", "app", exe=str(exe))
    assert roles.tiers(p) == {"control": True, "search": False, "play": False}
    roles.choose("music", p["id"])
    env.windows = [_win("player", "Old Player")]
    out = conn.run("music_search", {"query": "x"})
    assert env.opened == [[str(exe)]] and out.startswith("Opened a Old Player search")


def test_the_user_can_choose_by_name_and_only_real_options_are_accepted(env):
    assert "spotify" in conn.run("choose_app", {"app": "Spotify"}).lower()
    assert roles.chosen("music")["id"] == "spotify_app"
    out = conn.run("choose_app", {"app": "nothing like this"})
    assert isinstance(out, ToolFailure) and "Options:" in out
    assert roles.chosen("music")["id"] == "spotify_app"


def test_empty_query_opens_nothing(env):
    roles.choose("music", "deezer_web")
    assert isinstance(conn.run("music_search", {"query": "  "}), ToolFailure)
    assert env.opened == []


def test_preview_names_the_chosen_app_and_no_default_is_named(env):
    a = conn.get("music_search")
    assert a.risk == "low"
    assert "which music app" in a.describe({"query": "fairuz"})
    roles.choose("music", "tidal_web")
    assert a.describe({"query": "fairuz"}) == "open Tidal (web) and search for \"fairuz\""


def test_now_playing_reuses_the_media_session(monkeypatch):
    monkeypatch.setattr(music, "_now_playing", lambda: [
        {"app": "Spotify", "title": "Song", "artist": "Band", "status": "playing"}])
    assert conn.run("music_now_playing") == "\"Song\" by Band in Spotify (playing)"
    monkeypatch.setattr(music, "_now_playing", lambda: [])
    assert "nothing playing" in conn.run("music_now_playing")
