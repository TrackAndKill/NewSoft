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
    reserve_budget,
)
from orchestrator.config import settings
from orchestrator.db.models import AgentRun, Event
from orchestrator.db.session import session_scope

_client: Anthropic | None = None


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


def run_agent(spec: AgentSpec, messages: list[dict], *, expected_output_tokens: int = 800) -> AgentResult:
    """Run one turn of an agent. Logs everything; enforces kill-switch and budget."""
    with session_scope() as s:
        check_active(s)
        # Rough pre-flight estimate so we fail fast if cap is already busted.
        prompt_chars = sum(len(str(m)) for m in messages) + len(spec.system_prompt)
        est_in = max(prompt_chars // 4, 200)
        est_cost = estimate_cost_usd(spec.model, est_in, expected_output_tokens)
        reserve_budget(s, est_cost)

    started = time.time()
    try:
        # The Anthropic SDK rejects tools=None / tools=[]; only pass the kwarg
        # when there's at least one tool defined.
        kwargs: dict = {
            "model": spec.model,
            "max_tokens": spec.max_tokens,
            "system": spec.system_prompt,
            "messages": messages,
        }
        if spec.tools:
            kwargs["tools"] = spec.tools
        resp = client().messages.create(**kwargs)
    except Exception as e:
        with session_scope() as s:
            run = AgentRun(
                agent=spec.name,
                role=spec.role,
                model=spec.model,
                input_messages=messages,
                system_prompt=spec.system_prompt,
                status="error",
                error=str(e),
                duration_ms=int((time.time() - started) * 1000),
            )
            s.add(run)
        raise

    duration_ms = int((time.time() - started) * 1000)
    text_parts = [b.text for b in resp.content if b.type == "text"]
    tool_uses = [
        {"id": b.id, "name": b.name, "input": b.input}
        for b in resp.content
        if b.type == "tool_use"
    ]
    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost = estimate_cost_usd(spec.model, in_tok, out_tok)

    with session_scope() as s:
        run = AgentRun(
            agent=spec.name,
            role=spec.role,
            model=spec.model,
            input_messages=messages,
            system_prompt=spec.system_prompt,
            output_text="\n".join(text_parts),
            tool_calls=tool_uses or None,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            duration_ms=duration_ms,
            status="ok",
        )
        s.add(run)
        s.flush()
        run_id = run.id
        record_spend(s, cost)
        s.add(
            Event(
                kind="agent_run",
                actor=spec.name,
                message=f"{spec.role} responded ({in_tok}+{out_tok} tok, ${cost:.4f})",
                payload={"run_id": run_id, "model": spec.model},
            )
        )

    return AgentResult(text="\n".join(text_parts), tool_uses=tool_uses, run_id=run_id, cost_usd=cost)


__all__ = ["AgentSpec", "AgentResult", "run_agent", "BudgetExceeded", "SystemHalted"]
