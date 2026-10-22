"""An action whose result is the whole reply isn't followed by a model
sentence (S116: "…I'll come back to you. That's new to me."), and a finished
app job's announcement is logged in full."""
import asyncio

import brain.agent as agent
import brain.llm_router as router
from brain import brain_store, pc_actions
from core.state_manager import MBUDDYState


def test_a_whole_reply_action_is_said_once_without_a_model_sentence(monkeypatch):
    line = "That's new to me. Give me a little time to learn how to use Sketcher, and I'll come back to you."

    async def fake_agent(user_text, history, memory, question_only=False):
        return [("use_app", {"app": "Sketcher", "task": "a box"}, line)]

    async def model(self, *a, **k):
        raise AssertionError("the model must not add to a whole reply")
        yield ""  # pragma: no cover
    monkeypatch.setattr(agent, "run_agent", fake_agent)
    monkeypatch.setattr(brain_store, "recall", lambda text: ("", []))
    monkeypatch.setattr(router, "providers_include_ollama", lambda: True)
    monkeypatch.setattr(router, "get_brain_adapter", lambda *a, **k: type("A", (), {"stream_chat": model, "is_ready": lambda s: True})())
    history = []

    async def go():
        return "".join([c async for c in router.ask_mbuddy_stream(MBUDDYState(), "make a box in Sketcher", history, {}, "en")])
    out = asyncio.run(go())
    assert out.count("That's new to me") == 1 and history[-1]["role"] == "assistant"


def test_only_abilities_that_say_it_all_skip_the_model():
    assert pc_actions.REGISTRY["use_app"].says_all and pc_actions.REGISTRY["learn_about"].says_all
    assert agent.says_whole_reply([("use_app", {}, "That's new to me.")])
    assert not agent.says_whole_reply([("use_app", {}, "x"), ("open_app", {}, "Opened Notepad.")])


def test_an_announcement_is_logged_in_full(monkeypatch):
    logged = []
    monkeypatch.setattr(brain_store.log, "info", logged.append)
    monkeypatch.setattr(brain_store, "_loop", None)
    speech = "[HAPPY] I'm back. I made it in Sketcher after 2 tries, and checked it against my plan. " * 3
    brain_store._announce(speech, "Sketcher: a box", ok=True)
    assert f"[Announce] {speech}" in logged
