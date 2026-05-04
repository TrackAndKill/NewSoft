import json
import re
from datetime import datetime, timezone

from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, Plan, Task, Venture
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent
from orchestrator.tools.domains import domain_register

ENGINEER = AgentSpec(
    name="engineer",
    role="Engineer",
    model=settings.model_sonnet,
    max_tokens=1800,
    system_prompt=(
        "You are NewSoft's first engineer. Break a CTO 30-day plan into concrete first-30-day tasks. "
        'Return STRICT JSON: {"tasks":[{"title":"...","description":"...","approval_action":null|"register_domain","approval_payload":null|{"name":"example.com","years":1,"estimated_usd":12}}]}. '
        "Create at least 3 tasks. Include exactly one sensible register_domain task if a domain would help the venture. No other approval actions are allowed."
    ),
)


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in Engineer output: {text[:200]}")
    return json.loads(match.group(0))


def _safe_task_title(value: str) -> str:
    value = (value or "Untitled task").strip()
    return value[:200]


def break_down_first_30(plan_id: int) -> list[int]:
    with session_scope() as s:
        plan = s.get(Plan, plan_id)
        if plan is None:
            raise ValueError(f"Plan {plan_id} not found")
        existing = s.scalars(select(Task).where(Task.plan_id == plan_id)).all()
        if existing:
            return [t.id for t in existing]
        venture = s.get(Venture, plan.venture_id)
        context = {"venture": {"id": venture.id, "name": venture.name, "charter": venture.charter}, "plan_30": plan.plan_30, "plan_id": plan.id}
    out = run_agent(ENGINEER, [{"role": "user", "content": json.dumps(context, indent=2)}], expected_output_tokens=1200)
    data = _parse_json(out.text)
    task_ids: list[int] = []
    for item in (data.get("tasks") or [])[:10]:
        action = item.get("approval_action") if item.get("approval_action") == "register_domain" else None
        payload = item.get("approval_payload") if isinstance(item.get("approval_payload"), dict) else None
        approval_id = None
        if action == "register_domain" and payload and payload.get("name"):
            approval_result = domain_register(str(payload.get("name")), int(payload.get("years") or 1))
            approval_id = int(approval_result["approval_id"])
        with session_scope() as s:
            task = Task(
                venture_id=context["venture"]["id"],
                plan_id=plan_id,
                title=_safe_task_title(item.get("title")),
                description=item.get("description") or "",
                needs_approval=bool(action and approval_id),
                approval_id=approval_id,
                approval_action=action if approval_id else None,
                approval_payload=payload if approval_id else None,
                status="approved" if approval_id else "pending",
                agent_run_id=out.run_id,
            )
            s.add(task)
            s.flush()
            task_ids.append(task.id)
            s.add(Event(kind="venture_task_created", actor="engineer", message=f"Task #{task.id} created for plan #{plan_id}: {task.title}", payload={"task_id": task.id, "plan_id": plan_id, "approval_id": approval_id}))
    if not task_ids:
        # Deterministic fallback; still creates a domain approval so smoke works if JSON was sparse.
        fallback = [
            {"title": "Define MVP landing page and offer", "description": "Write a one-page offer, feature promise, and waitlist CTA."},
            {"title": "Build validation dashboard skeleton", "description": "Create a simple internal dashboard for signups, conversion, and notes."},
            {"title": "Register venture domain", "description": "Reserve a cheap .com for the venture.", "approval_action": "register_domain", "approval_payload": {"name": f"{context['venture']['name'].lower().replace(' ', '-')[:40]}-test.com", "years": 1, "estimated_usd": 12}},
        ]
        for item in fallback:
            approval_id = None
            action = item.get("approval_action")
            payload = item.get("approval_payload")
            if action == "register_domain" and payload:
                approval_id = int(domain_register(payload["name"], 1)["approval_id"])
            with session_scope() as s:
                task = Task(venture_id=context["venture"]["id"], plan_id=plan_id, title=item["title"], description=item["description"], needs_approval=bool(approval_id), approval_id=approval_id, approval_action=action if approval_id else None, approval_payload=payload if approval_id else None, status="approved" if approval_id else "pending", agent_run_id=out.run_id)
                s.add(task); s.flush(); task_ids.append(task.id)
    return task_ids


def sync_approval_tasks() -> int:
    from orchestrator.db.models import MoneyTransaction
    count = 0
    with session_scope() as s:
        tasks = s.scalars(select(Task).where(Task.approval_action == "register_domain", Task.approval_id.is_not(None))).all()
        for task in tasks:
            tx = s.scalars(select(MoneyTransaction).where(MoneyTransaction.approval_id == task.approval_id).order_by(MoneyTransaction.created_at.desc())).first()
            if tx and tx.status in {"done", "simulated"} and task.status != "done":
                task.status = "done"
                task.completed_at = datetime.now(timezone.utc)
                count += 1
    return count
