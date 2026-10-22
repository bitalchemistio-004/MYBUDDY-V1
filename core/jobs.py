"""What MBUDDY is doing in the background, as facts the model is told (W1).

A job in an app, or learning something, runs for minutes on its own thread.
The reply step wasn't told, so "are you working on it?" was answered from
nothing: the model said "Yes, I'm working on it" (true), a guard dropped it
as an unsupported claim, and the user heard "Nothing changed just now." Then
the model said "I didn't open Blender" while the job was on its second try.

Every background job reports here: started, each step or try, how it ended
and why. status_text() is what the reply and the decision step are told;
snapshot() is for the buddy and the panel.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

RECENT_S = 15 * 60   # how long a finished job is still told
QUIET_S = 90         # no progress this long: the status says so instead of "running"
STALL_S = 420        # no progress this long: the job is ended as stalled (a model call may take 300 s)
# Where a job is in its life. A job says its phase as it goes; the buddy shows
# it (plan, do, check) and the ending is always success or failed.
PHASES = ("requested", "planning", "executing", "observing", "evaluating", "success", "failed")
# What the work looks like from outside, for the buddy's pose: looking
# something up, writing something, or plain work.
ACTS = ("", "search", "write")
_lock = threading.Lock()
_jobs: dict[int, "Job"] = {}
_next = 0
_listeners: list = []


@dataclass
class Job:
    id: int
    kind: str                 # "app" (working in an app) or "learn" (studying something)
    subject: str              # the app's name, or the topic
    task: str                 # what was asked, in the user's words
    started: float = field(default_factory=time.time)
    step: int = 0             # the try or step it is on
    total: int = 0            # how many it may take (0: unknown)
    doing: str = ""           # one line: what it is doing now
    last: str = ""            # the last result, in plain words
    ended: float = 0.0
    ok: bool | None = None    # None while running
    outcome: str = ""         # how it ended, and why if it didn't work
    updated: float = field(default_factory=time.time)   # the last sign of progress
    phase: str = "requested"  # one of PHASES
    act: str = ""             # one of ACTS
    progress: float | None = None   # 0..1 when the job really knows how far it is; None: unknown
    cancelled: bool = False   # ended from outside (stalled): its thread stops, and its late ending is never told

    @property
    def running(self) -> bool:
        return self.ended == 0.0


def on_change(listener) -> None:
    """listener(event, job): event is started, phase, step, done, failed or stalled."""
    _listeners.append(listener)


def _tell(event: str, job: Job) -> None:
    for listener in list(_listeners):
        try:
            listener(event, job)
        except Exception:
            pass


def start(kind: str, subject: str, task: str, total: int = 0) -> int:
    global _next
    with _lock:
        _next += 1
        job = _jobs[_next] = Job(_next, kind, subject, task, total=total)
        for old in [j for j in _jobs.values() if not j.running and time.time() - j.ended > RECENT_S]:
            del _jobs[old.id]
    _watch()
    _tell("started", job)
    return job.id


def step(job_id: int, n: int = 0, doing: str = "", last: str = "", phase: str = "", act: str | None = None,
         progress: float | None = None) -> None:
    """A sign of progress. phase: where the job is now (an ending phase is set
    by finish, never here). act: what the work looks like (None keeps it).
    progress: how far, only when really known."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or not job.running:
            return
        job.step = n or job.step
        job.doing = doing or job.doing
        job.last = last or job.last
        moved = phase in PHASES[:5] and phase != job.phase
        if moved:
            job.phase = phase
        if act is not None and act in ACTS:
            job.act = act
        if progress is not None:
            job.progress = min(1.0, max(0.0, float(progress)))
        job.updated = time.time()
    if moved:
        _tell("phase", job)
    _tell("step", job)


def finish(job_id: int, ok: bool, outcome: str = "") -> None:
    with _lock:
        job = _jobs.get(job_id)
        # One owner (F52): a job ended as stalled was cancelled and told; a late ending is not a second story
        if job is None or not job.running:
            return
        job.ended, job.ok, job.outcome = time.time(), ok, outcome
        job.phase = "success" if ok else "failed"
    _tell("done" if ok else "failed", job)


def check_stalled(now: float | None = None) -> list[Job]:
    """End every running job that showed no progress for STALL_S, as stalled
    with its last step, so the status never says "running" for work that
    stopped moving. Returns the jobs it ended."""
    now = time.time() if now is None else now
    ended = []
    with _lock:
        for job in _jobs.values():
            if job.running and now - job.updated > STALL_S:
                quiet = int((now - job.updated) // 60)
                job.ended, job.ok, job.phase, job.cancelled = now, False, "failed", True
                job.outcome = f"stalled: no progress for {quiet} min" + (f" while {job.doing}" if job.doing else "")
                ended.append(job)
    for job in ended:
        _tell("stalled", job)
    return ended


_watching = False


def _watch() -> None:
    """One background thread that checks for stalled jobs."""
    global _watching
    if _watching:
        return
    _watching = True

    def loop():
        while True:
            time.sleep(15)
            try:
                check_stalled()
            except Exception:
                pass
    threading.Thread(target=loop, name="mbuddy-jobs-watch", daemon=True).start()


# A job runs on its own thread from start to announcement, so the code along
# the way reports to "this thread's job" without passing an id around.
_local = threading.local()


def begin(kind: str, subject: str, task: str, total: int = 0) -> int:
    """Start a job and bind it to this thread."""
    _local.job = start(kind, subject, task, total)
    return _local.job


def step_current(n: int = 0, doing: str = "", last: str = "", phase: str = "", act: str | None = None,
                 progress: float | None = None) -> None:
    job_id = getattr(_local, "job", 0)
    if job_id:
        step(job_id, n, doing, last, phase, act, progress)


def cancelled_current() -> bool:
    """This thread's job was ended from outside: stop, kill what it runs, show and say nothing more."""
    job_id = getattr(_local, "job", 0)
    with _lock:
        job = _jobs.get(job_id) if job_id else None
        return bool(job and job.cancelled)


def finish_current(ok: bool, outcome: str = "") -> None:
    """End this thread's job (once: a later call does nothing)."""
    job_id = getattr(_local, "job", 0)
    if job_id:
        _local.job = 0
        finish(job_id, ok, outcome)


def snapshot() -> list[dict]:
    """Running jobs and the ones that ended in the last RECENT_S, newest first."""
    now = time.time()
    with _lock:
        jobs = [j for j in _jobs.values() if j.running or now - j.ended <= RECENT_S]
    return [{"id": j.id, "kind": j.kind, "subject": j.subject, "task": j.task, "running": j.running,
             "step": j.step, "total": j.total, "doing": j.doing, "last": j.last, "ok": j.ok,
             "outcome": j.outcome, "started": j.started, "ended": j.ended, "updated": j.updated,
             "phase": j.phase, "act": j.act, "progress": j.progress}
            for j in sorted(jobs, key=lambda j: (j.ended or j.started, j.id), reverse=True)]  # same instant: the later job first


def running() -> list[dict]:
    return [j for j in snapshot() if j["running"]]


SHOW_ENDED_S = 20   # how long the buddy is still handed a job that ended


def on_show(now: float | None = None) -> dict | None:
    """The one job the buddy shows beside itself: the running one (the newest,
    if several), else one that ended a moment ago, so its ending is seen.
    None when there is nothing to show. No clock values in it: the live
    stream sends it only when something about the job changed."""
    now = time.time() if now is None else now
    jobs = snapshot()
    job = next((j for j in jobs if j["running"]), None)
    if job is None:
        job = next((j for j in jobs if now - j["ended"] <= SHOW_ENDED_S), None)
    if job is None:
        return None
    return {k: job[k] for k in ("id", "kind", "subject", "task", "running", "phase", "act", "progress",
                                "step", "total", "doing", "last", "ok", "outcome")}


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    return f"{seconds // 60} min"


def _line(j: dict, now: float) -> str:
    if j["running"]:
        what = (f"working in {j['subject']} on \"{j['task']}\"" if j["kind"] == "app"
                else f"learning about {j['subject']}")
        tries = f", try {j['step']}" + (f" of {j['total']}" if j["total"] else "") if j["step"] else ""
        extra = "".join(f"; {label}: {j[key]}" for key, label in (("doing", "now"), ("last", "last result")) if j[key])
        quiet = now - j.get("updated", now)
        if quiet > QUIET_S:
            return (f"- STARTED {_ago(now - j['started'])} ago, but NO PROGRESS for {_ago(quiet)}: {what}{tries}{extra}. "
                    "It may be stuck; say so.")
        return f"- RUNNING NOW ({_ago(now - j['started'])} so far): you are {what}{tries}{extra}."
    # An ended job never reads as ongoing: live, "ENDED: working in <app>..."
    # was answered with "I'm still working in <app>".
    what = (f"the job in {j['subject']} (\"{j['task']}\")" if j["kind"] == "app"
            else f"learning about {j['subject']}")
    ending = "it is DONE and it worked" if j["ok"] else "it STOPPED without success"
    return (f"- NOT RUNNING ANY MORE, ended {_ago(now - j['ended'])} ago: {what}: {ending}"
            + (f" ({j['outcome']})" if j["outcome"] else "") + ".")


def status_text() -> str:
    """The block the model is told: true, from MBUDDY's own state."""
    jobs, now = snapshot(), time.time()
    if not jobs:
        return "YOUR BACKGROUND WORK: nothing is running, and nothing ended recently."
    lines = [_line(j, now) for j in jobs[:4]]
    if not any(j["running"] for j in jobs):
        lines.append("- Nothing is running right now.")
    return ("YOUR BACKGROUND WORK (true, from your own state). When he asks what you are doing, whether you are "
            "working, busy, asleep or done, or how it is going, this is the answer; don't invent anything else "
            "you were doing:\n" + "\n".join(lines))


def plain_status() -> str:
    """One spoken sentence of the same facts, for when nothing else can be said."""
    jobs, now = snapshot(), time.time()
    live = [j for j in jobs if j["running"]]
    if live:
        j = live[0]
        what = f"working in {j['subject']}" if j["kind"] == "app" else f"learning about {j['subject']}"
        tries = f", on try {j['step']}" if j["step"] else ""
        quiet = now - j.get("updated", now)
        if quiet > QUIET_S:
            return f"I started {what}, but there's been no progress for {_ago(quiet)}, so it may be stuck."
        return f"I'm {what} right now{tries}."
    if jobs:
        j = jobs[0]
        what = f"in {j['subject']}" if j["kind"] == "app" else f"learning about {j['subject']}"
        return (f"I finished the work {what} a moment ago." if j["ok"]
                else f"I stopped the work {what}: {j['outcome'] or 'it did not work'}.")
    return ""


def reset() -> None:
    """Tests only."""
    global _next
    with _lock:
        _jobs.clear()
        _next = 0
    _listeners.clear()
    _local.job = 0
