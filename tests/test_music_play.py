"""music_play: says "Playing" only when Windows' media controls show a new song
playing, and only for a provider that can find one song. Nothing real is
opened or fetched."""
import pytest

from brain.connectors import music
from core import roles


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MBUDDY_ROLES_PATH", str(tmp_path / "roles.json"))
    monkeypatch.setenv("MBUDDY_ACTIVITY_DIR", str(tmp_path / "activity"))
    roles.choose("music", "youtube_music_web")


def _setup(monkeypatch, sessions_after, video="IcrbM1l_BoI"):
    opened = []
    calls = {"n": 0}

    def now_playing():
        calls["n"] += 1
        return [] if calls["n"] == 1 else sessions_after  # first read is the "before"

    monkeypatch.setattr(music, "find_video_id", lambda q: video)
    monkeypatch.setattr(music, "_startfile", lambda uri: opened.append(uri))
    monkeypatch.setattr(music, "_now_playing", now_playing)
    monkeypatch.setattr(music, "_sleep", lambda s: None)
    return opened


def test_playing_is_claimed_only_when_windows_shows_it_and_the_play_is_noted(monkeypatch, tmp_path):
    opened = _setup(monkeypatch, [{"title": "Wake Me Up", "artist": "Avicii", "app": "chrome", "status": "playing"}])
    out = music.play_music({"query": "Avicii Wake Me Up"})
    assert opened == ["https://music.youtube.com/watch?v=IcrbM1l_BoI"]
    assert out == 'Playing "Wake Me Up" by Avicii in YouTube Music (web).'
    log = (tmp_path / "activity" / "plays.jsonl").read_text(encoding="utf-8")
    assert "Wake Me Up" in log and "youtube_music_web" in log


def test_opened_but_not_playing_says_so(monkeypatch):
    _setup(monkeypatch, [])
    out = music.play_music({"query": "relaxing music"})
    assert out.startswith("NOT PLAYING:") and "Press play" in out


def test_a_song_that_was_already_playing_does_not_count(monkeypatch):
    same = [{"title": "Golden", "artist": "Kaya", "app": "chrome", "status": "playing"}]
    monkeypatch.setattr(music, "find_video_id", lambda q: "abcdefghijk")
    monkeypatch.setattr(music, "_startfile", lambda uri: None)
    monkeypatch.setattr(music, "_now_playing", lambda: same)
    monkeypatch.setattr(music, "_sleep", lambda s: None)
    assert music.play_music({"query": "x"}).startswith("NOT PLAYING:")


def test_nothing_found_is_a_failure(monkeypatch):
    _setup(monkeypatch, [], video=None)
    out = music.play_music({"query": "zzzz"})
    assert str(out).startswith("FAILED:") and "found nothing" in out


def test_a_provider_that_cannot_pick_one_song_opens_a_search_and_says_so(monkeypatch):
    roles.choose("music", "deezer_web")
    opened = _setup(monkeypatch, [])
    monkeypatch.setattr(music, "_windows", lambda: [type("W", (), {"app": "chrome", "title": "deezer - Chrome"})()])
    out = music.play_music({"query": "jazz"})
    assert opened == ["https://www.deezer.com/search/jazz"]
    assert "isn't playing yet" in out and "Playing" not in out
