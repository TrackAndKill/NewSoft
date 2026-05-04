import json
import re
from datetime import datetime, timezone

from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, Plan, Site, SiteContent, Task, Venture
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent
from orchestrator.tools.domains import domain_register
from orchestrator.tools.sites import ensure_site_for_venture, request_site_approval

ENGINEER = AgentSpec(
    name="engineer",
    role="Engineer",
    model=settings.model_sonnet,
    max_tokens=2200,
    system_prompt=(
        "You are NewSoft's first engineer. Break a CTO 30-day plan into concrete first-30-day tasks. "
        'Return STRICT JSON: {"tasks":[{"title":"...","description":"...","approval_action":null|"register_domain"|"configure_dns"|"draft_landing_page"|"deploy_landing_page","approval_payload":null|{"name":"example.com","years":1,"estimated_usd":12}}]}. '
        "Include this launch sequence for chartered ventures: register_domain, configure_dns, draft_landing_page, deploy_landing_page. "
        "configure_dns and deploy_landing_page are approval-gated. draft_landing_page is not approval-gated. No cold email."
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


def _domain_from_tasks(tasks: list[dict], venture_name: str) -> str:
    for item in tasks:
        payload = item.get("approval_payload") if isinstance(item.get("approval_payload"), dict) else {}
        name = str(payload.get("name") or payload.get("domain") or "").strip().lower()
        if name and "." in name:
            return name
    return re.sub(r"[^a-z0-9]+", "-", venture_name.lower()).strip("-")[:40] + "-test.com"


def _create_task(context: dict, plan_id: int, item: dict, out_run_id: int | None = None) -> int:
    action = item.get("approval_action")
    payload = item.get("approval_payload") if isinstance(item.get("approval_payload"), dict) else None
    approval_id = None
    if action == "register_domain" and payload and payload.get("name"):
        approval_id = int(domain_register(str(payload.get("name")), int(payload.get("years") or 1))["approval_id"])
    elif action in {"configure_dns", "deploy_landing_page"} and payload and payload.get("site_id"):
        approval_id = int(request_site_approval(int(payload["site_id"]), action, requested_by="engineer"))
    elif action == "draft_landing_page":
        action = None
        payload = None
    else:
        action = None
        payload = None
    with session_scope() as s:
        task = Task(
            venture_id=context["venture"]["id"], plan_id=plan_id,
            title=_safe_task_title(item.get("title")), description=item.get("description") or "",
            needs_approval=bool(action and approval_id), approval_id=approval_id,
            approval_action=action if approval_id else None, approval_payload=payload if approval_id else None,
            status="approved" if approval_id else "pending", agent_run_id=out_run_id,
        )
        s.add(task); s.flush()
        s.add(Event(kind="venture_task_created", actor="engineer", message=f"Task #{task.id} created for plan #{plan_id}: {task.title}", payload={"task_id": task.id, "plan_id": plan_id, "approval_id": approval_id, "approval_action": task.approval_action}))
        return task.id


def break_down_first_30(plan_id: int) -> list[int]:
    with session_scope() as s:
        plan = s.get(Plan, plan_id)
        if plan is None:
            raise ValueError(f"Plan {plan_id} not found")
        existing = s.scalars(select(Task).where(Task.plan_id == plan_id)).all()
        if existing:
            return [t.id for t in existing]
        venture = s.get(Venture, plan.venture_id)
        context = {"venture": {"id": venture.id, "name": venture.name, "slug": venture.slug, "charter": venture.charter}, "plan_30": plan.plan_30, "plan_id": plan.id}
    try:
        out = run_agent(ENGINEER, [{"role": "user", "content": json.dumps(context, indent=2)}], expected_output_tokens=1400)
        data = _parse_json(out.text)
        generated = list(data.get("tasks") or [])[:10]
        out_run_id = out.run_id
    except Exception:
        generated = []
        out_run_id = None
    domain = _domain_from_tasks(generated, context["venture"]["name"])
    site_id = ensure_site_for_venture(context["venture"]["id"], domain=domain)
    canonical = [
        {"title": "Register venture domain", "description": "Reserve the venture domain before public launch.", "approval_action": "register_domain", "approval_payload": {"name": domain, "years": 1, "estimated_usd": 12}},
        {"title": "Configure DNS for landing page", "description": "Point root and www A records at the NewSoft VM.", "approval_action": "configure_dns", "approval_payload": {"site_id": site_id, "domain": domain, "vm_ipv4": settings.vm_ipv4 or None}},
        {"title": "Draft landing page", "description": "Use the Copywriter pod to create a no-tracker static landing page.", "approval_action": "draft_landing_page", "approval_payload": {"site_id": site_id}},
        {"title": "Deploy landing page", "description": "Stage static HTML, write nginx vhost, and issue TLS when DNS is ready.", "approval_action": "deploy_landing_page", "approval_payload": {"site_id": site_id, "domain": domain}},
    ]
    # Keep useful generated non-approval work, but force the launch sequence in-order.
    extras = [i for i in generated if i.get("approval_action") not in {"register_domain", "configure_dns", "draft_landing_page", "deploy_landing_page"}][:4]
    task_ids = []
    for item in canonical + extras:
        task_ids.append(_create_task(context, plan_id, item, out_run_id))
    return task_ids


def sync_approval_tasks() -> int:
    from orchestrator.db.models import MoneyTransaction
    count = 0
    with session_scope() as s:
        tasks = s.scalars(select(Task).where(Task.approval_id.is_not(None))).all()
        for task in tasks:
            if task.approval_action == "register_domain":
                tx = s.scalars(select(MoneyTransaction).where(MoneyTransaction.approval_id == task.approval_id).order_by(MoneyTransaction.created_at.desc())).first()
                if tx and tx.status in {"done", "simulated"} and task.status != "done":
                    task.status = "done"; task.completed_at = datetime.now(timezone.utc); count += 1
            elif task.approval_action in {"configure_dns", "deploy_landing_page"}:
                approval = s.get(Approval, task.approval_id)
                if approval and approval.status == "approved" and task.status != "done":
                    task.status = "done"; task.completed_at = datetime.now(timezone.utc); count += 1
    return count
