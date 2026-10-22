"""Live search facade used by the LLM router.

perform_web_search is async — it runs the blocking HTTP call in a thread
executor so the asyncio event loop is never frozen during a search.
"""
from __future__ import annotations

import asyncio

from brain.fallbacks import fallback_reply
from utils.search_provider import get_search_provider
import utils.logger as log


async def perform_web_search(query: str, max_results: int = 5) -> str:
    """Run a live web search without blocking the event loop.

    Uses run_in_executor so that the synchronous requests.get() call inside
    the search provider doesn't freeze TTS streaming or WebSocket keepalives.
    """
    loop = asyncio.get_event_loop()
    try:
        provider = get_search_provider()
        result = await loop.run_in_executor(
            None, lambda: provider.search(query, max_results=max_results)
        )
        return result
    except Exception as exc:
        log.error("Web search failed", exc)
        return fallback_reply("search_unavailable")
