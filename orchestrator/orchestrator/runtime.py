import json
import time
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from orchestrator.budget import (
    BudgetExceeded,
    SystemHalted,
    check_active,
    estimate_cost_usd,
    record_spend,
    record_venture_llm_spend,
    reserve_budget,
    reserve_venture_llm,
)
from orchestrator.config import settings
from orchestrator.db.models import AgentRun, Event, ToolCall
from orchestrator.db.session import session_scope
from orchestrator.tools.registry import TOOL_REGISTRY
from orchestrator.tools.operator import reset_tool_context, set_tool_context

_client: Anthropic | None = None
MAX_TOOL_TURNS = 4


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


@dataclass
class AgentSpec:
    name: str
    role: str
    model: str
    system_prompt: str
    max_tokens: int = 1024
    tools: list[dict] = field(default_factory=list)


@dataclass
class AgentResult:
    text: str
    tool_uses: list[dict]
    run_id: int
    cost_usd: float


def _content_block_to_dict(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    if isinstance(block, dict):
        return block
    return {"type": getattr(block, "type", "unknown"), "text": str(block)}


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))


def _call_model(spec: AgentSpec, messages: list[dict]) -> Any:
    request = {
        "model": spec.model,
        "max_tokens": spec.max_tokens,
        "system": spec.system_prompt,
        "messages": messages,
    }
    if spec.tools:
        request["tools"] = spec.tools
    return client().messages.create(**request)


def run_agent(spec: AgentSpec, messages: list[dict], *, expected_output_tokens: int = 800, venture_id: int | None = None) -> AgentResult:
    """Run an agent. Logs prompt/response/tool calls; enforces kill-switch and global + optional venture budget."""
    original_messages = _jsonable(messages)
    working_messages = list(messages)
    with session_scope() as s:
        check_active(s)
        prompt_chars = sum(len(str(m)) for m in messages) + len(spec.system_prompt)
        est_in = max(prompt_chars // 4, 200)
        est_cost = estimate_cost_usd(spec.model, est_in, expected_output_tokens)
        reserve_venture_llm(s, venture_id, est_cost)
        reserve_budget(s, est_cost)

    started = time.time()
    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    tool_call_rows: list[dict[str, Any]] = []
    total_in = 0
    total_out = 0

    try:
        context_token = set_tool_context(agent=spec.name, venture_id=venture_id)
        try:
            for _turn in range(MAX_TOOL_TURNS):
                resp = _call_model(spec, working_messages)
                total_in += resp.usage.input_tokens
                total_out += resp.usage.output_tokens
                content_blocks = [_content_block_to_dict(b) for b in resp.content]
                text_parts.extend([b.get("text", "") for b in content_blocks if b.get("type") == "text" and b.get("text")])
                turn_tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
                tool_uses.extend(turn_tool_uses)
                if not turn_tool_uses:
                    break

                working_messages.append({"role": "assistant", "content": content_blocks})
                tool_results = []
                for tool_use in turn_tool_uses:
                    name = tool_use.get("name")
                    args = tool_use.get("input") or {}
                    result: Any
                    status = "ok"
                    try:
                        func = TOOL_REGISTRY[name]
                        result = func(**args)
                    except Exception as e:  # return tool error to model; do not crash agent loop
                        status = "error"
                        result = {"error": type(e).__name__, "message": str(e)}
                    tool_call_rows.append(
                        {
                            "tool": name or "unknown",
                            "arguments": _jsonable(args),
                            "result": _jsonable({"status": status, "data": result}),
                            "dry_run": False,
                            "cost_usd": 0.0,
                        }
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.get("id"),
                            "content": json.dumps(result, default=str)[:12000],
                        }
                    )
                working_messages.append({"role": "user", "content": tool_results})
            else:
                text_parts.append("\n\n[Tool turn cap reached; continuing with available evidence.]")
        finally:
            reset_tool_context(context_token)
    except Exception as e:
        with session_scope() as s:
            run = AgentRun(
                venture_id=venture_id,
                agent=spec.name,
                role=spec.role,
                model=spec.model,
                input_messages=original_messages,
                system_prompt=spec.system_prompt,
                status="error",
                error=str(e),
                duration_ms=int((time.time() - started) * 1000),
            )
            s.add(run)
        raise

    duration_ms = int((time.time() - started) * 1000)
    cost = estimate_cost_usd(spec.model, total_in, total_out)
    output_text = "\n".join(t for t in text_parts if t)

    with session_scope() as s:
        run = AgentRun(
            venture_id=venture_id,
            agent=spec.name,
            role=spec.role,
            model=spec.model,
            input_messages=original_messages,
            system_prompt=spec.system_prompt,
            output_text=output_text,
            tool_calls=tool_uses or None,
            input_tokens=total_in,
            output_tokens=total_out,
            cost_usd=cost,
            duration_ms=duration_ms,
            status="ok",
        )
        s.add(run)
        s.flush()
        run_id = run.id
        for row in tool_call_rows:
            s.add(ToolCall(agent_run_id=run_id, **row))
        record_spend(s, cost)
        record_venture_llm_spend(s, venture_id, cost)
        s.add(
            Event(
                kind="agent_run",
                actor=spec.name,
                message=f"{spec.role} responded ({total_in}+{total_out} tok, ${cost:.4f})",
                payload={"run_id": run_id, "model": spec.model, "venture_id": venture_id},
            )
        )

    return AgentResult(text=output_text, tool_uses=tool_uses, run_id=run_id, cost_usd=cost)


__all__ = ["AgentSpec", "AgentResult", "run_agent", "BudgetExceeded", "SystemHalted"]
