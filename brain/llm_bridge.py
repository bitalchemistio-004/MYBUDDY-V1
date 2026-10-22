"""
brain/llm_bridge.py — Standalone compatibility shims for legacy modules.
This file deliberately has MINIMAL dependencies to avoid circular imports.
"""
from .brain_adapters import get_available_providers, get_brain_adapter, GroqAdapter

# Legacy shims
_groq = GroqAdapter()
llm_client = getattr(_groq, "client", None)

async def llm_chat_create(**kwargs):
    """Legacy shim for modules expecting a direct chat creation call."""
    providers = get_available_providers()
    if not providers:
        raise RuntimeError("No ready brain provider is configured.")
    adapter = get_brain_adapter(providers[0])
    from core.ui_settings import personalize  # the user's name from the app
    messages = [{**m, "content": personalize(m["content"])} if isinstance(m, dict) and isinstance(m.get("content"), str) else m
                for m in kwargs.get("messages", [])]
    # "background": housekeeping nobody is waiting for. The cloud answers one call at a time, so it waits for the user's turn.
    priority = kwargs.pop("priority", None)
    max_tok = kwargs.get("max_tokens", 200)
    temp = kwargs.get("temperature", 0.7)
    model = kwargs.get("model")
    # Callers name GROQ_UTILITY_MODEL. That name only means something to
    # Groq; any other provider (the local Ollama brain) uses its own model.
    if providers[0] != "groq":
        model = None
    full_text = ""
    from core import model_gate
    token = model_gate.priority.set(model_gate.parse(priority)) if priority else None
    try:
        async for delta in adapter.stream_chat(messages, max_tok, temp, model=model):
            full_text += delta
    finally:
        if token is not None:
            model_gate.priority.reset(token)
    class MockChoice:
        def __init__(self, text):
            self.message = type('obj', (), {'content': text})()
    class MockResponse:
        def __init__(self, text):
            self.choices = [MockChoice(text)]
    return MockResponse(full_text)
