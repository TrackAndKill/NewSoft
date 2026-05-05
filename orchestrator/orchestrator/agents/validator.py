"""Validator pod for staged EXPLORE memo experiments."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, Experiment, ExperimentStage, Idea, Memo
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent
from orchestrator.tools.memory import TOOLS as MEMORY_TOOLS
from orchestrator.tools.search import TOOLS as SEARCH_TOOLS
from orchestrator.tools.search import search_tools_available

EXPERIMENT_REAL_WORLD_BUDGET_USD = 200.0
STAGE_COST_CAPS = {1: 0.50, 2: 0.30, 3: 0.20}
STAGES = {
    1: ("research_only", "run_experiment_stage_research"),
    2: ("outreach_draft", "run_experiment_stage_outreach_draft"),
    3: ("validation_run", "run_experiment_stage_validation"),
}

STAGE1 = AgentSpec(
    name="validator",
    role="Validator Stage 1 Research",
    model=settings.model_sonnet,
    max_tokens=2200,
    tools=(SEARCH_TOOLS if search_tools_available() else []) + MEMORY_TOOLS,
    system_prompt=(
        "You are the Validator. Run Stage 1 research_only for a venture memo. "
        "No drafting, no spend, no publishing, no contact. Use search_memory once and live web tools if available. "
        "Return STRICT JSON: {\"summary\":\"...\",\"signals\":[{\"url\":\"...\",\"evidence\":\"...\"}],"
        "\"icp\":\"...\",\"channels\":[\"...\"],\"stage2_plan\":\"...\",\"success_metric\":\"...\"}."
    ),
)

STAGE2 = AgentSpec(
    name="validator",
    role="Validator Stage 2 Outreach Draft",
    model=settings.model_sonnet,
    max_tokens=2000,
    tools=MEMORY_TOOLS,
    system_prompt=(
        "You are the Validator. Run Stage 2 outreach_draft from approved Stage 1 research. "
        "No sending, no spend, no publishing. Use search_memory once for prior lessons. "
        "Return STRICT JSON: {\"email_variants\":[{\"subject\":\"...\",\"body\":\"...\"}],"
        "\"dm_template\":\"...\",\"landing_page_brief\":\"...\",\"stage3_plan\":\"...\",\"operator_review_notes\":[\"...\"]}."
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


def _stage_summary(stage_index: int, memo: Memo, idea: Idea | None) -> str:
    title = idea.title if idea else f"memo #{memo.id}"
    if stage_index == 1:
        return f"Stage 1 research_only for memo #{memo.id}: public-source prospect signal map for {title}. No external action, no spend."
    if stage_index == 2:
        return f"Stage 2 outreach_draft for memo #{memo.id}: draft emails/DM/landing brief only. No sending, no publishing, no spend."
    return f"Stage 3 validation_run for memo #{memo.id}: gated live validation plan for {title}. May include domain/site/outreach sub-actions; do not approve in Phase 7 smoke."


def ensure_staged_experiment(memo_id: int) -> int:
    """Create Experiment parent and Stage 1 design if needed. Never auto-runs."""
    with session_scope() as s:
        memo, idea = _memo_context(s, memo_id)
        exp = s.scalars(select(Experiment).where(Experiment.memo_id == memo_id).order_by(Experiment.created_at.desc())).first()
        if exp is None:
            exp = Experiment(memo_id=memo_id, approval_id=None, design_json={"phase": 7, "staged": True}, status="designed", current_stage="research_only")
            s.add(exp); s.flush()
        stage1 = s.scalars(select(ExperimentStage).where(ExperimentStage.experiment_id == exp.id, ExperimentStage.stage_index == 1)).first()
        if stage1 is None:
            stage1 = ExperimentStage(
                experiment_id=exp.id,
                stage_name="research_only",
                stage_index=1,
                status="designed",
                design_json={"memo_id": memo_id, "goal": _stage_summary(1, memo, idea), "agent_cost_cap_usd": STAGE_COST_CAPS[1], "real_money_usd": 0.0},
            )
            s.add(stage1)
            s.add(Event(kind="experiment_stage_designed", actor="validator", message=f"Stage 1 research_only designed for memo #{memo_id}", payload={"memo_id": memo_id, "experiment_id": exp.id, "stage_index": 1}))
        return exp.id


def file_next_stage_approvals() -> list[int]:
    created: list[int] = []
    with session_scope() as s:
        stages = s.scalars(select(ExperimentStage).where(ExperimentStage.status == "designed").order_by(ExperimentStage.created_at.asc())).all()
        for stage in stages:
            if stage.approval_id:
                continue
            exp = s.get(Experiment, stage.experiment_id)
            if exp is None:
                continue
            memo, idea = _memo_context(s, exp.memo_id)
            _, action = STAGES[stage.stage_index]
            approval = Approval(
                requested_by="validator",
                action=action,
                payload={"memo_id": exp.memo_id, "experiment_id": exp.id, "stage_id": stage.id, "stage_index": stage.stage_index, "stage_name": stage.stage_name, "summary": _stage_summary(stage.stage_index, memo, idea)},
                rationale=_stage_summary(stage.stage_index, memo, idea),
                status="pending",
            )
            s.add(approval); s.flush()
            stage.approval_id = approval.id
            stage.status = "pending_approval"
            created.append(approval.id)
            s.add(Event(kind="experiment_stage_approval_requested", actor="validator", message=f"Stage {stage.stage_index} approval #{approval.id} filed for memo #{exp.memo_id}", payload={"memo_id": exp.memo_id, "experiment_id": exp.id, "stage_id": stage.id, "approval_id": approval.id}))
    return created


def design_experiment(memo_id: int) -> Approval | None:
    ensure_staged_experiment(memo_id)
    before: set[int]
    with session_scope() as s:
        before = {a.id for a in s.scalars(select(Approval).where(Approval.status == "pending")).all()}
    file_next_stage_approvals()
    with session_scope() as s:
        approval = s.scalars(select(Approval).where(Approval.status == "pending").order_by(Approval.created_at.desc())).first()
        return approval if approval and approval.id not in before else None


def _run_stage1(stage: ExperimentStage, exp: Experiment, memo: Memo, idea: Idea | None) -> tuple[str, dict, int, float]:
    context = {"memo_id": memo.id, "idea": {"title": idea.title if idea else "?", "summary": idea.summary if idea else "", "source": idea.source if idea else ""}, "memo": memo.content, "stage_design": stage.design_json}
    out = run_agent(STAGE1, [{"role": "user", "content": json.dumps(context, indent=2)}], expected_output_tokens=1600)
    data = _parse_json(out.text)
    md = "\n".join([f"# Stage 1 research: memo #{memo.id}", "", data.get("summary", ""), "", "## ICP", data.get("icp", ""), "", "## Signals", *[f"- {x.get('url','')}: {x.get('evidence','')}" for x in data.get("signals", [])], "", "## Channels", *[f"- {c}" for c in data.get("channels", [])], "", "## Proposed Stage 2", data.get("stage2_plan", "")])
    return md, data, out.run_id, out.cost_usd


def _run_stage2(stage: ExperimentStage, exp: Experiment, memo: Memo, idea: Idea | None) -> tuple[str, dict, int, float]:
    with session_scope() as s:
        prior = s.scalars(select(ExperimentStage).where(ExperimentStage.experiment_id == exp.id, ExperimentStage.stage_index == 1)).first()
        prior_result = prior.result_json if prior else {}
    context = {"memo_id": memo.id, "idea": {"title": idea.title if idea else "?", "summary": idea.summary if idea else "", "source": idea.source if idea else ""}, "memo": memo.content, "stage1_result": prior_result, "stage_design": stage.design_json}
    out = run_agent(STAGE2, [{"role": "user", "content": json.dumps(context, indent=2)}], expected_output_tokens=1500)
    data = _parse_json(out.text)
    emails = data.get("email_variants", [])[:3]
    md = "\n".join([f"# Stage 2 drafts: memo #{memo.id}", "", "## Email variants", *[f"### Variant {i+1}: {e.get('subject','')}\n{e.get('body','')}" for i, e in enumerate(emails)], "", "## DM template", data.get("dm_template", ""), "", "## Landing page brief", data.get("landing_page_brief", ""), "", "## Proposed Stage 3", data.get("stage3_plan", "")])
    return md, data, out.run_id, out.cost_usd


def _create_next_stage(s, exp: Experiment, completed_index: int) -> None:
    next_index = completed_index + 1
    if next_index not in STAGES:
        return
    existing = s.scalars(select(ExperimentStage).where(ExperimentStage.experiment_id == exp.id, ExperimentStage.stage_index == next_index)).first()
    if existing:
        return
    name, _ = STAGES[next_index]
    stage = ExperimentStage(experiment_id=exp.id, stage_name=name, stage_index=next_index, status="designed", design_json={"experiment_id": exp.id, "agent_cost_cap_usd": STAGE_COST_CAPS[next_index], "real_money_usd": 0.0 if next_index < 3 else EXPERIMENT_REAL_WORLD_BUDGET_USD})
    s.add(stage)


def run_experiment_stage(approval_id: int, force_live: bool | None = None) -> ExperimentRunResult:
    with session_scope() as s:
        approval = s.get(Approval, approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        if approval.status != "approved":
            raise ValueError(f"Approval {approval_id} is {approval.status}, expected approved")
        payload = approval.payload or {}
        stage_id = int(payload.get("stage_id") or 0)
        stage = s.get(ExperimentStage, stage_id)
        if stage is None:
            raise ValueError(f"Stage {stage_id} not found")
        exp = s.get(Experiment, stage.experiment_id)
        memo, idea = _memo_context(s, exp.memo_id)
        if stage.status == "done":
            return ExperimentRunResult(exp.id, stage.cost_usd, stage.status)
        if stage.stage_index == 3:
            # Phase 7 ships the gate but smoke must stop before live validation.
            stage.status = "pending_approval"
            raise ValueError("Stage 3 validation_run is gated for a later operator-approved live run; Phase 7 smoke must not execute it")
        stage.status = "running"
        exp.current_stage = stage.stage_name
        exp.status = "running"
        stage_index = stage.stage_index

    if stage_index == 1:
        result_md, result_json, run_id, cost = _run_stage1(stage, exp, memo, idea)
    elif stage_index == 2:
        result_md, result_json, run_id, cost = _run_stage2(stage, exp, memo, idea)
    else:
        raise ValueError(f"Unsupported stage {stage_index}")
    status = "done" if cost <= STAGE_COST_CAPS[stage_index] else "failed"
    if status == "failed":
        result_md += f"\n\n**Aborted:** agent cost ${cost:.4f} exceeded cap ${STAGE_COST_CAPS[stage_index]:.2f}."

    with session_scope() as s:
        stage = s.get(ExperimentStage, stage_id)
        exp = s.get(Experiment, stage.experiment_id)
        stage.status = status
        stage.result_md = result_md
        stage.result_json = result_json
        stage.agent_run_id = run_id
        stage.cost_usd = cost
        stage.completed_at = datetime.now(timezone.utc)
        exp.status = "done" if stage_index == 2 and status == "done" else status
        exp.result_md = (exp.result_md or "") + f"\n\n{result_md}"
        exp.cost_usd = float(exp.cost_usd or 0) + cost
        exp.current_stage = STAGES.get(stage_index + 1, (stage.stage_name,))[0] if status == "done" and stage_index < 3 else stage.stage_name
        if status == "done":
            _create_next_stage(s, exp, stage_index)
        s.add(Event(kind="experiment_stage_result", actor="validator", message=f"Stage {stage_index} {status} for experiment #{exp.id}", payload={"memo_id": exp.memo_id, "experiment_id": exp.id, "stage_id": stage.id, "stage_index": stage_index, "run_id": run_id, "cost_usd": cost}))
    file_next_stage_approvals()
    return ExperimentRunResult(exp.id, cost, status)


def run_experiment(approval_id: int, force_live: bool | None = None) -> ExperimentRunResult:
    """Back-compat alias: route legacy run_experiment approvals to Stage 1."""
    with session_scope() as s:
        approval = s.get(Approval, approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        memo_id = int((approval.payload or {}).get("memo_id"))
        exp_id = ensure_staged_experiment(memo_id)
        stage = s.scalars(select(ExperimentStage).where(ExperimentStage.experiment_id == exp_id, ExperimentStage.stage_index == 1)).first()
        stage.approval_id = approval_id
        stage.status = "pending_approval"
        approval.action = "run_experiment_stage_research"
        approval.payload = {**(approval.payload or {}), "experiment_id": exp_id, "stage_id": stage.id, "stage_index": 1, "stage_name": "research_only"}
    return run_experiment_stage(approval_id, force_live)
