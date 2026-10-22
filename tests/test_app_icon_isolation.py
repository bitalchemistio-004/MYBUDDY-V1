"""Reading a program's icon must never be able to stall the server: it runs
one at a time, in a child process, with a timeout (the Apps screen asked for
five icons right after boot and every request hung)."""
import subprocess
import sys
import threading
import time

from core import app_icon


def _fresh(monkeypatch):
    monkeypatch.setattr(app_icon, "_cache", {})


def test_hung_child_is_killed_and_not_remembered(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(app_icon, "CHILD_TIMEOUT", 1.0)
    monkeypatch.setattr(app_icon, "_child_argv",
                        lambda path, size: [sys.executable, "-c", "import time; time.sleep(60)"])
    began = time.time()
    assert app_icon.for_path(sys.executable) is None
    assert time.time() - began < 5, "a hung read must give up at the timeout"
    assert app_icon._cache == {}, "a timeout is not an answer: ask again next time"


def test_reads_run_one_at_a_time(monkeypatch):
    _fresh(monkeypatch)
    running, peak, guard = 0, 0, threading.Lock()

    def fake(path, size):
        nonlocal running, peak
        with guard:
            running += 1
            peak = max(peak, running)
        time.sleep(0.1)
        with guard:
            running -= 1
        return b"png", True

    monkeypatch.setattr(app_icon, "_read_in_child", fake)
    exes = [sys.executable, sys.executable.replace("python", "pythonw"), __file__, app_icon.__file__]
    threads = [threading.Thread(target=app_icon.for_path, args=(p,)) for p in exes]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak == 1


def test_server_process_does_not_load_the_icon_libraries():
    code = ("import sys; from core import app_icon; app_icon.for_path(sys.executable); "
            "bad = [m for m in ('numpy', 'PIL', 'win32gui', 'win32ui', 'win32com') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], timeout=60).returncode == 0
