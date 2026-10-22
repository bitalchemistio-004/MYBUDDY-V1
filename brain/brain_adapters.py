"""
brain/brain_adapters.py — Unified interface for different LLM providers.

Design:
  - BaseBrainAdapter: abstract streaming interface used by the router.
  - GroqAdapter: Groq Whisper Chat with intra-model model fallback.
  - GenericOpenAIAdapter: any OpenAI-compatible endpoint, configured purely
    from {PROVIDER}_API_KEY/{PROVIDER}_BASE_URL/{PROVIDER}_CHAT_MODEL env
    vars. This is what "gemini" actually resolves to (via Google's
    OpenAI-compatibility layer) — there is no dedicated GeminiAdapter class;
    an earlier version of this docstring claimed one existed when it never
    did, which made Gemini look implemented when GEMINI_BASE_URL/
    GEMINI_CHAT_MODEL were never set, so is_ready() was always False.
  - ADAPTER_POOL: process-lifetime singleton — never re-instantiated per request.
  - get_brain_adapter(name): returns from pool; never creates duplicate adapters.
  - get_available_providers(): returns ordered list of providers that have valid keys.
"""

import asyncio
from typing import AsyncIterator, List, Dict

from core.config import (
    GROQ_API_KEY, BRAIN_PROVIDER,
)
from utils.logger import info, ok, warn, error as logerr

log = info, ok, warn, logerr


# ─────────────────────────────────────────────────────────────
# Base interface
# ─────────────────────────────────────────────────────────────

class BaseBrainAdapter:
    """Abstract streaming interface. All providers must implement stream_chat."""
    provider_name: str = "base"
    _REGISTRY: Dict[str, type["BaseBrainAdapter"]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "provider_name") and cls.provider_name != "base":
            BaseBrainAdapter._REGISTRY[cls.provider_name] = cls

    def is_ready(self) -> bool:
        """Returns True if the adapter is correctly configured and ready to accept requests."""
        return True

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        if False: yield ""  # make this an async generator
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────
# Groq adapter
# ─────────────────────────────────────────────────────────────

class GroqAdapter(BaseBrainAdapter):
    provider_name = "groq"

    def __init__(self):
        from openai import AsyncOpenAI
        
        self._ready = bool(GROQ_API_KEY)
        if self._ready:
            self.client = AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=GROQ_API_KEY,
            )
    @property
    def primary_model(self) -> str:
        import core.config as cfg
        return getattr(cfg, "GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")

    @property
    def fallback_model(self) -> str:
        import core.config as cfg
        return getattr(cfg, "GROQ_UTILITY_MODEL", "llama-3.1-8b-instant")

    def is_ready(self) -> bool:
        return self._ready

    def _record_usage(self, model: str, usage) -> None:
        """
        Add one request's real token usage to the running daily total.

        This is the only place genuine token counts exist in the whole
        pipeline — nowhere else calls the API with `include_usage`, so
        nothing else can report a real number. Failing silently here would
        just mean the dashboard shows nothing for a turn, which is honest;
        raising would take down a reply over a bookkeeping call, which is not
        worth the tradeoff.
        """
        try:
            from core.status import runtime_status

            prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion = int(getattr(usage, "completion_tokens", 0) or 0)
            runtime_status.record_llm_usage(model=model, prompt_tokens=prompt,
                                            completion_tokens=completion)
        except Exception:
            pass

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        if not self._ready:
            raise RuntimeError("Groq adapter is not ready — GROQ_API_KEY is missing.")
            
        current_model = model or self.primary_model
        for attempt in range(3):
            try:
                stream = await self.client.chat.completions.create(
                    model=current_model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    # Groq's API is OpenAI-compatible: with this flag, the
                    # final chunk carries real prompt/completion token counts
                    # (empty choices, populated usage). Without it that data
                    # simply never exists, and any "tokens used" figure shown
                    # anywhere would have to be invented rather than measured.
                    stream_options={"include_usage": True},
                )
                async for chunk in stream:
                    if chunk.choices:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            yield delta
                    usage = getattr(chunk, "usage", None)
                    if usage is not None:
                        self._record_usage(current_model, usage)
                return  # Stream finished cleanly
            except Exception as e:
                err_msg = str(e).lower()
                # 404 Model Not Found / Invalid request error -> fallback immediately
                if ("404" in err_msg or "model_not_found" in err_msg or "does not exist" in err_msg) and current_model == self.primary_model:
                    log[2](f"[Groq] Primary model '{current_model}' not found → switching to fallback '{self.fallback_model}'")
                    current_model = self.fallback_model
                    continue
                # Intra-Groq fallback: try the utility model on rate limits
                if ("rate_limit" in err_msg or "429" in err_msg) and model is None and current_model == self.primary_model:
                    log[2](f"[Groq] Primary rate-limited → switching to {self.fallback_model}")
                    current_model = self.fallback_model
                    continue
                log[3](f"[Groq] Error (attempt {attempt+1}/3): {e}")
                if attempt == 2:
                    raise  # Let the router handle the failure
                await asyncio.sleep(1.5 * (attempt + 1))




# ─────────────────────────────────────────────────────────────
# Generic OpenAI-compatible adapter
# ─────────────────────────────────────────────────────────────

class GenericOpenAIAdapter(BaseBrainAdapter):
    """Dynamically configured from {PROVIDER}_API_KEY, {PROVIDER}_BASE_URL, etc."""
    provider_name = "generic"

    def __init__(self, name: str):
        import os
        from openai import AsyncOpenAI
        
        self.provider_name = name
        env_pref = name.upper()
        self.api_key = os.getenv(f"{env_pref}_API_KEY")
        self.base_url = os.getenv(f"{env_pref}_BASE_URL")
        self.model = os.getenv(f"{env_pref}_CHAT_MODEL")
        
        self._ready = bool(self.api_key and self.base_url and self.model)
        if self._ready:
            self.client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

    def is_ready(self) -> bool:
        return getattr(self, "_ready", False)

    def _follow_env(self) -> None:
        """The address can change while MBUDDY runs (switching the brain between this PC and the cloud, core/cloud_brain.py):
        this adapter is created once, so it looks at the address again before each request and rebuilds its client
        when it changed. Without this, after a switch every reply went to the old address and failed."""
        import os
        from openai import AsyncOpenAI
        url = os.getenv(f"{self.provider_name.upper()}_BASE_URL") or ("http://127.0.0.1:11434/v1" if self.provider_name == "ollama" else None)
        if self._ready and url and url != self.base_url:
            self.base_url = url
            self.client = AsyncOpenAI(base_url=url, api_key=self.api_key)

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        if not self._ready:
            raise RuntimeError(f"[{self.provider_name}] Adapter is missing env variables ({self.provider_name.upper()}_API_KEY/BASE_URL/CHAT_MODEL).")
        self._follow_env()
        selected_model = model or self.model
        if not model and self.provider_name == "ollama":
            from core import ui_settings  # the model chosen in the app's settings
            selected_model = ui_settings.get("llm_model") or self.model

        for attempt in range(3):
            try:
                extra = {"stream_options": {"include_usage": True}} if self.provider_name == "ollama" else {}
                from core import model_gate
                if self.provider_name == "ollama" and model_gate.priority.get() != model_gate.TURN:
                    extra["extra_headers"] = {model_gate.HEADER: model_gate.priority.get()}     # housekeeping waits for the user's turn
                from core import timeline
                timeline.mark("reply request sent")
                stream = await self.client.chat.completions.create(
                    model=selected_model,
                    messages=messages, # type: ignore
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    **extra,
                )
                self.last_usage = None
                got_token = False
                async for chunk in stream:
                    if getattr(chunk, "usage", None):
                        # Ollama counts only the prompt tokens it had to read
                        # anew; the rest came from its cache (M2 S6).
                        self.last_usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if delta:
                        if not got_token:
                            got_token = True
                            timeline.mark("first token")
                        yield delta
                return
            except Exception as e:
                log[3](f"[{self.provider_name}] Error (attempt {attempt+1}/3): {e}")
                if attempt == 2: raise
                if self.provider_name == "ollama":
                    # A server that isn't there won't appear by waiting: fail
                    # now instead of 3x retries in silence (M2 R4).
                    from brain import ollama_service
                    if await asyncio.to_thread(ollama_service.probe) == "refused":
                        raise
                await asyncio.sleep(1.5 * (attempt + 1))


# ─────────────────────────────────────────────────────────────
# Singleton adapter pool + helper functions
# ─────────────────────────────────────────────────────────────

# Module-level singletons — created once, reused everywhere
_POOL: Dict[str, BaseBrainAdapter] = {}


def _build_adapter(name: str) -> BaseBrainAdapter:
    n = name.lower()
    if n in BaseBrainAdapter._REGISTRY and n != "generic":
        return BaseBrainAdapter._REGISTRY[n]()
    return GenericOpenAIAdapter(n)


def get_brain_adapter(provider: str) -> BaseBrainAdapter:
    """
    Return the singleton adapter for `provider`.
    Creates it once on first call; subsequent calls return the cached instance.
    This prevent repeated discoveries.
    """
    key = provider.lower()
    if key not in _POOL:
        _POOL[key] = _build_adapter(key)
    return _POOL[key]


# Every provider an adapter exists for. Used to discover what is actually
# usable, rather than only what BRAIN_PROVIDER happens to name.
KNOWN_PROVIDERS = ("groq", "gemini", "openai", "deepseek", "ollama")

# Runtime override, set from the desktop or the control API. Takes precedence
# over BRAIN_PROVIDER so a provider can be switched without editing config or
# restarting. None means "follow the environment".
_provider_override = None


def set_active_providers(providers):
    """
    Choose which providers to use, in preference order, for this run.

    Args:
        providers: a provider name, or an ordered list of them. Pass None to
            go back to whatever BRAIN_PROVIDER says.

    Returns:
        list: the providers that were accepted, filtered to those whose
        credentials are actually present.

    Raises:
        ValueError: if none of the requested providers are usable, so the
        caller can say why rather than silently falling back.
    """
    global _provider_override

    if providers is None:
        _provider_override = None
        return get_available_providers()

    if isinstance(providers, str):
        providers = [providers]

    wanted = [str(p).strip().lower() for p in providers if str(p).strip()]
    usable = [p for p in wanted if get_brain_adapter(p).is_ready()]

    if not usable:
        raise ValueError(
            f"None of {wanted} are usable. Check that the matching API key is set."
        )

    _provider_override = usable
    return usable


def get_configured_providers() -> List[str]:
    """The provider order currently in force, runtime override first."""
    if _provider_override:
        return list(_provider_override)
    raw = [p.strip().lower() for p in BRAIN_PROVIDER.split(",") if p.strip()]
    return raw or ["groq"]


def discover_providers() -> List[dict]:
    """
    Report every known provider and whether it can actually be used.

    This is what the desktop needs to offer a real choice: it lists providers
    whose credentials are present regardless of whether BRAIN_PROVIDER names
    them, which is why a configured Gemini key used to be invisible.

    Returns:
        list of dicts with `name`, `ready`, and `active`.
    """
    active = get_configured_providers()
    out = []
    for name in KNOWN_PROVIDERS:
        try:
            ready = bool(get_brain_adapter(name).is_ready())
        except Exception:
            ready = False
        out.append({"name": name, "ready": ready, "active": name in active})
    return out


def get_available_providers() -> List[str]:
    """
    Providers ready to accept requests, in preference order.

    Honours a runtime override set through `set_active_providers`, otherwise
    follows BRAIN_PROVIDER. Either way only providers whose credentials are
    present are returned.
    """
    ready = []
    for p in get_configured_providers():
        try:
            if get_brain_adapter(p).is_ready():
                ready.append(p)
        except Exception:
            continue
    return ready
