"""What the buddy is told about background work (track B, step 2): the job's
phase, what the work looks like, real progress only, the app's real icon,
and what is playing on the PC."""
import asyncio
import sys

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import ui_api
from core import app_icon, jobs, ui_settings


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "app_settings.json"))
    ui_settings.reset_cache()
    jobs.reset()
    monkeypatch.setitem(ui_api._media, "item", None)
    monkeypatch.setitem(ui_api._media, "pc", None)
    yield
    jobs.reset()
    ui_settings.reset_cache()


def test_a_job_moves_through_its_phases_and_ends_in_success_or_failed():
    events = []
    jobs.on_change(lambda event, job: events.append((event, job.phase)))
    job = jobs.start("app", "Sketcher 2", "a small house", total=6)
    assert jobs.snapshot()[0]["phase"] == "requested"
    jobs.step(job, 1, doing="writing the script", phase="planning", act="write")
    jobs.step(job, 1, doing="still writing", phase="planning")           # the same phase: no second phase event
    jobs.step(job, 1, doing="running it", phase="executing", act="")
    jobs.step(job, 1, doing="checking", phase="success")                  # an ending is never set by a step
    assert jobs.snapshot()[0]["phase"] == "executing"
    jobs.finish(job, True, "made it")
    assert jobs.snapshot()[0]["phase"] == "success"
    assert [e for e in events if e[0] == "phase"] == [("phase", "planning"), ("phase", "executing")]
    other = jobs.start("learn", "origami", "learn origami")
    jobs.finish(other, False, "found nothing")
    assert jobs.snapshot()[0]["phase"] == "failed"


def test_a_stalled_job_ends_as_failed():
    job = jobs.start("app", "Sketcher 2", "a small house")
    jobs.step(job, 1, doing="running it", phase="executing")
    ended = jobs.check_stalled(now=jobs._jobs[job].updated + jobs.STALL_S + 1)
    assert [j.id for j in ended] == [job] and jobs.snapshot()[0]["phase"] == "failed"


def test_progress_is_only_what_the_job_says_and_the_act_is_kept_until_changed():
    job = jobs.start("learn", "origami", "learn origami")
    assert jobs.on_show()["progress"] is None
    jobs.step(job, doing="reading page 1 of 3", phase="executing", act="search", progress=0.25)
    jobs.step(job, doing="still reading")
    shown = jobs.on_show()
    assert (shown["progress"], shown["act"], shown["doing"]) == (0.25, "search", "still reading")
    jobs.step(job, progress=7, act="juggling")                            # clamped; an unknown act is ignored
    assert (jobs.on_show()["progress"], jobs.on_show()["act"]) == (1.0, "search")


def test_the_buddy_is_handed_the_running_job_then_its_ending_for_a_moment_then_nothing():
    assert jobs.on_show() is None
    first = jobs.start("app", "Sketcher 2", "a small house")
    jobs.finish(first, True, "made it")
    second = jobs.start("learn", "origami", "learn origami")
    assert jobs.on_show()["id"] == second, "the running one, not the one that just ended"
    jobs.finish(second, False, "found nothing")
    shown = jobs.on_show()
    assert (shown["id"], shown["running"], shown["ok"], shown["outcome"]) == (second, False, False, "found nothing")
    assert not {"started", "ended", "updated"} & set(shown), "no clock values: the live stream sends it only on a change"
    assert jobs.on_show(now=jobs._jobs[second].ended + jobs.SHOW_ENDED_S + 1) is None


def test_the_live_state_carries_the_job_with_an_icon_only_for_a_connected_app():
    ui_settings.update({"connected_apps": [{"name": "Python", "path": sys.executable}]})
    assert ui_api.live_snapshot()["job"] is None
    job = jobs.start("app", "Python", "a small script")
    assert ui_api.live_snapshot()["job"]["icon"] == "/ui/app-icon?name=Python"
    jobs.finish(job, True, "done")
    other = jobs.start("app", "Not Connected", "x")
    assert "icon" not in ui_api.live_snapshot()["job"]
    jobs.finish(other, False, "no")
    learn = jobs.start("learn", "origami", "learn origami")
    assert "icon" not in ui_api.live_snapshot()["job"]
    jobs.finish(learn, True, "ok")


def test_a_real_program_icon_is_read_as_a_png_and_a_missing_one_is_none(tmp_path):
    png = app_icon.for_path(sys.executable)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert app_icon.for_path(sys.executable) is png, "read once, then kept"
    plain = tmp_path / "notes.txt"
    plain.write_text("no icon in here", encoding="utf-8")
    assert app_icon.for_path(str(plain)) is None
    assert app_icon.for_path(str(tmp_path / "missing.exe")) is None


def test_the_icon_endpoint_serves_connected_apps_only():
    ui_settings.update({"connected_apps": [{"name": "Python", "path": sys.executable}]})

    async def go():
        app = web.Application()
        ui_api.register_ui_routes(app)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/ui/app-icon", params={"name": "Python"})
            assert r.status == 200 and r.content_type == "image/png" and (await r.read())[:4] == b"\x89PNG"
            assert (await c.get("/ui/app-icon", params={"name": "C:/Windows/System32/notepad.exe"})).status == 404
            assert (await c.get("/ui/app-icon")).status == 404
    asyncio.run(go())


def test_what_is_playing_is_read_off_the_loop_and_only_a_playing_session_counts(monkeypatch):
    asyncio.run(_playing(monkeypatch))


async def _playing(monkeypatch):
    from brain import pc_windows
    sessions = [{"app": "AnyPlayer", "title": "Paused One", "artist": "A", "status": "paused"},
                {"app": "OtherPlayer", "title": "Moving", "artist": "B", "status": "playing"}]
    monkeypatch.setattr(pc_windows, "now_playing", lambda max_age=0.0: [dict(s) for s in sessions])
    monkeypatch.setattr(ui_api, "_read_pc", lambda: {"cpu": 7})
    monkeypatch.setitem(ui_api._media, "at", 0.0)
    assert ui_api.live_snapshot()["media"] is None
    await ui_api._refresh_media()
    assert ui_api.live_snapshot()["media"] == {"title": "Moving", "artist": "B", "app": "OtherPlayer", "playing": True,
                                               "position_s": 0, "duration_s": 0}
    assert ui_api.live_snapshot()["pc"] == {"cpu": 7}
    sessions[1]["status"] = "stopped"
    await ui_api._refresh_media()
    assert ui_api.live_snapshot()["media"]["title"] == "Moving", "not read again within a few seconds"
    monkeypatch.setitem(ui_api._media, "at", 0.0)
    await ui_api._refresh_media()
    shown = ui_api.live_snapshot()["media"]
    assert (shown["title"], shown["playing"]) == ("Paused One", False), "nothing playing: what is paused can go on"
    sessions[0]["status"] = "stopped"
    monkeypatch.setitem(ui_api._media, "at", 0.0)
    await ui_api._refresh_media()
    assert ui_api.live_snapshot()["media"] is None

    def broken(max_age=0.0):
        raise RuntimeError("no media reader")
    monkeypatch.setattr(pc_windows, "now_playing", broken)
    monkeypatch.setitem(ui_api._media, "item", {"title": "stale"})
    monkeypatch.setitem(ui_api._media, "at", 0.0)
    await ui_api._refresh_media()
    assert ui_api.live_snapshot()["media"] is None, "unreadable is nothing known, never a stale title"


def test_pc_numbers_are_whole_and_what_the_pc_does_not_report_is_none(monkeypatch):
    from core import pc_stats
    pc_stats.reset()
    monkeypatch.setattr(pc_stats.psutil, "cpu_percent", lambda interval=None: 41.6)
    monkeypatch.setattr(pc_stats.psutil, "sensors_battery", lambda: None)
    monkeypatch.setattr(pc_stats, "_gpu_temp", lambda: None)
    got = pc_stats.read()
    assert got["cpu"] == 42 and isinstance(got["memory"], int)
    assert (got["battery"], got["charging"], got["gpu_temp"], got["strain"]) == (None, None, None, False)


def test_strain_is_a_run_of_high_readings_or_a_hot_graphics_card_not_one_busy_moment(monkeypatch):
    from core import pc_stats
    pc_stats.reset()
    load = {"cpu": 97.0, "gpu": 60}
    monkeypatch.setattr(pc_stats.psutil, "cpu_percent", lambda interval=None: load["cpu"])
    monkeypatch.setattr(pc_stats, "_gpu_temp", lambda: load["gpu"])
    assert [pc_stats.read()["strain"] for _ in range(3)] == [False, False, True]
    load["cpu"] = 20.0
    assert pc_stats.read()["strain"] is False
    load["gpu"] = 88
    assert pc_stats.read()["strain"] is True
    pc_stats.reset()


def test_snoozing_a_reminder_moves_its_time_and_it_is_announced_again(tmp_path):
    from datetime import datetime, timedelta
    from brain.task_tracker import TaskTracker
    tracker = TaskTracker(db_path=tmp_path / "tasks.db")
    now = datetime(2026, 9, 30, 15, 0, 0)
    task = tracker.create_task("Call the dentist", due_date=(now - timedelta(minutes=1)).isoformat())
    assert [f["title"] for f in tracker.fire_due_reminders(now)] == ["Call the dentist"]
    assert tracker.fire_due_reminders(now) == []
    assert tracker.snooze_task(task.task_id, 10, now=now) == "2026-09-30T15:10:00"
    assert tracker.fire_due_reminders(now + timedelta(minutes=9)) == []
    assert [f["title"] for f in tracker.fire_due_reminders(now + timedelta(minutes=10))] == ["Call the dentist"]
    tracker.complete_task(task.task_id)
    assert tracker.snooze_task(task.task_id, 10, now=now) is None, "a finished task is not brought back"
    assert tracker.snooze_task(9999, 10) is None


def test_the_player_buttons_go_to_the_player_that_is_showing(monkeypatch):
    from brain import pc_windows
    sent = []
    monkeypatch.setattr(pc_windows, "media_command", lambda app, command: sent.append((app, command)) or True)
    monkeypatch.setattr(pc_windows, "now_playing", lambda max_age=0.0: [])
    monkeypatch.setattr(ui_api, "_read_pc", lambda: None)

    async def go():
        app = web.Application()
        ui_api.register_ui_routes(app)
        async with TestClient(TestServer(app)) as c:
            assert (await c.post("/ui/media", json={"command": "toggle"})).status == 409, "nothing is showing"
            ui_api._media["item"] = {"title": "T", "artist": "A", "app": "AnyPlayer", "playing": True}
            assert (await c.post("/ui/media", json={"command": "eject"})).status == 400
            r = await c.post("/ui/media", json={"command": "next"})
            assert r.status == 200 and (await r.json())["ok"] is True
    asyncio.run(go())
    assert sent == [("AnyPlayer", "next")]
    assert pc_windows.MEDIA_COMMANDS == ("toggle", "next", "previous")


def test_a_section_that_is_not_installed_adds_nothing_and_breaks_nothing(monkeypatch):
    import sys
    import types
    assert ui_api._section_state("core.no_such_section", "current") is None
    fake = types.ModuleType("core.fake_section")
    fake.current = lambda: {"minutes_left": 12}
    fake.broken = lambda: 1 / 0
    monkeypatch.setitem(sys.modules, "core.fake_section", fake)
    assert ui_api._section_state("core.fake_section", "current") == {"minutes_left": 12}
    assert ui_api._section_state("core.fake_section", "broken") is None
    assert ui_api._section_state("core.fake_section", "missing") is None
    snap = ui_api.live_snapshot()
    assert "checkin" in snap and "focus" in snap

    app = web.Application()
    ui_api.register_ui_routes(app)            # the section modules may or may not exist: either way it registers
    assert any(getattr(r.resource, "canonical", "") == "/ui/live" for r in app.router.routes())


def test_a_nudge_is_held_long_enough_for_every_window_to_see_it(monkeypatch):
    """core.focus.nudge() answers once and then stays quiet for minutes; the
    live state is read by several windows many times a second, so the first
    reader must not swallow it."""
    import sys
    import types
    calls = {"n": 0}

    def nudge():
        calls["n"] += 1
        return {"app": "Some player", "key": "player"} if calls["n"] == 1 else None
    fake = types.ModuleType("core.focus")
    fake.nudge, fake.current, fake.just_finished = nudge, (lambda: None), (lambda: None)
    monkeypatch.setitem(sys.modules, "core.focus", fake)
    monkeypatch.setitem(ui_api._nudge, "item", None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ui_api.time, "monotonic", lambda: clock["t"])
    assert ui_api.live_snapshot()["focus_nudge"] == {"app": "Some player", "key": "player"}
    clock["t"] += 5
    assert ui_api.live_snapshot()["focus_nudge"] == {"app": "Some player", "key": "player"}, "another window, a moment later"
    clock["t"] += ui_api.NUDGE_SHOWN_S
    assert ui_api.live_snapshot()["focus_nudge"] is None
    assert "focus_done" in ui_api.live_snapshot()
