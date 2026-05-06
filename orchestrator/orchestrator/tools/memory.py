from __future__ import annotations

from typing import Any

from orchestrator.memory import search_memory as _search_memory

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_memory",
        "description": "Search the firm's memory of past memos, postmortems, charters, lessons, board decisions, and operator clarification answers. Use this before making big judgments to avoid repeating mistakes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kinds": {"type": "array", "items": {"type": "string", "enum": ["memo", "postmortem", "charter", "lesson", "board_decision", "clarification"]}},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    }
]

def search_memory(query: str, kinds: list[str] | None = None, limit: int = 5) -> list[dict]:
    return _search_memory(query, kinds=kinds, limit=limit)
