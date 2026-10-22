"""The reply prompt keeps its cacheable part identical between turns (M2 S6).
On this PC Ollama reads new prompt tokens at ~110/s, so anything that changes
early in the prompt (mood, date, live facts) made it re-read the rest,
history included, on every turn. No model runs here: the prompt is captured."""
import asyncio

import brain.agent as agent
import brain.llm_router as router
from brain import brain_store
from core.state_manager import MBUDDYState


def capture_turns(monkeypatch, turns):
    captured = []

    async def no_tools(user_text, history, memory, question_only=False):
        return []

    async def model(self, messages, *a, **k):
        captured.append([dict(m) for m in messages])
        yield "[NORMAL] Okay."
    monkeypatch.setattr(agent, "run_agent", no_tools)
    monkeypatch.setattr(brain_store, "recall", lambda text: ("", []))
    monkeypatch.setattr(router, "providers_include_ollama", lambda: True)
    monkeypatch.setattr(router, "get_brain_adapter",
                        lambda *a, **k: type("A", (), {"stream_chat": model, "is_ready": lambda s: True})())
    state, history, memory = MBUDDYState(), [], {}
    for text, mood in turns:
        state.current = mood

        async def go():
            return "".join([c async for c in router.ask_mbuddy_stream(state, text, history, memory, "en")])
        asyncio.run(go())
    return captured


def test_the_system_prompt_is_identical_across_turns_and_moods(monkeypatch):
    first, second = capture_turns(monkeypatch, [("Tell me something fun about octopuses.", "Normal"),
                                                ("I had a long day at work.", "Worried")])
    assert first[0]["role"] == "system" and first[0]["content"] == second[0]["content"]
    assert "YOUR CURRENT MOOD" not in second[0]["content"]


def test_what_changes_comes_after_the_history_right_before_the_user(monkeypatch):
    _first, second = capture_turns(monkeypatch, [("Tell me something fun about octopuses.", "Normal"),
                                                 ("I had a long day at work.", "Worried")])
    roles = [m["role"] for m in second]
    assert roles[-2:] == ["system", "user"] and "assistant" in roles[1:-2]
    turn_block = second[-2]["content"]
    assert "YOUR CURRENT MOOD: Worried" in turn_block and "TODAY:" in turn_block
    assert "LIVE FACTS" in turn_block   # this turn's facts ("nothing was done") go here too
