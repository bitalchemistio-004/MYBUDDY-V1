import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import brain.connectors as conn
from api import routines_api
from brain.tools import ToolFailure
from core import jobs, routines


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("MBUDDY_ROUTINES_PATH", str(tmp_path / "routines.json"))
    monkeypatch.setattr(routines_api, "_require_local", lambda request: None)
    jobs.reset()


STEPS = [{"type": "open_app", "name": "Notes"}, {"type": "play_music", "query": "calm piano"},
         {"type": "focus", "minutes": 25}]


def test_steps_are_validated_with_messages_the_user_can_act_on():
    for bad in ({"type": "delete_files", "path": "x"}, {"type": "open_app", "name": ""}, {"type": "focus", "minutes": 2},
                {"type": "focus", "minutes": "25"}, {"type": "open_url", "url": "javascript:alert(1)"},
                {"type": "open_url", "url": "notes.example"}):
        with pytest.raises(ValueError):
            routines.clean_step(bad)
    assert routines.clean_step({"type": "open_url", "url": "https://example.org/a"})["url"] == "https://example.org/a"
    with pytest.raises(ValueError):
        routines.save("", STEPS)
    with pytest.raises(ValueError):
        routines.save("Too many", STEPS * 5)


def test_saving_editing_finding_and_deleting():
    r = routines.save("Start my work day", STEPS)
    with pytest.raises(ValueError):
        routines.save("start my work day", STEPS)
    r2 = routines.save("Start my work day", STEPS[:2], routine_id=r["id"])
    assert len(routines.get(r["id"])["steps"]) == 2 and r2["id"] == r["id"]
    assert routines.find_by_name("work day")["id"] == r["id"]
    assert routines.find_by_name("run my work day routine")["id"] == r["id"]
    assert routines.find_by_name("bedtime") is None
    assert routines.delete(r["id"]) and not routines.delete(r["id"])


def test_a_run_reports_each_step_as_a_job_and_stops_at_the_first_failure():
    r = routines.save("Morning", STEPS)
    seen = []

    def do(step):
        seen.append(step["type"])
        return (step["type"] != "play_music", "worked" if step["type"] != "play_music" else "FAILED: no app chosen")

    out = routines.run(r["id"], do_step=do, pause_s=0)
    assert seen == ["open_app", "play_music"]                       # stopped: the sprint never started
    assert out["ok"] is False and out["results"][-1]["step"] == 2
    job = jobs.snapshot()[-1] if isinstance(jobs.snapshot(), list) else None
    assert job and job["kind"] == "routine" and job["ok"] is False and "step 2 of 3" in job["outcome"]
    assert routines.get(r["id"])["last_run"]["ok"] is False
    ok = routines.run(r["id"], do_step=lambda s: (True, "fine"), pause_s=0)
    assert ok["ok"] and len(ok["results"]) == 3 and "all 3 steps worked" in ok["outcome"]


def test_a_step_that_raises_is_a_failure_not_a_crash():
    r = routines.save("Odd", STEPS[:1])

    def boom(step):
        raise RuntimeError("no such program")
    out = routines.run(r["id"], do_step=boom, pause_s=0)
    assert out["ok"] is False and "RuntimeError" in out["results"][0]["text"]


def test_the_ability_starts_a_saved_routine_and_says_when_there_is_none(monkeypatch):
    started = []
    monkeypatch.setattr(routines, "run_async", lambda rid: started.append(rid) or True)
    out = conn.run("run_routine", {"name": "work day"})
    assert isinstance(out, ToolFailure) and "no routine called" in out
    r = routines.save("Work day", STEPS)
    assert "Started the routine Work day (3 steps)" in conn.run("run_routine", {"name": "run my work day"})
    assert started == [r["id"]]
    monkeypatch.setattr(routines, "run_async", lambda rid: False)
    assert "already running" in conn.run("run_routine", {"name": "work day"})


def test_the_api_saves_lists_runs_and_deletes(monkeypatch):
    ran = []
    monkeypatch.setattr(routines, "run_async", lambda rid: ran.append(rid) or True)
    app = web.Application()
    routines_api.register(app)

    async def go():
        async with TestClient(TestServer(app)) as c:
            s = await (await c.get("/ui/routines")).json()
            assert s["ok"] and s["routines"] == [] and set(s["step_types"]) == {"open_app", "play_music", "open_url", "focus"}
            bad = await c.post("/ui/routines", json={"name": "x", "steps": [{"type": "nope"}]})
            assert bad.status == 400
            made = await (await c.post("/ui/routines", json={"name": "Wind down", "steps": STEPS[:2]})).json()
            rid = made["saved"]
            assert made["routines"][0]["name"] == "Wind down"
            assert (await (await c.post("/ui/routines/run", json={"id": rid})).json())["ok"] and ran == [rid]
            assert (await c.post("/ui/routines/run", json={"id": "nope"})).status == 404
            assert (await c.delete("/ui/routines", json={"id": rid})).status == 200
            assert (await c.delete("/ui/routines", json={"id": rid})).status == 404
    asyncio.run(go())
