"""Read-only web search/fetch tools for Scout and Validator agents."""
from __future__ import annotations

import os
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

import httpx


class ToolUnavailable(RuntimeError):
    """Raised when an optional read-only tool is not configured."""


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TIMEOUT_SECONDS = 10.0

TOOLS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": "Search the live web for recent market/customer signals. Returns title, url, and snippet results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "count": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": "Fetch a URL as plain text, following redirects. Does not execute JavaScript.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch"},
                "max_chars": {"type": "integer", "minimum": 500, "maximum": 12000, "default": 5000},
            },
            "required": ["url"],
        },
    },
]


def _brave_key() -> str:
    # Phase 3 spec uses BRAVE_API_KEY. Phase 2 server was pre-seeded with
    # BRAVE_SEARCH_API_KEY, so accept both names for compatibility.
    key = os.getenv("BRAVE_API_KEY") or os.getenv("BRAVE_SEARCH_API_KEY") or ""
    if not key:
        raise ToolUnavailable("BRAVE_API_KEY is not configured")
    return key


def search_tools_available() -> bool:
    return bool(os.getenv("BRAVE_API_KEY") or os.getenv("BRAVE_SEARCH_API_KEY"))


def web_search(query: str, count: int = 5) -> list[dict[str, str]]:
    """Returns [{title, url, snippet}] from Brave Search."""
    key = _brave_key()
    query = (query or "").strip()
    if not query:
        return []
    count = max(1, min(int(count or 5), 8))
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.get(
            BRAVE_ENDPOINT,
            params={"q": query, "count": count, "text_decorations": "false"},
            headers={"Accept": "application/json", "X-Subscription-Token": key},
        )
        resp.raise_for_status()
        data = resp.json()
    results = []
    for row in data.get("web", {}).get("results", [])[:count]:
        results.append(
            {
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "snippet": str(row.get("description") or row.get("snippet") or ""),
            }
        )
    return results


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "\n".join(parser.parts)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_url(url: str, max_chars: int = 5000) -> str:
    """Returns plain-text body, truncated. No JavaScript execution."""
    _brave_key()  # keep provider configuration explicit for both tools
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("fetch_url only supports http(s) URLs")
    max_chars = max(500, min(int(max_chars or 5000), 12000))
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": "NewSoftBot/0.3 (+https://firm.profithub.me)"})
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        body = resp.text
    if "html" in content_type.lower() or "<html" in body[:1000].lower():
        body = _html_to_text(body)
    else:
        body = re.sub(r"\s+", " ", body).strip()
    return body[:max_chars]
