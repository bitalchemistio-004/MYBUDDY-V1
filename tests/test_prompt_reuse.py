"""What the model reads anew each turn stays small (M2 S6b). Measured live
after S6: 393-453 new tokens per turn. Causes found: a history window that
slid by one message every turn, notes recalled on a single shared word, and a
reply prompt not warmed at boot. No model or web here."""
import asyncio

import brain.agent as agent
import brain.llm_router as router
from brain import brain_store as bs


def test_the_history_opens_with_the_same_message_for_two_turns():
    history = []
    firsts = []
    for turn in range(8):
        history += [{"role": "user", "content": f"question {turn}"}, {"role": "assistant", "content": f"answer {turn}"}]
        firsts.append(router._compact_history_for_llm(history)[0]["content"])
    changes = sum(1 for a, b in zip(firsts, firsts[1:]) if a != b)
    assert changes <= len(firsts) // 2          # it used to change every turn
    assert all(len(router._compact_history_for_llm(history[:n])) <= router.MAX_HISTORY_MESSAGES for n in range(1, 17))


def test_recall_needs_a_real_match_not_one_shared_word(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(bs, "vault_root", lambda: root)
    monkeypatch.setattr(bs, "_vault", None)
    monkeypatch.setattr(bs, "_vault_root", None)
    monkeypatch.setattr(bs, "_embed_notes", None)
    v = bs.get_vault()
    v.upsert("Apps", "Slides maker", "Make a deck. It takes some time to learn.", source="web", tags=["mbuddy/app"])
    assert bs.recall("what time is it?") == ("", [])               # one body word: not a match
    block, paths = bs.recall("open the slides maker")
    assert paths and "Slides maker" in block                       # the title: a match


def test_the_agent_log_splits_deciding_from_running(monkeypatch):
    logged = []

    async def decide(text, history, question_only=False):
        return [{"name": "clock", "args": {}}]
    monkeypatch.setattr(agent, "decide_calls", decide)
    monkeypatch.setattr(agent, "execute_calls", lambda calls, memory: [("clock", {}, "It's 9:00.")])
    monkeypatch.setattr(agent.log, "info", logged.append)
    asyncio.run(agent.run_agent("what time is it?", [], {}))
    assert any("deciding" in m and "running" in m for m in logged if m.startswith("[Agent] ran"))



def test_a_reply_cut_before_ollamas_count_still_logs_an_estimate(monkeypatch):
    logged = []

    async def no_tools(user_text, history, memory, question_only=False):
        return []

    async def model(self, messages, *a, **k):
        self.last_usage = None
        yield "[NORMAL] Okay."
    monkeypatch.setattr(agent, "run_agent", no_tools)
    monkeypatch.setattr(bs, "recall", lambda text: ("", []))
    monkeypatch.setattr(router, "providers_include_ollama", lambda: True)
    monkeypatch.setattr(router, "get_brain_adapter", lambda *a, **k: type("A", (), {"stream_chat": model, "is_ready": lambda s: True})())
    monkeypatch.setattr(router, "log", (logged.append, logged.append, logged.append, logged.append))
    from core.state_manager import MBUDDYState
    state, history = MBUDDYState(), []
    for text in ("hello there", "how are you today"):
        async def go():
            return "".join([c async for c in router.ask_mbuddy_stream(state, text, history, {}, "en")])
        asyncio.run(go())
    lines = [m for m in logged if isinstance(m, str) and m.startswith("[Brain] reply prompt")]
    assert len(lines) == 2 and "estimated" in lines[1] and "system prompt unchanged: yes" in lines[1]
