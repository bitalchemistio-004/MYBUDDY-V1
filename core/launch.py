"""Opening things for the user, so they outlive MBUDDY (U1).

The desktop shell runs the backend inside a Windows job that ends everything
in it when MBUDDY closes or crashes, so no model process is ever left behind.
Anything MBUDDY opens *for the user* (an app, a file, a web page, a finished
result in a connected app) must not be in that job, or closing MBUDDY would
close it too. This is the one way to open something for the user: it leaves
the job (CREATE_BREAKAWAY_FROM_JOB), or runs normally when there's no job.
Hidden test runs and the model server stay in the job on purpose.
"""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def job_state() -> tuple[bool, bool]:
    """(this process is in a Windows job, that job lets a child leave it).
    Asked of Windows; (False, True) when it can't be asked."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                    ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("io", ctypes.c_uint64 * 6), ("p", ctypes.c_size_t),
                    ("q", ctypes.c_size_t), ("r", ctypes.c_size_t), ("s", ctypes.c_size_t)]
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Declared types: without them the process handle didn't fit a C int,
        # the call raised, and this said "not in a job" when it was in one.
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        k32.IsProcessInJob.restype = wintypes.BOOL
        k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD)]
        k32.QueryInformationJobObject.restype = wintypes.BOOL
        in_job = wintypes.BOOL()
        if not k32.IsProcessInJob(k32.GetCurrentProcess(), None, ctypes.byref(in_job)) or not in_job.value:
            return False, True
        info = Extended()
        if not k32.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None):
            return True, True
        return True, bool(info.Basic.LimitFlags & 0x1800)  # BREAKAWAY_OK or SILENT_BREAKAWAY_OK
    except Exception:
        return False, True


def interpreter() -> tuple[str, dict]:
    """(python to start, extra environment) for running this project's Python
    WITHOUT the virtual environment's launcher. The venv's python.exe is a
    small launcher that starts the real interpreter as a child inside its own
    Windows job; with it in between, whether the interpreter lands in
    MBUDDY's job depended on timing (seen as a flaky test under load, W1).
    The real interpreter with __PYVENV_LAUNCHER__ set behaves as the venv's."""
    import sys
    base = getattr(sys, "_base_executable", "") or sys.executable
    if base and os.path.normcase(base) != os.path.normcase(sys.executable) and os.path.isfile(base):
        return base, {"__PYVENV_LAUNCHER__": sys.executable}
    return sys.executable, {}


def _spawn(argv: list[str], flags: int, cwd: str | None = None) -> subprocess.Popen:
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True, cwd=cwd)
    try:
        return subprocess.Popen(argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:
        # Not in a job (fine), or a job that doesn't allow leaving it: then what
        # is opened here ends when that job ends, which the log must show.
        in_job, may_leave = job_state()
        if in_job and not may_leave:
            import utils.logger as log
            log.warn("[Launch] this process is in a Windows job that doesn't allow leaving it; "
                     f"{argv[-1]!r} will close when that job ends")
        return subprocess.Popen(argv, creationflags=flags, **kw)


def open_for_user(target: str | list[str], cwd: str | None = None) -> subprocess.Popen:
    """Open an app (argv list), or a file, folder, URL or URI (a string, the way
    double-clicking it would), outside MBUDDY's job."""
    if isinstance(target, (list, tuple)):
        return _spawn([str(a) for a in target], CREATE_NEW_PROCESS_GROUP, cwd)
    # The shell's own "start" opens it like a double-click (file associations,
    # URLs, ms-settings: pages); cmd leaves the job first, so what it opens does too.
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    return _spawn([comspec, "/c", "start", "", str(target)], CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, cwd)
