import json
import re

from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import Event, Idea, Memo, Plan, Venture
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent
from orchestrator.tools.memory import TOOLS as MEMORY_TOOLS
from orchestrator.tools.operator import TOOLS as OPERATOR_TOOLS

CTO = AgentSpec(
    name="cto",
    role="CTO",
    model=settings.model_opus,
    max_tokens=1600,
    tools=MEMORY_TOOLS + OPERATOR_TOOLS,
    system_prompt=(
        "You are NewSoft's CTO. For a newly chartered venture, produce a practical 30/60/90-day build plan. "
        "Call search_memory once for related technical/validation lessons; if empty, continue. Prefer fast validation, tiny technical scope, and approval-gated external actions. "
        'Return STRICT JSON: {"summary":"...","plan_30":"...","plan_60":"...","plan_90":"..."}.'
    ),
)


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in CTO output: {text[:200]}")
    return json.loads(match.group(0))


def draft_plan(venture_id: int) -> int:
    with session_scope() as s:
        venture = s.get(Venture, venture_id)
        if venture is None:
            raise ValueError(f"Venture {venture_id} not found")
        existing = s.scalars(select(Plan).where(Plan.venture_id == venture_id)).first()
        if existing:
            return existing.id
        memo = s.get(Memo, venture.memo_id)
        idea = s.get(Idea, venture.idea_id)
        context = {
            "venture": {"id": venture.id, "name": venture.name, "slug": venture.slug, "charter": venture.charter},
            "idea": {"title": idea.title if idea else "", "summary": idea.summary if idea else "", "source": idea.source if idea else ""},
            "memo": memo.content if memo else "",
        }
    out = run_agent(CTO, [{"role": "user", "content": json.dumps(context, indent=2)}], expected_output_tokens=1000, venture_id=venture_id)
    data = _parse_json(out.text)
    content_md = (
        f"# {context['venture']['name']} 30/60/90 Plan\n\n"
        f"## Summary\n{data.get('summary','')}\n\n"
        f"## 30 days\n{data.get('plan_30','')}\n\n"
        f"## 60 days\n{data.get('plan_60','')}\n\n"
        f"## 90 days\n{data.get('plan_90','')}"
    )
    with session_scope() as s:
        plan = Plan(
            venture_id=venture_id,
            content_md=content_md,
            plan_30=data.get("plan_30", ""),
            plan_60=data.get("plan_60", ""),
            plan_90=data.get("plan_90", ""),
            agent_run_id=out.run_id,
        )
        s.add(plan)
        s.flush()
        s.add(Event(kind="venture_plan_created", actor="cto", message=f"CTO plan #{plan.id} created for venture #{venture_id}", payload={"venture_id": venture_id, "plan_id": plan.id, "run_id": out.run_id}))
        return plan.id
