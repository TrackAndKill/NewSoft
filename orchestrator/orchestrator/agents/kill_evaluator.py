"""Conservative kill evaluator for ventures.

The evaluator never kills anything directly. It only returns evidence; the
scheduler turns a positive result into an operator Approval.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from orchestrator.config import settings
from orchestrator.db.models import Event, Experiment, Lead, Plan, Site, Task, Venture
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent

EVALUATOR = AgentSpec(
    name="kill_evaluator",
    role="Kill Evaluator",
    model=settings.model_sonnet,
    max_tokens=1200,
    system_prompt=(
        "You are a conservative kill evaluator for an autonomous venture firm. "
        "You review one venture and decide whether its explicit kill criteria are clearly hit. "
        "Soft concerns, vague low traction, or missing data are not enough. "
        "Return STRICT JSON only: {\"should_kill\":bool,\"criteria_hit\":[\"...\"],"
        "\"rationale\":\"...\",\"evidence\":{...}}. Never recommend killing without concrete evidence."
    ),
)


def parse_kill_criteria(charter: str) -> list[str]:
    """Extract bullet lines under the charter's Kill criteria section."""
    lines = charter.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("## "):
            in_section = "kill criteria" in stripped.lower()
            continue
        if in_section:
            if stripped.startswith("#"):
                break
            if stripped.startswith(('-', '*')):
                item = stripped.lstrip('-* ').strip()
                if item:
                    out.append(item)
    return out


def _json_from_text(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in kill evaluator output: {text[:200]}")
    data = json.loads(match.group(0))
    return {
        "should_kill": bool(data.get("should_kill")),
        "criteria_hit": list(data.get("criteria_hit") or []),
        "rationale": str(data.get("rationale") or ""),
        "evidence": data.get("evidence") if isinstance(data.get("evidence"), dict) else {},
    }


def _safe_fallback(context: dict[str, Any], error: str | None = None) -> dict[str, Any]:
    evidence = dict(context.get("evidence") or {})
    if error:
        evidence["agent_error"] = error[:500]
    return {
        "should_kill": False,
        "criteria_hit": [],
        "rationale": "No explicit kill criterion was conclusively hit; conservative fallback keeps the venture alive.",
        "evidence": evidence,
    }


def evaluate_venture(venture_id: int) -> dict[str, Any]:
    with session_scope() as s:
        venture = s.get(Venture, venture_id)
        if venture is None:
            raise ValueError(f"Venture {venture_id} not found")
        criteria = venture.kill_criteria_json or parse_kill_criteria(venture.charter or "")
        if not venture.kill_criteria_json and criteria:
            venture.kill_criteria_json = {"criteria": criteria, "parsed_at": datetime.now(timezone.utc).isoformat()}
        plan_count = int(s.scalar(select(func.count(Plan.id)).where(Plan.venture_id == venture.id)) or 0)
        task_rows = s.scalars(select(Task).where(Task.venture_id == venture.id)).all()
        site = s.scalars(select(Site).where(Site.venture_id == venture.id).order_by(Site.created_at.desc())).first()
        lead_count = 0
        if site:
            lead_count = int(s.scalar(select(func.count(Lead.id)).where(Lead.site_id == site.id)) or 0)
        experiment_count = int(s.scalar(select(func.count(Experiment.id))) or 0)
        days_since_charter = (datetime.now(timezone.utc) - venture.created_at).days if venture.created_at else 0
        context = {
            "venture": {"id": venture.id, "slug": venture.slug, "name": venture.name, "status": venture.status, "created_at": venture.created_at.isoformat() if venture.created_at else None},
            "kill_criteria": criteria.get("criteria", criteria) if isinstance(criteria, dict) else criteria,
            "charter": venture.charter,
            "evidence": {
                "days_since_charter": days_since_charter,
                "plan_count": plan_count,
                "task_status_counts": {status: sum(1 for t in task_rows if t.status == status) for status in sorted({t.status for t in task_rows})},
                "lead_count": lead_count,
                "site_status": site.status if site else None,
                "experiment_count_total": experiment_count,
            },
        }
    if not context["kill_criteria"]:
        return _safe_fallback(context)
    try:
        out = run_agent(EVALUATOR, [{"role": "user", "content": json.dumps(context, indent=2, default=str)}], expected_output_tokens=900)
        result = _json_from_text(out.text)
        result["agent_run_id"] = out.run_id
        return result
    except Exception as exc:
        with session_scope() as s:
            s.add(Event(kind="kill_evaluator_fallback", actor="kill_evaluator", message=f"Kill evaluator fallback for venture #{venture_id}: {exc}", payload={"venture_id": venture_id}))
        return _safe_fallback(context, str(exc))
