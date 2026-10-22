"""MBUDDY owns what it starts (U1). Real processes, all harmless sleepers.

The desktop shell puts the backend in a Windows job with "kill on job
close": when the shell exits or crashes, Windows ends the backend and what it
started (the model server and its runner), so nothing is left eating memory.
Apps opened *for the user* leave the job (core.launch), so they stay open."""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import psutil
import pytest

from core import launch

KILL_ON_JOB_CLOSE, BREAKAWAY_OK, EXTENDED_LIMIT_INFORMATION = 0x2000, 0x0800, 9


class _Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _Extended(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _job():
    """The same job the shell makes (shell/src-tauri/src/backend.rs)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE | BREAKAWAY_OK
    assert k32.SetInformationJobObject(wintypes.HANDLE(job), EXTENDED_LIMIT_INFORMATION,
                                       ctypes.byref(info), ctypes.sizeof(info))
    return k32, job


def _wait_pids(path, n, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            pids = [int(x) for x in path.read_text().split() if x.strip()]
            if len(pids) >= n:
                return pids
        time.sleep(0.1)
    raise AssertionError(f"the stand-in didn't start its children: {path.read_text() if path.exists() else ''}")


def test_closing_the_job_ends_the_backend_and_its_server_but_not_the_users_app(tmp_path):
    in_job, may_leave = launch.job_state()
    if in_job and not may_leave:
        # Nested jobs: a child can leave only if EVERY job around it allows it.
        # Run from inside such a job (some terminals and tool runners make one),
        # the app can't outlive the test's job, so this can't be shown here.
        pytest.skip("pytest itself runs inside a Windows job that doesn't allow leaving it")
    pids = tmp_path / "pids.txt"
    sleeper = "import time; time.sleep(60)"
    backend = (
        "import sys, time, subprocess; sys.path.insert(0, '.')\n"
        "time.sleep(1.5)  # assigned to the job meanwhile, like the shell does right after starting it\n"
        "import os; from core import launch; exe, extra = launch.interpreter(); env = {**os.environ, **extra}\n"
        f"server = subprocess.Popen([exe, '-c', {sleeper!r}], env=env)  # like `ollama serve`\n"
        f"app = launch.open_for_user([exe, '-c', {sleeper!r}])  # like an app opened for the user\n"
        f"open(r'{pids}', 'w').write(f'{{server.pid}} {{app.pid}}')\n"
        "time.sleep(60)\n")
    k32, job = _job()
    # The real interpreter, not the venv launcher: the launcher starts it as a
    # child in a job of its own, and then which job it lands in depends on timing.
    exe, extra = launch.interpreter()
    stand_in = subprocess.Popen([exe, "-c", backend], creationflags=0x08000000, env={**os.environ, **extra})
    server_pid = app_pid = None
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, stand_in.pid)
        assert k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle))
        ctypes.windll.kernel32.CloseHandle(handle)
        server_pid, app_pid = _wait_pids(pids, 2)
        k32.CloseHandle(wintypes.HANDLE(job))   # what the shell exiting or crashing does
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and (stand_in.poll() is None or psutil.pid_exists(server_pid)):
            time.sleep(0.05)
        assert stand_in.poll() is not None, "the backend should end with the job"
        assert not psutil.pid_exists(server_pid), "the model server should end with the job"
        assert psutil.pid_exists(app_pid), (
            f"the user's app must stay open (pytest in a job: {in_job}, may leave it: {may_leave})")
    finally:
        for pid in (stand_in.pid, server_pid, app_pid):
            if pid and psutil.pid_exists(pid):
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass


def test_open_for_user_leaves_the_job_and_falls_back_without_one(monkeypatch):
    calls = []

    def popen(argv, creationflags=0, **kw):
        calls.append((argv, creationflags))
        if creationflags & launch.CREATE_BREAKAWAY_FROM_JOB and len(calls) == 1:
            raise OSError("access denied: the job doesn't allow leaving it")
        return object()
    monkeypatch.setattr(launch.subprocess, "Popen", popen)
    launch.open_for_user("https://example.com")
    assert calls[0][1] & launch.CREATE_BREAKAWAY_FROM_JOB and not calls[1][1] & launch.CREATE_BREAKAWAY_FROM_JOB
    assert calls[0][0][1:4] == ["/c", "start", ""] and calls[0][0][-1] == "https://example.com"


def test_the_job_state_is_read_without_error():
    in_job, may_leave = launch.job_state()
    assert isinstance(in_job, bool) and isinstance(may_leave, bool)
    assert may_leave or in_job          # not in a job always means free to open things
