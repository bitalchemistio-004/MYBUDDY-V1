"""Background LLM jobs follow the active provider (Phase 2 local LLM)."""
import asyncio

import brain.llm_bridge as bridge


class FakeAdapter:
    def __init__(self, name):
        self.provider_name = name
        self.models = []

    async def stream_chat(self, messages, max_tokens, temperature, model=None):
        self.models.append(model)
        yield "ok"


def _run(monkeypatch, provider):
    adapter = FakeAdapter(provider)
    monkeypatch.setattr(bridge, "get_available_providers", lambda: [provider])
    monkeypatch.setattr(bridge, "get_brain_adapter", lambda name: adapter)
    resp = asyncio.run(bridge.llm_chat_create(model="openai/gpt-oss-20b", messages=[{"role": "user", "content": "hi"}]))
    return adapter, resp


def test_groq_model_name_is_not_sent_to_ollama(monkeypatch):
    adapter, resp = _run(monkeypatch, "ollama")
    assert adapter.models == [None]  # Ollama uses its own OLLAMA_CHAT_MODEL
    assert resp.choices[0].message.content == "ok"


def test_groq_still_gets_the_requested_utility_model(monkeypatch):
    adapter, _ = _run(monkeypatch, "groq")
    assert adapter.models == ["openai/gpt-oss-20b"]
