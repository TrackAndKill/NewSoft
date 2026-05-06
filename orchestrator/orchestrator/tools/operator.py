from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from orchestrator.db.models import Clarification, Event
from orchestrator.db.session import session_scope

_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("operator_tool_context", default={})

TOOLS: list[dict[str, Any]] = [
    {
        "name": "ask_operator",
        "description": (
            "Open-ended question for the operator. NOT for approve/reject decisions "
            "(use approvals for those). Use when a short text answer would help. "
            "The answer is async; do NOT wait for it. Future runs can see answers via search_memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            "required": ["question"],
        },
    }
]


def set_tool_context(**kwargs: Any):
    return _CONTEXT.set({k: v for k, v in kwargs.items() if v is not None})


def reset_tool_context(token) -> None:
    _CONTEXT.reset(token)


def ask_operator(question: str, context: str = "", priority: str = "normal") -> dict[str, Any]:
    question = str(question or "").strip()
    if not question:
        return {"error": "question is required", "status": "error"}
    if priority not in {"low", "normal", "high"}:
        priority = "normal"
    ctx = _CONTEXT.get() or {}
    with session_scope() as s:
        row = Clarification(
            asked_by_agent=str(ctx.get("agent") or "agent")[:80],
            agent_run_id=ctx.get("agent_run_id"),
            venture_id=ctx.get("venture_id"),
            memo_id=ctx.get("memo_id"),
            question_md=question,
            context_md=str(context or "")[:4000] or None,
            priority=priority,
            status="open",
        )
        s.add(row)
        s.flush()
        cid = row.id
        s.add(Event(kind="clarification_requested", actor=row.asked_by_agent, message=f"Clarification #{cid} requested", payload={"clarification_id": cid, "venture_id": row.venture_id, "priority": priority}))
    return {"clarification_id": cid, "status": "open"}
