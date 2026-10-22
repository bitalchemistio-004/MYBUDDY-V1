"""What's playing, read on one worker thread (M2 S2 / F1). It used to run
asyncio.run() on whichever thread asked; from the server's loop thread WinRT
failed with RPC_E_WRONG_THREAD (0x8001010e), silently. Reads only: nothing
on the PC is changed."""
import asyncio
import threading
import time

import pytest

from brain import pc_windows as pw


@pytest.fixture
def reader(monkeypatch):
    calls = []

    async def fake():
        calls.append(threading.current_thread().name)
        return [{"app": "Player", "title": "Song", "artist": "", "status": "playing"}]
    monkeypatch.setattr(pw, "_read_media", fake)
    monkeypatch.setattr(pw, "_media_last", (0.0, []))
    return calls


def test_it_reads_on_its_own_thread_from_a_running_loop(reader):
    async def turn():  # like the server: a coroutine calling it directly
        return pw.now_playing()
    assert asyncio.run(turn())[0]["title"] == "Song"
    assert reader == ["mbuddy-media"]


def test_two_threads_at_once_share_the_one_worker(reader):
    out = []
    threads = [threading.Thread(target=lambda: out.append(pw.now_playing())) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(out) == 2 and all(o and o[0]["title"] == "Song" for o in out)
    assert set(reader) == {"mbuddy-media"}


def test_a_hung_read_gives_up_at_the_timeout(monkeypatch):
    async def hang():
        await asyncio.sleep(10)
    monkeypatch.setattr(pw, "_read_media", hang)
    monkeypatch.setattr(pw, "MEDIA_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pw, "_media_last", (0.0, []))
    started = time.perf_counter()
    assert pw.now_playing() == []
    assert time.perf_counter() - started < 1.5


def test_only_the_per_turn_status_uses_the_cache(reader):
    pw.now_playing()
    pw.now_playing(max_age=pw.MEDIA_CACHE_S)   # recent enough: no new read
    assert len(reader) == 1
    pw.now_playing()                            # a check that a song changed: always fresh
    assert len(reader) == 2


def test_the_real_reader_works_from_inside_a_running_loop(monkeypatch):
    # The real WinRT call, from the loop thread where it used to fail.
    monkeypatch.setattr(pw, "_media_last", (0.0, []))
    failures = []
    monkeypatch.setattr(pw.log, "warn", lambda msg: failures.append(msg))

    async def turn():
        return pw.now_playing()
    items = asyncio.run(turn())
    assert isinstance(items, list) and not failures, failures
