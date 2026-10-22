"""Starting the local brain on demand (M2 S4). Everything is faked: no real
Ollama is probed, started or stopped."""
import asyncio

import pytest
import requests

from brain import ollama_service as osvc


@pytest.fixture
def world(monkeypatch, tmp_path):
    spawned = []
    state = {"up": False, "refuse": True}

    def get(url, timeout):
        if state["up"]:
            return type("R", (), {"raise_for_status": lambda self: None})()
        if state["refuse"]:
            raise requests.ConnectionError("refused")
        raise requests.ConnectTimeout("slow")

    def popen(cmd, **kw):
        spawned.append((cmd, kw.get("creationflags")))
        state["up"] = True   # the server comes up
        return type("Proc", (), {"pid": 4242})()
    monkeypatch.setattr(osvc.requests, "get", get)
    monkeypatch.setattr(osvc, "listening", lambda port: state["up"] or not state["refuse"])
    monkeypatch.setattr(osvc.subprocess, "Popen", popen)
    monkeypatch.setattr(osvc, "find_exe", lambda: r"C:\Ollama\ollama.exe")
    monkeypatch.setattr(osvc, "needs_warm", False)
    monkeypatch.setattr(osvc, "PID_FILE", tmp_path / "ollama_started.json")
    monkeypatch.setattr(osvc, "ollama_processes", lambda: [4242] if spawned else [])
    monkeypatch.setattr(osvc, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(osvc.time, "sleep", lambda s: None)
    return state, spawned


def test_refused_starts_it_once_windowless_and_waits_for_it(world):
    state, spawned = world
    assert osvc.ensure_running() == (True, "")
    assert len(spawned) == 1 and spawned[0][0][-2:] == [r"C:\Ollama\ollama.exe", "serve"]
    assert spawned[0][0] == [r"C:\Ollama\ollama.exe", "serve"]   # MBUDDY's own child: inside its job (U1)
    assert not spawned[0][1] & osvc.subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert spawned[0][1] & osvc.subprocess.CREATE_NO_WINDOW
    assert osvc.ensure_running() == (True, "") and len(spawned) == 1   # up now: nothing more


def test_already_up_is_left_alone(world):
    state, spawned = world
    state["up"] = True
    assert osvc.ensure_running() == (True, "") and spawned == []


def test_something_listening_slowly_is_never_doubled(world):
    state, spawned = world
    state["refuse"] = False  # connect timeout: a server is there, loading
    up, why = osvc.ensure_running()
    assert not up and "not answering" in why and spawned == []


def test_missing_ollama_is_said_plainly_without_crashing(world, monkeypatch):
    state, spawned = world
    monkeypatch.setattr(osvc, "find_exe", lambda: None)
    up, why = osvc.ensure_running()
    assert not up and "couldn't find it installed" in why and spawned == []


def test_a_second_start_isnt_tried_while_an_ollama_process_exists(world, monkeypatch):
    state, spawned = world
    monkeypatch.setattr(osvc.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd) or type("Proc", (), {"pid": 4242})())  # never comes up
    assert osvc.ensure_running(wait_s=0.01)[0] is False
    assert osvc.ensure_running(wait_s=0.01)[0] is False
    assert len(spawned) == 1


def test_a_turn_with_the_brain_down_says_so_at_once(monkeypatch):
    from brain import llm_router
    monkeypatch.setattr(llm_router, "get_available_providers", lambda: ["ollama"])
    monkeypatch.setattr(osvc, "probe", lambda timeout=1.0, port=None: "refused")
    monkeypatch.setattr(osvc, "ensure_running", lambda wait_s=10.0: (False, "Ollama isn't running, and starting it failed"))
    history = []

    async def turn():
        return [c async for c in llm_router.ask_mbuddy_stream(None, "hi there friend", history, {}, "en")]
    chunks = asyncio.run(turn())
    assert chunks[0].startswith("[FOCUSED] Starting my brain")
    assert "can't reach my brain" in chunks[1] and "starting it failed" in chunks[1] and len(chunks) == 2
    assert history == []   # its own error lines don't become conversation


def test_a_genuinely_closed_port_is_refused_not_busy():
    # Real sockets, nothing mocked: on Windows the connect to a closed port
    # times out before it is refused, which S4 first took for "busy" (B1).
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert osvc.probe(port=port) == "refused"


def test_a_port_that_listens_but_doesnt_answer_is_busy():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert osvc.probe(timeout=0.5, port=s.getsockname()[1]) == "busy"
    finally:
        s.close()




def test_a_server_that_answers_after_15_s_is_waited_for_and_warmed_once(world, monkeypatch):
    state, spawned = world
    clock = {"t": 1000.0}
    monkeypatch.setattr(osvc.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(osvc.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    started = {}

    def popen(cmd, **kw):
        spawned.append((cmd, kw.get("creationflags")))
        started["at"] = clock["t"]
        return type("Proc", (), {"pid": 4242})()
    monkeypatch.setattr(osvc.subprocess, "Popen", popen)
    monkeypatch.setattr(osvc, "listening", lambda port: "at" in started and clock["t"] - started["at"] >= 15)
    monkeypatch.setattr(osvc, "alive", lambda timeout=1.0: "at" in started and clock["t"] - started["at"] >= 15)
    warmed = []
    monkeypatch.setattr(osvc, "warm", lambda: warmed.append(1))
    assert osvc.ensure_running() == (True, "") and len(spawned) == 1
    osvc.warm_if_needed()
    osvc.warm_if_needed()
    assert warmed == [1]


def test_a_brain_still_waking_up_is_said_and_finished_in_the_background(monkeypatch):
    from brain import llm_router
    monkeypatch.setattr(llm_router, "get_available_providers", lambda: ["ollama"])
    monkeypatch.setattr(osvc, "probe", lambda timeout=1.0, port=None: "refused")
    monkeypatch.setattr(osvc, "ensure_running", lambda wait_s=30.0: (False, "I started Ollama, but it didn't answer within 20 seconds"))
    finishing = []
    monkeypatch.setattr(osvc, "finish_start_in_background", lambda: finishing.append(1))

    async def turn():
        return [c async for c in llm_router.ask_mbuddy_stream(None, "hi there friend", [], {}, "en")]
    chunks = asyncio.run(turn())
    assert "still waking up" in chunks[-1] and finishing == [1]



def test_any_existing_ollama_process_is_waited_for_not_doubled(world, monkeypatch):
    state, spawned = world
    monkeypatch.setattr(osvc, "ollama_processes", lambda: [777])   # e.g. the tray, still starting
    assert osvc.ensure_running(wait_s=0.01)[0] is False and spawned == []


def test_real_serve_started_from_two_threads_and_a_later_call_is_exactly_one(monkeypatch, tmp_path):
    # The real ollama.exe on a spare port (only the child's environment is
    # changed); the user's own Ollama is not touched.
    import os, threading, time
    import psutil
    import pytest
    exe = osvc.find_exe()
    if not exe:
        pytest.skip("Ollama isn't installed")
    port = 11555
    before = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "ollama.exe"}
    mine = lambda: [p.pid for p in psutil.process_iter(["name"])
                    if (p.info["name"] or "").lower() == "ollama.exe" and p.pid not in before]
    monkeypatch.setattr(osvc, "base_url", lambda: f"http://127.0.0.1:{port}")
    monkeypatch.setattr(osvc, "ollama_processes", mine)
    monkeypatch.setattr(osvc, "_SERVE_ENV", {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}"})
    monkeypatch.setattr(osvc, "PID_FILE", tmp_path / "ollama_started.json")
    results = []
    try:
        threads = [threading.Thread(target=lambda: results.append(osvc.ensure_running(90.0))) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        results.append(osvc.ensure_running(90.0))
        started = mine()
        assert all(r[0] for r in results), results
        assert len(started) == 1, started
        assert psutil.Process(started[0]).ppid() == os.getpid()   # MBUDDY's own child, inside its job (U1)
    finally:
        for pid in mine():
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass


def test_the_warm_up_reads_the_reply_prompt_too(monkeypatch):
    # S6b: the first reply after a restart read its whole system prompt.
    from brain.memory_context import build_minimal_system
    from core.state_manager import MBUDDYState
    from core.ui_settings import personalize
    sent = []
    monkeypatch.setattr(osvc, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(osvc.requests, "post", lambda url, json=None, timeout=None:
                        sent.append(json) or type("R", (), {"json": lambda self: {"load_duration": 2e9}})())
    osvc.warm()
    systems = [m["messages"][0]["content"] for m in sent if m and "messages" in m]
    assert personalize(build_minimal_system(MBUDDYState(), {}, "en")) in systems


def test_left_behind_runners_are_reported_and_only_mbuddys_own_are_stopped(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import psutil
    install = tmp_path / "Ollama"
    (install / "lib").mkdir(parents=True)
    exe = install / "ollama.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(osvc, "find_exe", lambda: str(exe))
    monkeypatch.setattr(osvc, "PID_FILE", tmp_path / "ollama_started.json")
    osvc.PID_FILE.write_text('{"pid": 4100}', encoding="utf-8")   # the server MBUDDY started (now gone)
    procs = [  # (pid, name, parent, image)
        (5001, "llama-server.exe", 4100, install / "lib" / "llama-server.exe"),   # MBUDDY's, orphaned
        (5002, "llama-server.exe", 9999, tmp_path / "Other" / "llama-server.exe"),  # someone else's
        (5003, "llama-server.exe", 1234, install / "lib" / "llama-server.exe"),   # parent alive
        (5004, "notepad.exe", 4100, tmp_path / "notepad.exe"),
    ]
    alive = {1234}
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [SimpleNamespace(pid=p, info={
        "name": n, "ppid": pp, "exe": str(img), "memory_info": SimpleNamespace(rss=2**30)}) for p, n, pp, img in procs])
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: pid in alive)
    killed = []
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(kill=lambda: killed.append(pid)))
    found = osvc.clean_orphans()
    assert {o["pid"] for o in found} == {5001, 5002}
    assert killed == [5001]                          # only the one that is clearly MBUDDY's


def test_unloading_after_10_minutes_asleep_is_the_default_and_can_be_changed(tmp_path, monkeypatch):
    import pytest
    from core import ui_settings
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    ui_settings.reset_cache()
    assert ui_settings.get("unload_model_after_min") == 10   # the user's choice (2026-09-29)
    ui_settings.update({"unload_model_after_min": 0})
    assert ui_settings.get("unload_model_after_min") == 0   # never
    with pytest.raises(ui_settings.SettingsError):
        ui_settings.update({"unload_model_after_min": -1})
    ui_settings.reset_cache()


def test_prompt_warming_is_skipped_while_a_turn_waits(monkeypatch):
    sent = []
    monkeypatch.setattr(osvc, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(osvc.requests, "post", lambda url, json=None, timeout=None: sent.append(json))
    parts = osvc.warm_prompts(lambda: True)
    assert sent == [] and all("skipped" in p for p in parts) and len(parts) == 2
    parts = osvc.warm_prompts(lambda: False)
    assert len(sent) == 2 and not any("skipped" in p for p in parts)


def test_closing_mbuddy_frees_the_model_and_ends_only_the_server_it_started(monkeypatch):
    from brain import ollama_service as svc
    calls = []
    monkeypatch.setattr(svc, "unload", lambda: calls.append("unload"))

    class Proc:
        def __init__(self, pid):
            self.pid = pid

        def children(self, recursive=False):
            return [SimpleRunner()]

        def terminate(self):
            calls.append(f"terminate {self.pid}")

        def wait(self, t):
            calls.append("wait")

    class SimpleRunner:
        def terminate(self):
            calls.append("terminate runner")

    import psutil
    monkeypatch.setattr(psutil, "Process", Proc)
    monkeypatch.setattr(svc, "PID_FILE", type("P", (), {"unlink": lambda self: calls.append("pidfile gone")})())
    monkeypatch.setattr(svc, "started_by_mbuddy", lambda: None)             # the user's own Ollama
    svc.shutdown()
    assert calls == ["unload"]                                              # the model leaves memory, the server stays
    calls.clear()
    monkeypatch.setattr(svc, "started_by_mbuddy", lambda: 4242)             # one MBUDDY started
    svc.shutdown()
    assert calls == ["unload", "terminate runner", "terminate 4242", "wait", "pidfile gone"]
