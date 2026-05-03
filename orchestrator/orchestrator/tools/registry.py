"""Tool registry used by the Anthropic runtime tool loop."""
from __future__ import annotations

from typing import Any, Callable

from orchestrator.tools.search import TOOLS as SEARCH_TOOL_SCHEMAS
from orchestrator.tools.search import fetch_url, web_search

ToolFunc = Callable[..., Any]

TOOL_REGISTRY: dict[str, ToolFunc] = {
    "web_search": web_search,
    "fetch_url": fetch_url,
}

TOOL_SCHEMAS: list[dict[str, Any]] = SEARCH_TOOL_SCHEMAS
