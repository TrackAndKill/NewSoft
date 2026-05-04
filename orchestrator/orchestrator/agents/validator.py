"""Validator pod for EXPLORE memos."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, Experiment, Idea, Memo
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent
from orchestrator.tools.search import TOOLS as SEARCH_TOOLS
from orchestrator.tools.search import search_tools_available

EXPERIMENT_REAL_WORLD_BUDGET_USD = 200.0
EXPERIMENT_AGENT_COST_CAP_USD = 0.50

VALIDATOR_DESIGN = AgentSpec(
    name="validator",
    role="Validator",
    model=settings.model_sonnet,
    max_tokens=1400,
    system_prompt=(
        "You are the Validator for an autonomous venture firm. For an EXPLORE memo, design one cheap validation experiment. "
        "The experiment must cost under $200 real-world spend, require operator approval before any external action, and be measurable. "
        "Return STRICT JSON: {\"hypothesis\":\"...\",\"method\":\"landing_page|cold_email_list|fake_door|ad_probe|desk_research\","
        "\"success_metric\":\"...\",\"deadline\":\"...\",\"estimated_cost_usd\":N,\"steps\":[\"...\"],\"approval_request\":\"...\"}."
    ),
)

VALIDATOR_RUN = AgentSpec(
    name="validator",
    role="Validator Experiment Runner",
    model=settings.model_sonnet,
    max_tokens=1800,
    tools=SEARCH_TOOLS if search_tools_available() else [],
    system_prompt=(
        "You are running an approved Phase 3 validation experiment using only read-only web tools. "
        "Do not spend money, publish, send email, contact customers, create accounts, or make external commitments. "
        "Use web_search/fetch_url if available to validate demand, competitor pricing, channel reachability, and pain evidence. "
        "Return a concise Markdown validation report with: summary, evidence URLs, result vs success metric, recommendation (fund/explore/pass), and next step."
    ),
)


@dataclass
class ExperimentRunResult:
    experiment_id: int
    cost_usd: float
    status: str


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in validator output: {text[:200]}")
    return json.loads(match.group(0))


def _memo_context(s, memo_id: int) -> tuple[Memo, Idea | None]:
    memo = s.get(Memo, memo_id)
    if memo is None:
        raise ValueError(f"Memo {memo_id} not found")
    return memo, s.get(Idea, memo.idea_id)


def design_experiment(memo_id: int) -> Approval | None:
    """Design a sub-$200 experiment and file an Approval. Never auto-runs."""
    with session_scope() as s:
        memo, idea = _memo_context(s, memo_id)
        existing_experiment = s.scalars(select(Experiment).where(Experiment.memo_id == memo_id)).first()
        existing_approval = s.scalars(
            select(Approval).where(Approval.action == "run_experiment", Approval.status == "pending")
        ).all()
        for approval in existing_approval:
            if (approval.payload or {}).get("memo_id") == memo_id:
                return None
        if existing_experiment is not None:
            return None
        context = {
            "memo_id": memo.id,
            "idea": {"title": idea.title if idea else "?", "summary": idea.summary if idea else "", "source": idea.source if idea else ""},
            "memo": memo.content,
            "board_decision": memo.decision,
        }

    out = run_agent(VALIDATOR_DESIGN, [{"role": "user", "content": json.dumps(context, indent=2)}], expected_output_tokens=900)
    data = _parse_json(out.text)
    estimated = float(data.get("estimated_cost_usd") or 0)
    if estimated > EXPERIMENT_REAL_WORLD_BUDGET_USD:
        data["estimated_cost_usd"] = EXPERIMENT_REAL_WORLD_BUDGET_USD
        data["budget_note"] = "Clamped to hard $200 experiment budget."

    with session_scope() as s:
        approval = Approval(
            requested_by="validator",
            action="run_experiment",
            payload={"memo_id": memo_id, "design_json": data, "agent_run_id": out.run_id},
            rationale=data.get("approval_request") or data.get("hypothesis") or "Run validation experiment",
            status="pending",
        )
        s.add(approval)
        s.flush()
        s.add(
            Event(
                kind="experiment_designed",
                actor="validator",
                message=f"Validation experiment designed for memo #{memo_id}; awaiting approval #{approval.id}",
                payload={"memo_id": memo_id, "approval_id": approval.id, "run_id": out.run_id},
            )
        )
        return approval


def run_experiment(approval_id: int, force_live: bool | None = None) -> ExperimentRunResult:
    """Run an approved read-only experiment and flip memo back to pending for Board re-vote.

    force_live is accepted for the common approval dispatcher contract. The
    experiment runner remains read-only in Phase 6.
    """
    with session_scope() as s:
        approval = s.get(Approval, approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        if approval.action != "run_experiment":
            raise ValueError(f"Approval {approval_id} action is {approval.action}, expected run_experiment")
        if approval.status != "approved":
            raise ValueError(f"Approval {approval_id} is {approval.status}, expected approved")
        memo_id = int((approval.payload or {}).get("memo_id"))
        design = (approval.payload or {}).get("design_json") or {}
        memo, idea = _memo_context(s, memo_id)
        existing = s.scalars(select(Experiment).where(Experiment.approval_id == approval_id)).first()
        if existing:
            return ExperimentRunResult(existing.id, existing.cost_usd, existing.status)
        exp = Experiment(memo_id=memo_id, approval_id=approval_id, design_json=design, status="running")
        s.add(exp)
        s.flush()
        experiment_id = exp.id
        context = {
            "memo_id": memo_id,
            "idea": {"title": idea.title if idea else "?", "summary": idea.summary if idea else "", "source": idea.source if idea else ""},
            "memo": memo.content,
            "approved_design": design,
            "agent_cost_cap_usd": EXPERIMENT_AGENT_COST_CAP_USD,
        }

    out = run_agent(VALIDATOR_RUN, [{"role": "user", "content": json.dumps(context, indent=2)}], expected_output_tokens=1200)
    status = "done" if out.cost_usd <= EXPERIMENT_AGENT_COST_CAP_USD else "failed"
    result_md = out.text
    if out.cost_usd > EXPERIMENT_AGENT_COST_CAP_USD:
        result_md += f"\n\n**Aborted:** agent cost ${out.cost_usd:.4f} exceeded cap ${EXPERIMENT_AGENT_COST_CAP_USD:.2f}."

    with session_scope() as s:
        exp = s.get(Experiment, experiment_id)
        exp.status = status
        exp.result_md = result_md
        exp.cost_usd = out.cost_usd
        exp.completed_at = datetime.now(timezone.utc)
        memo = s.get(Memo, memo_id)
        if memo is not None and status == "done":
            memo.decision = "pending"
            memo.decision_at = None
        s.add(
            Event(
                kind="validation_report",
                actor="validator",
                message=f"Validation experiment #{experiment_id} {status} for memo #{memo_id}; Board can re-vote",
                payload={"memo_id": memo_id, "experiment_id": experiment_id, "run_id": out.run_id, "status": status},
            )
        )
    return ExperimentRunResult(experiment_id, out.cost_usd, status)
