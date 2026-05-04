"""Postmortem writer for killed/pivoted ventures."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select

from orchestrator.config import settings
from orchestrator.db.models import AgentRun, Event, Idea, Memo, Plan, Postmortem, Site, Task, Venture
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent

WRITER = AgentSpec(
    name="postmortem_writer",
    role="Postmortem Writer",
    model=settings.model_opus,
    max_tokens=1800,
    system_prompt=(
        "You are the postmortem writer for an autonomous venture firm. "
        "Write a useful, honest postmortem from the available operating record. "
        "Return STRICT JSON only: {\"content_md\":\"# ...\",\"lessons_md\":\"- ...\"}. "
        "Do not invent metrics; label unknowns clearly."
    ),
)


def _json_from_text(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in postmortem output: {text[:200]}")
    return json.loads(match.group(0))


def _fallback_content(context: dict[str, Any], kill_reason: str, error: str | None = None) -> tuple[str, str]:
    venture = context.get("venture", {})
    tasks = context.get("tasks", [])
    completed = [t for t in tasks if t.get("status") in {"done", "completed"}]
    blocked = [t for t in tasks if t.get("status") not in {"done", "completed"}]
    content = [
        f"# Postmortem: {venture.get('name', 'Unknown venture')}",
        "",
        f"**Kill reason.** {kill_reason}",
        "",
        "## What happened",
        f"The venture `{venture.get('slug')}` was reviewed for shutdown. The operating record had {len(tasks)} task(s), {len(completed)} completed task(s), and {len(blocked)} non-completed task(s).",
        "",
        "## Evidence",
        "```json",
        json.dumps(context.get("evidence", {}), indent=2, default=str),
        "```",
        "",
        "## Notes",
        "This fallback postmortem was generated from durable records without inventing missing metrics.",
    ]
    if error:
        content += ["", f"Writer fallback reason: `{error[:300]}`"]
    lessons = "\n".join([
        "- Keep explicit kill criteria measurable and tied to dated evidence.",
        "- Preserve venture sites and operating records after kill decisions; do not delete learning history.",
        "- Require owner approval for shutdowns even when an evaluator flags criteria as hit.",
    ])
    return "\n".join(content), lessons


def _context_for_venture(s, venture: Venture) -> dict[str, Any]:
    memo = s.get(Memo, venture.memo_id)
    idea = s.get(Idea, venture.idea_id)
    plan = s.scalars(select(Plan).where(Plan.venture_id == venture.id).order_by(desc(Plan.created_at))).first()
    tasks = s.scalars(select(Task).where(Task.venture_id == venture.id).order_by(Task.created_at.asc())).all()
    site = s.scalars(select(Site).where(Site.venture_id == venture.id).order_by(desc(Site.created_at))).first()
    return {
        "venture": {c.name: getattr(venture, c.name) for c in venture.__table__.columns},
        "idea": {c.name: getattr(idea, c.name) for c in idea.__table__.columns} if idea else None,
        "memo": {"id": memo.id, "decision": memo.decision, "content": memo.content} if memo else None,
        "plan": {"id": plan.id, "content_md": plan.content_md, "plan_30": plan.plan_30, "plan_60": plan.plan_60, "plan_90": plan.plan_90} if plan else None,
        "tasks": [{c.name: getattr(t, c.name) for c in t.__table__.columns} for t in tasks],
        "site": {c.name: getattr(site, c.name) for c in site.__table__.columns} if site else None,
        "evidence": {"task_count": len(tasks), "site_status": site.status if site else None},
    }


def write_postmortem(venture_id: int, kill_reason: str) -> int:
    with session_scope() as s:
        venture = s.get(Venture, venture_id)
        if venture is None:
            raise ValueError(f"Venture {venture_id} not found")
        if venture.status not in {"chartered", "active", "kill_pending", "killed"}:
            raise ValueError(f"Venture {venture_id} status {venture.status} cannot receive a postmortem")
        existing = s.scalars(select(Postmortem).where(Postmortem.venture_id == venture_id).order_by(desc(Postmortem.created_at))).first()
        if existing and venture.status == "killed":
            return existing.id
        context = _context_for_venture(s, venture)

    agent_run_id: int | None = None
    try:
        out = run_agent(WRITER, [{"role": "user", "content": json.dumps({"kill_reason": kill_reason, **context}, indent=2, default=str)}], expected_output_tokens=1400)
        data = _json_from_text(out.text)
        content_md = str(data.get("content_md") or "").strip()
        lessons_md = str(data.get("lessons_md") or "").strip()
        if not content_md or not lessons_md:
            raise ValueError("postmortem JSON missing content_md or lessons_md")
        agent_run_id = out.run_id
    except Exception as exc:
        content_md, lessons_md = _fallback_content(context, kill_reason, str(exc))

    with session_scope() as s:
        row = Postmortem(venture_id=venture_id, content_md=content_md, lessons_md=lessons_md, agent_run_id=agent_run_id)
        s.add(row); s.flush()
        s.add(Event(kind="postmortem_written", actor="postmortem_writer", message=f"Postmortem #{row.id} written for venture #{venture_id}", payload={"venture_id": venture_id, "postmortem_id": row.id, "agent_run_id": agent_run_id}))
        return row.id
