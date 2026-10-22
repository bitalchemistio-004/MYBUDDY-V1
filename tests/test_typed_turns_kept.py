"""A typed request is never lost to speech that arrives after it (S116 P3.1).
Live, room audio heard while awake was queued and answered, and it cancelled
the typed request that was running (the HTTP call ended RemoteDisconnected)."""
import asyncio
import time
from types import SimpleNamespace

from api.websocket_server import MBUDDYSession


def bare_session():
    s = MBUDDYSession.__new__(MBUDDYSession)
    s.turn_queue, s._current_typed, s.response_task = [], False, None
    s.started = []
    s._track_task = lambda coro, name: (coro.close(), s.started.append(name), SimpleNamespace(done=lambda: False, cancel=lambda: s.started.append("cancelled")))[2]
    return s


def test_typed_turns_queue_ahead_of_voice_and_are_never_replaced():
    s = bare_session()
    s._queue_turn("room talk", "en", False)
    s._queue_turn("make me a box", "en", True)
    s._queue_turn("more room talk", "en", False)   # replaces the older voice line, not the typed one
    assert s.turn_queue == [("make me a box", "en", True), ("more room talk", "en", False)]
    s._clear_voice_turns()                          # e.g. going to sleep
    assert s.turn_queue == [("make me a box", "en", True)] and s.is_queued("make me a box")


def test_speech_during_a_typed_reply_waits_instead_of_cancelling_it():
    s = bare_session()
    s._start_or_queue("make me a box", "en", typed=True)   # starts
    s._current_typed = True                                 # its reply is running
    s._start_or_queue("Not him", "en", typed=False)         # room audio
    assert "cancelled" not in s.started and s.turn_queue == [("Not him", "en", False)]


def test_a_typed_turn_waits_behind_a_running_voice_reply():
    s = bare_session()
    s._start_or_queue("tell me a joke", "en", typed=False)
    s._start_or_queue("make me a box", "en", typed=True)
    assert "cancelled" not in s.started and s.turn_queue == [("make me a box", "en", True)]


def test_voice_over_voice_still_replaces_as_before():
    s = bare_session()
    s._start_or_queue("tell me a joke", "en", typed=False)
    s._start_or_queue("actually, the weather", "en", typed=False)
    assert "cancelled" in s.started


def test_talking_over_a_typed_reply_stops_the_speech_not_the_work():
    s = bare_session()
    cancelled = []
    s.response_task = SimpleNamespace(done=lambda: False, cancel=lambda: cancelled.append("reply"))
    s.tts_task = SimpleNamespace(done=lambda: False, cancel=lambda: cancelled.append("speech"))
    s._current_typed = True
    s.is_outputting_audio, s.reply_speaking, s.current_inner_thought = True, True, ""
    s.playback_done_event = asyncio.Event()
    s._inflight_audio_buf = []
    s.ws = SimpleNamespace(closed=True)
    asyncio.run(s._handle_barge_in())
    assert cancelled == ["speech"]


def test_the_model_is_unloaded_after_the_chosen_minutes_asleep_only_if_still_asleep(monkeypatch, tmp_path):
    # U1 / the user's choice: free the GPU after 10 minutes asleep, load it back on wake.
    import api.websocket_server as ws
    from brain import ollama_service
    from core import ui_settings
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    ui_settings.reset_cache()
    waited, unloaded = [], []

    async def no_wait(seconds):
        waited.append(seconds)
    monkeypatch.setattr(ws.asyncio, "sleep", no_wait)
    monkeypatch.setattr(ollama_service, "unload", lambda: unloaded.append(1))
    monkeypatch.setattr(ollama_service, "model_loaded", lambda: True)
    s = bare_session()
    s.is_awake = False
    asyncio.run(s._unload_after_sleep())
    assert waited == [600] and unloaded == [1] and s._model_unloaded
    s.is_awake, s._model_unloaded, unloaded[:] = True, False, []
    asyncio.run(s._unload_after_sleep())          # woke up meanwhile: nothing unloaded
    assert unloaded == [] and not s._model_unloaded
    ui_settings.reset_cache()


def test_a_turn_that_finds_the_model_unloaded_warms_it_first(monkeypatch):
    # U1b: after an unload, a typed turn loaded the model inside the turn
    # (decide 18.7 s) and hit the 30 s timeout. Now it warms before the stages.
    from brain import brain_adapters, ollama_service
    loaded = {"now": False}
    reloads = []
    monkeypatch.setattr(brain_adapters, "get_available_providers", lambda: ["ollama"])
    monkeypatch.setattr(ollama_service, "model_loaded", lambda: loaded["now"])
    s = bare_session()
    s.state = SimpleNamespace(current="Normal")

    def reload():
        reloads.append(1)
        loaded["now"] = True
    s._reload_brain = reload

    async def said(*a, **k):
        return None
    s._send_checked = said

    async def turn():
        warmed = await s._wake_before_turn()
        again = await s._wake_before_turn()   # loaded now: nothing to do
        return warmed, again
    assert asyncio.run(turn()) == (True, False)
    assert reloads == [1]


def test_a_turn_after_waking_waits_for_the_model_only_not_the_prompt_warm_up(monkeypatch):
    # U1c: live, the turn that woke MBUDDY waited 33 s for prompt warm-ups
    # that didn't even help it. Waking loads the model and then reads prompts
    # in the background; a turn waits for the load only, and the prompt
    # reading steps aside for it.
    from brain import brain_adapters, ollama_service
    monkeypatch.setattr(brain_adapters, "get_available_providers", lambda: ["ollama"])
    loaded = {"now": False}
    monkeypatch.setattr(ollama_service, "model_loaded", lambda: loaded["now"])
    loads, asked = [], []

    def load():
        time.sleep(0.2)
        loads.append(1)
        loaded["now"] = True
        return "model load 0.2s"

    def prompts(should_stop=None):
        asked.append(should_stop())
        return ["decision prompt skipped (a turn is waiting)"] if asked[-1] else ["decision prompt 9.0s"]
    monkeypatch.setattr(ollama_service, "load_model", load)
    monkeypatch.setattr(ollama_service, "warm_prompts", prompts)
    s = bare_session()
    s.state = SimpleNamespace(current="Normal")

    async def said(*a, **k):
        return None
    s._send_checked = said

    async def wake_then_turn():
        background = s._brain_warm_task()         # the wake word
        warmed = await s._wake_before_turn()      # the turn that follows at once
        s.llm_in_flight = True                    # its reply is now running
        await background
        return warmed
    assert asyncio.run(wake_then_turn()) is True
    assert loads == [1]                            # one load, shared
    assert asked == [True]                         # prompt reading stepped aside for the turn



def test_the_model_stays_loaded_while_a_background_job_runs(monkeypatch, tmp_path):
    # F2: live, the model was unloaded while an app job was mid-try.
    import api.websocket_server as ws
    from brain import ollama_service
    from core import jobs, ui_settings
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    ui_settings.reset_cache()
    jobs.reset()
    job = jobs.start("app", "Sketcher 2", "a chair")
    waits, unloaded = [], []

    async def wait(seconds):
        waits.append(seconds)
        if len(waits) == 3:          # the job ends while MBUDDY is asleep
            jobs.finish(job, True, "made it")
    monkeypatch.setattr(ws.asyncio, "sleep", wait)
    monkeypatch.setattr(ollama_service, "unload", lambda: unloaded.append(len(waits)))
    monkeypatch.setattr(ollama_service, "model_loaded", lambda: True)
    s = bare_session()
    s.is_awake = False
    asyncio.run(s._unload_after_sleep())
    assert waits[0] == 600 and waits[1:] == [30, 30] and unloaded == [3]   # only once the job had ended
    jobs.reset()
    ui_settings.reset_cache()
