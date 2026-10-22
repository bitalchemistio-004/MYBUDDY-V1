"""Long-term memory is built from what the user said, never from MBUDDY's reply.

Live case, 2026-09-26: "My head hurts." -> the model invented "You slept in
after the 3 a.m. call.", and the emotional-memory tagger stored "Sam
mentioning head pain after a 3 a.m. call". The invented detail came only
from the reply.
"""
import asyncio

import memory.long_term as lt
import memory.timeline as tl

REPLY = "You slept in after the 3 a.m. call."
USER = "My head hurts, I had a long day at work today."


def _capture(monkeypatch, module):
    prompts = []

    async def fake_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])

        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": '{"significant": false, "core": [], "event": null}'})()})()]
        return R()

    monkeypatch.setattr(module, "llm_chat_create", fake_llm)
    return prompts


def test_fact_extraction_never_sees_the_reply(monkeypatch):
    prompts = _capture(monkeypatch, lt)
    asyncio.run(lt.extract_and_save_facts("I have a brother named Toha and I work as an engineer.", REPLY, {}))
    assert prompts and REPLY not in prompts[0] and "3 a.m." not in prompts[0]


def test_emotional_memory_never_sees_the_reply(monkeypatch):
    prompts = _capture(monkeypatch, lt)
    asyncio.run(lt.tag_emotional_memory(USER, REPLY, {}))
    assert prompts and "3 a.m." not in prompts[0]


def test_timeline_holds_only_real_actions_and_asks_no_model():
    # Live 2026-09-27: "We planned a calendar event 'MATE90RELASE'" was saved
    # although the reminder had failed. Now only succeeded actions are copied.
    from brain import action_log
    action_log.clear()
    memory = {}
    asyncio.run(tl.extract_timeline_event("plan MATE90RELASE on 1 october", "Done! It's planned.", memory))
    assert memory.get("shared_timeline", []) == []  # nothing really happened
    action_log.record("add_reminder", {"text": "MATE90RELASE"}, "Reminder set: MATE90RELASE on Thu 01 Oct 2026 at 09:00.")
    action_log.record("adjust_volume", {"action": "down"}, "Volume lowered to 20%.")
    action_log.record("window_action", {"action": "close"}, "Cancelled: the user said no, so nothing was done to chrome.")
    asyncio.run(tl.extract_timeline_event(USER, REPLY, memory))
    assert [e["event"] for e in memory["shared_timeline"]] == ["Reminder set: MATE90RELASE on Thu 01 Oct 2026 at 09:00."]
    asyncio.run(tl.extract_timeline_event(USER, REPLY, memory))
    assert len(memory["shared_timeline"]) == 1  # not recorded twice
    assert not hasattr(tl, "llm_chat_create")  # no model call any more
    action_log.clear()


def test_vector_memory_stores_only_the_user(monkeypatch):
    from api.websocket_server import MBUDDYSession

    added = []

    class FakeStore:
        def add(self, text, meta):
            added.append(text)

    import memory.vector_store as vsmod
    monkeypatch.setattr(vsmod, "get_vector_store", lambda: FakeStore())
    asyncio.run(MBUDDYSession._add_vector_memory(object(), USER, REPLY, {"time": "t", "lang": "en"}))
    assert added == [f"User: {USER}"]
