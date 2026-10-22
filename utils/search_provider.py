"""Search provider abstraction for live-info questions.

Priority order:
  1. DdgsSearchProvider  — duckduckgo-search package (no API key, reliable)
  2. GoogleCustomSearch   — if GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_CX are set
  3. DuckDuckGoHtml       — legacy HTML scraper fallback
  4. Disabled             — if MBUDDY_ENABLE_WEB_SEARCH != 1
"""
from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

import utils.logger as log


# Common modern browser User-Agent
MODERN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


class SearchProvider:
    def search(self, query: str, max_results: int = 3) -> str:
        raise NotImplementedError


class DisabledSearchProvider(SearchProvider):
    def search(self, query: str, max_results: int = 3) -> str:
        return "Live search is disabled on this backend."


# ── Best provider: duckduckgo-search package ─────────────────────────────────
class DdgsSearchProvider(SearchProvider):
    """Uses the official duckduckgo-search PyPI package.
    No API key required. Significantly more reliable than HTML scraping.
    """

    # Measured 2026-09-27: "api" 1.8 s, "lite" 2.5 s, "auto" 3.5 s, "html" 13 s,
    # "duckduckgo" failed every time (2.2 s lost per search). Fastest first.
    BACKENDS = ("api", "lite", "auto")

    def search(self, query: str, max_results: int = 5) -> str:
        from concurrent.futures import ThreadPoolExecutor
        from ddgs import DDGS  # Use the new package name directly

        log.info(f"🔍 DDGS search: '{query}'")
        fetched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        fetch_pages = int(os.getenv("MBUDDY_SEARCH_FETCH_PAGES", "2") or "0")
        for backend in self.BACKENDS:
            try:
                with DDGS(timeout=5) as ddgs:
                    found = list(ddgs.text(query, max_results=max_results, backend=backend))
            except Exception as exc:
                log.warn(f"DDGS backend '{backend}' failed: {type(exc).__name__}")
                continue
            if not found:
                log.warn(f"DDGS backend '{backend}' returned no results - trying next.")
                continue
            # The top pages are read at the same time, not one after another.
            urls = [r["href"] for r in found[:fetch_pages] if r.get("href")]
            with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
                excerpts = dict(zip(urls, pool.map(DuckDuckGoHtmlSearchProvider._page_excerpt, urls)))
            results: list[str] = []
            for i, r in enumerate(found):
                parts = [f"{i + 1}. {r.get('title', 'Untitled')}"]
                if r.get("href"):
                    parts.append(f"   URL: {r['href']}")
                if r.get("body"):
                    parts.append(f"   Snippet: {r['body'][:250]}")
                if excerpts.get(r.get("href")):
                    parts.append(f"   Page excerpt: {excerpts[r['href']][:700]}")
                results.append("\n".join(parts))
            return (
                f"Provider: DuckDuckGo ({backend})\nFetched at: {fetched_at}\nQuery: {query}\n"
                + "\n".join(results)
            )

        # Final fallback to HTML scraper
        log.warn("All DDGS backends failed - using manual HTML fallback.")
        return DuckDuckGoHtmlSearchProvider().search(query, max_results=max_results)


# ── Legacy HTML scraper (fallback) ───────────────────────────────────────────
class DuckDuckGoHtmlSearchProvider(SearchProvider):
    """Scrapes the DDG HTML page. Fragile — use DdgsSearchProvider when possible."""

    @staticmethod
    def _page_excerpt(url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return ""
        try:
            headers = {"User-Agent": MODERN_UA}
            response = requests.get(url, headers=headers, timeout=3)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for node in soup(["script", "style", "noscript"]):
                node.decompose()
            chunks: list[str] = []
            title = soup.find("title")
            if title:
                chunks.append(title.get_text(" ", strip=True))
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                chunks.append(meta["content"])
            for selector in ("h1", "h2", "p"):
                for node in soup.select(selector):
                    text = " ".join(node.get_text(" ", strip=True).split())
                    if text and text not in chunks:
                        chunks.append(text)
                    if len(" ".join(chunks)) >= 900:
                        break
                if len(" ".join(chunks)) >= 900:
                    break
            return " ".join(" ".join(chunks).split())[:1200]
        except Exception as exc:
            log.warn(f"Search page fetch failed for {url}: {type(exc).__name__} {exc}")
            return ""

    @staticmethod
    def _clean_url(href: str) -> str:
        href_str = str(href) if href else ""
        if not href_str:
            return ""
        parsed = urlparse(href_str)
        qs = parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return unquote(qs["uddg"][0])
        return href_str

    def search(self, query: str, max_results: int = 3) -> str:
        log.info(f"🔍 DDG HTML search: '{query}'")
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {"User-Agent": "MBUDDY/4.1"}
        response = requests.get(url, headers=headers, timeout=6)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[str] = []
        fetch_pages = int(os.getenv("MBUDDY_SEARCH_FETCH_PAGES", "2") or "0")
        for node in soup.select(".result"):
            title_node = node.select_one(".result__a")
            snippet_node = node.select_one(".result__snippet")
            title = " ".join(title_node.get_text(" ", strip=True).split()) if title_node else ""
            href = self._clean_url(title_node.get("href", "")) if title_node else ""
            snippet = " ".join(snippet_node.get_text(" ", strip=True).split()) if snippet_node else ""
            if not title and not snippet:
                continue
            parts = [f"{len(results) + 1}. {title or 'Untitled result'}"]
            if href:
                parts.append(f"   URL: {href}")
            if snippet:
                parts.append(f"   Snippet: {snippet}")
            if href and len(results) < fetch_pages:
                excerpt = self._page_excerpt(href)
                if excerpt:
                    parts.append(f"   Page excerpt: {excerpt}")
            results.append("\n".join(parts))
            if len(results) >= max_results:
                break
        fetched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        if not results:
            return f"Provider: DuckDuckGo HTML\nFetched at: {fetched_at}\nNo search results found."
        return f"Provider: DuckDuckGo HTML\nFetched at: {fetched_at}\nQuery: {query}\n" + "\n".join(results)


# ── Google Custom Search ──────────────────────────────────────────────────────
class GoogleCustomSearchProvider(SearchProvider):
    """Official Google Programmable Search JSON API provider."""

    def __init__(self, api_key: str, cx: str):
        self.api_key = api_key
        self.cx = cx

    def search(self, query: str, max_results: int = 3) -> str:
        log.info(f"🔍 Google search: '{query}'")
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": self.api_key,
                "cx": self.cx,
                "q": query,
                "num": max(1, min(max_results, 10)),
            },
            headers={"User-Agent": "MBUDDY/4.1"},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        fetched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        items = payload.get("items") or []
        if not items:
            return f"Provider: Google Custom Search\nFetched at: {fetched_at}\nNo search results found."
        results: list[str] = []
        for item in items[:max_results]:
            parts = [f"{len(results) + 1}. {item.get('title') or 'Untitled result'}"]
            if item.get("link"):
                parts.append(f"   URL: {item['link']}")
            if item.get("snippet"):
                parts.append(f"   Snippet: {' '.join(item['snippet'].split())}")
            pagemap = item.get("pagemap") or {}
            metatags = (pagemap.get("metatags") or [{}])[0]
            description = metatags.get("og:description") or metatags.get("description") or ""
            if description:
                parts.append(f"   Page excerpt: {' '.join(description.split())}")
            results.append("\n".join(parts))
        return f"Provider: Google Custom Search\nFetched at: {fetched_at}\nQuery: {query}\n" + "\n".join(results)


# ── Factory ───────────────────────────────────────────────────────────────────
def get_search_provider() -> SearchProvider:
    enabled = os.getenv("MBUDDY_ENABLE_WEB_SEARCH", "1").lower() in {"1", "true", "yes"}
    if not enabled:
        return DisabledSearchProvider()

    # Google if both credentials are present
    provider = os.getenv("MBUDDY_SEARCH_PROVIDER", "ddgs").strip().lower()
    if provider == "google":
        api_key = os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
        cx = os.getenv("GOOGLE_SEARCH_CX", "").strip()
        if api_key and cx:
            return GoogleCustomSearchProvider(api_key, cx)
        log.warn("MBUDDY_SEARCH_PROVIDER=google but credentials missing — falling back to DDGS.")

    # Try ddgs package first, fall back to HTML scraper if not installed
    try:
        import ddgs  # noqa: F401
        return DdgsSearchProvider()
    except ImportError:
        log.warn("duckduckgo-search not installed — using HTML scraper fallback.")
        return DuckDuckGoHtmlSearchProvider()
