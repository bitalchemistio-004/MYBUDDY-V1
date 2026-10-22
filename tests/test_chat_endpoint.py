"""Typed chat never returns the previous turn's reply as if it were new."""
import asyncio
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import api.device_control as dc


class FakeSession:
    def __init__(self, answers: bool):
        self.answers = answers
        self.last_reply_text = "the previous answer"
        self.reply_count = 3
        self.response_task = None
        self._suppress_speech = False
        self.state = SimpleNamespace(current="Normal", energy=60.0)
        self.active_language = "en"

    def _set_awake(self, awake):
        pass

    def _cancel_inner(self):
        pass

    async def _route_intent(self, text, lang, typed=False):
        if self.answers:
            self.last_reply_text = f"answer to {text}"
            self.reply_count += 1


def chat(session, text):
    async def go():
        app = web.Application()
        app.router.add_post("/control/chat", dc.chat_handler)
        async with TestClient(TestServer(app)) as c:
            r = await c.post("/control/chat", json={"text": text, "speak": False})
            return r.status, await r.json()
    return asyncio.run(go())


def test_a_reply_comes_back(monkeypatch):
    monkeypatch.setattr(dc, "active_session", lambda: FakeSession(answers=True))
    status, body = chat(None, "hi")
    assert status == 200 and body["reply"] == "answer to hi"


def test_an_interrupted_turn_says_so_instead_of_the_old_reply(monkeypatch):
    monkeypatch.setattr(dc, "active_session", lambda: FakeSession(answers=False))
    status, body = chat(None, "how loud is my PC?")
    assert status == 409 and body["error"] == "interrupted"
    assert "previous answer" not in str(body)


def test_a_turn_queued_behind_the_last_one_still_gets_its_reply(monkeypatch):
    class Queued(FakeSession):
        async def _route_intent(self, text, lang, typed=False):
            self.queued_text = text  # the previous turn is still finishing

            async def later():
                await asyncio.sleep(0.3)
                self.queued_text = ""
                self.last_reply_text = f"answer to {text}"
                self.reply_count += 1
            asyncio.get_event_loop().create_task(later())

    monkeypatch.setattr(dc, "active_session", lambda: Queued(answers=False))
    status, body = chat(None, "yes")
    assert status == 200 and body["reply"] == "answer to yes"


def test_typing_does_not_leave_the_voice_awake(monkeypatch):
    slept = []

    class Sleepy(FakeSession):
        is_awake = False

        async def sleep_after_typed_turn(self):
            slept.append(True)

    monkeypatch.setattr(dc, "active_session", lambda: Sleepy(answers=True))
    chat(None, "hi")
    assert slept == [True]

    slept.clear()

    class Awake(Sleepy):
        is_awake = True

    monkeypatch.setattr(dc, "active_session", lambda: Awake(answers=True))
    chat(None, "hi")
    assert slept == []  # the user had woken it by voice: leave it awake


def test_sleep_after_typed_turn_respects_a_voice_wake():
    from api.websocket_server import MBUDDYSession

    class S:
        llm_in_flight = reply_speaking = is_outputting_audio = False
        is_awake = True
        _woken_by_voice = False
        slept = 0

        async def go_to_sleep(self):
            self.slept += 1

    s = S()
    asyncio.run(MBUDDYSession.sleep_after_typed_turn(s, limit_s=1))
    assert s.slept == 1
    s2 = S(); s2._woken_by_voice = True
    asyncio.run(MBUDDYSession.sleep_after_typed_turn(s2, limit_s=1))
    assert s2.slept == 0


def test_a_typed_chat_keeps_the_buddy_looking_awake_for_a_while_after_the_reply(monkeypatch):
    import time
    from core.status import runtime_status
    monkeypatch.setattr(dc, "active_session", lambda: FakeSession(answers=True))
    runtime_status.update(chat_until=0.0)
    chat(None, "hi")
    left = runtime_status.chat_until - time.time()
    assert dc.CHAT_STAYS_AWAKE_S - 5 < left <= dc.CHAT_STAYS_AWAKE_S    # the voice may sleep; the character does not drop off at once
    from api import ui_api
    assert "chat_until" in ui_api.LIVE_FIELDS


def test_the_live_state_says_whether_the_microphone_is_on(tmp_path, monkeypatch):
    from api import ui_api
    from core import ui_settings
    monkeypatch.setenv("MBUDDY_SETTINGS_PATH", str(tmp_path / "s.json"))
    ui_settings.reset_cache()
    assert ui_api.live_snapshot()["mic_on"] is True
    ui_settings.update({"microphone_on": False})
    assert ui_api.live_snapshot()["mic_on"] is False       # the buddy then stays up instead of looking asleep
    ui_settings.reset_cache()


def test_a_turn_that_is_cancelled_midway_does_not_leave_the_buddy_thinking(monkeypatch):
    import asyncio

    from api.websocket_server import MBUDDYSession
    from core.status import runtime_status

    class S:
        response_task = None

        async def _respond_turn(self, text, lang, typed):
            runtime_status.update(thinking=True)
            await asyncio.sleep(30)                                  # a tool that never returns

    async def go():
        s = S()
        task = asyncio.ensure_future(MBUDDYSession._respond(s, "play some music", "en"))
        s.response_task = task
        await asyncio.sleep(0.05)
        assert runtime_status.thinking is True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return runtime_status.thinking

    assert asyncio.run(go()) is False


# ── the field test (2026-10-19): a slow reply was shown as "still thinking, ask again" and then lost ──
def test_a_slow_turn_is_pending_and_its_reply_is_collected_when_it_lands(monkeypatch):
    monkeypatch.setattr(dc, "CHAT_WAIT_S", 0.2)

    class Slow(FakeSession):
        def __init__(self):
            super().__init__(answers=True)
            self.memory = {"conversation_log": []}

        def is_queued(self, text):
            return False

        async def _route_intent(self, text, lang, typed=False):
            async def work():
                await asyncio.sleep(0.6)                       # longer than the endpoint waits
                self.last_reply_text = "Python 3.14.6 is the latest stable release."
                self.memory["conversation_log"].append({"user": text, "mbuddy": self.last_reply_text})
                self.reply_count += 1
            self.response_task = asyncio.ensure_future(work())

    session = Slow()
    monkeypatch.setattr(dc, "active_session", lambda: session)

    async def go():
        app = web.Application()
        app.router.add_post("/control/chat", dc.chat_handler)
        app.router.add_get("/control/chat/result", dc.chat_result_handler)
        async with TestClient(TestServer(app)) as c:
            r = await c.post("/control/chat", json={"text": "latest python?", "speak": False})
            first = (r.status, await r.json())
            q = {"text": "latest python?", "after": str(first[1]["after"]), "started": str(first[1]["started"])}
            mid = await (await c.get("/control/chat/result", params=q)).json()
            await asyncio.sleep(0.7)
            last = await (await c.get("/control/chat/result", params=q)).json()
            return first, mid, last
    first, mid, last = asyncio.run(go())
    assert first[0] == 202 and first[1]["pending"] is True and "error" not in first[1]
    assert mid.get("pending") is True
    assert last["ok"] is True and last["reply"].startswith("Python 3.14.6")


def test_a_silent_turn_is_remembered_as_silent(monkeypatch):
    session = FakeSession(answers=True)
    session._silent_texts = set()
    monkeypatch.setattr(dc, "active_session", lambda: session)
    chat(None, "what's 17*23")
    assert "what's 17*23" in session._silent_texts                # a queued run of it later stays silent too


def test_a_queued_message_gets_its_own_reply_not_the_one_before(monkeypatch):
    class Two(FakeSession):
        def __init__(self):
            super().__init__(answers=True)
            self.memory = {"conversation_log": []}
            self.queue = []

        def is_queued(self, text):
            return text in self.queue

        async def _route_intent(self, text, lang, typed=False):
            self.queue.append(text)                          # behind a turn that is still finishing

            async def finish_both():
                await asyncio.sleep(0.1)                     # the earlier turn ends first...
                self.memory["conversation_log"].append({"user": "latest python?", "mbuddy": "Python 3.15.0."})
                self.reply_count += 1
                await asyncio.sleep(0.2)                     # ...then this one
                self.queue.remove(text)
                self.memory["conversation_log"].append({"user": text, "mbuddy": "That's 391."})
                self.reply_count += 1
            self.response_task = asyncio.ensure_future(finish_both())

    monkeypatch.setattr(dc, "active_session", lambda: Two())
    status, body = chat(None, "whats 17*23")
    assert status == 200 and body["reply"] == "That's 391."
