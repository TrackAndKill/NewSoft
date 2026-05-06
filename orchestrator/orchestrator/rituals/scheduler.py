import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from orchestrator.agents.board import review_memo
from orchestrator.agents.ceo import charter_venture
from orchestrator.agents.discovery import run_discovery
from orchestrator.agents.validator import design_experiment, file_next_stage_approvals, run_experiment_stage
from orchestrator.budget import SystemHalted, check_active
from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, Experiment, ExperimentStage, Goal, Memo, Plan, Site, SiteContent, Venture
from orchestrator.db.session import session_scope
from orchestrator.digest import send_digest

log = logging.getLogger("rituals")
scheduler = AsyncIOScheduler()


def discovery_tick() -> None:
    try:
        with session_scope() as s:
            check_active(s)
            goal = s.scalar(select(Goal).where(Goal.status == "active").order_by(Goal.created_at.desc()))
            if goal is None:
                return
            goal_id, goal_text = goal.id, f"{goal.title}\n\n{goal.description}"
    except SystemHalted:
        log.info("System halted; skipping discovery tick."); return
    try:
        result = run_discovery(goal_id, goal_text)
        with session_scope() as s:
            s.add(Event(kind="discovery_complete", actor="scheduler", message=f"Discovery run complete: {len(result.idea_ids)} ideas, top={result.top_idea_id}, memo={result.memo_id}, cost=${result.total_cost_usd:.4f}", payload={"idea_ids": result.idea_ids, "top_idea_id": result.top_idea_id, "memo_id": result.memo_id, "cost_usd": result.total_cost_usd}))
    except Exception as e:
        log.exception("discovery_tick failed")
        with session_scope() as s: s.add(Event(kind="error", actor="scheduler", message=f"Discovery tick failed: {e}"))


def board_tick() -> None:
    try:
        with session_scope() as s:
            check_active(s)
            memo_ids = [m.id for m in s.scalars(select(Memo).where(Memo.decision == "pending").order_by(Memo.created_at.asc())).all()]
    except SystemHalted:
        log.info("System halted; skipping board tick."); return
    for memo_id in memo_ids:
        try:
            outcome = review_memo(memo_id)
        except Exception as e:
            log.exception("review_memo(%s) failed", memo_id)
            with session_scope() as s: s.add(Event(kind="error", actor="board", message=f"Board review failed for memo {memo_id}: {e}"))
            continue
        if outcome.decision == "fund":
            with session_scope() as s: exists = s.scalars(select(Venture).where(Venture.memo_id == memo_id)).first()
            if exists is not None: continue
            try: charter_venture(memo_id)
            except Exception as e:
                log.exception("charter_venture(%s) failed", memo_id)
                with session_scope() as s: s.add(Event(kind="error", actor="ceo", message=f"Charter failed for memo {memo_id}: {e}"))


def validator_tick() -> None:
    try:
        with session_scope() as s:
            check_active(s)
            explore = s.scalars(select(Memo).where(Memo.decision == "explore").order_by(Memo.created_at.asc())).all()
            memo_ids = [m.id for m in explore]
            runnable_approval_ids = [
                a.id for a in s.scalars(
                    select(Approval).where(
                        Approval.status == "approved",
                        Approval.action.in_(["run_experiment_stage_research", "run_experiment_stage_outreach_draft"]),
                    )
                ).all()
                if (a.payload or {}).get("stage_id")
            ]
    except SystemHalted:
        log.info("System halted; skipping validator tick."); return
    for memo_id in memo_ids:
        try: design_experiment(memo_id)
        except Exception as e:
            log.exception("design_experiment(%s) failed", memo_id)
            with session_scope() as s: s.add(Event(kind="error", actor="validator", message=f"Validator design failed for memo {memo_id}: {e}"))
    try:
        file_next_stage_approvals()
    except Exception as e:
        log.exception("file_next_stage_approvals failed")
        with session_scope() as s: s.add(Event(kind="error", actor="validator", message=f"Stage approval filing failed: {e}"))
    for approval_id in runnable_approval_ids:
        try: run_experiment_stage(approval_id)
        except Exception as e:
            log.exception("run_experiment_stage(%s) failed", approval_id)
            with session_scope() as s: s.add(Event(kind="error", actor="validator", message=f"Experiment stage run failed for approval {approval_id}: {e}"))


def venture_tick() -> None:
    from orchestrator.agents.cto import draft_plan
    from orchestrator.agents.copywriter import draft_landing_page
    from orchestrator.agents.engineer import break_down_first_30, sync_approval_tasks
    from orchestrator.tools.sites import ensure_site_for_venture, request_site_approval, site_tick as run_site_tick
    try:
        with session_scope() as s:
            check_active(s)
            ventures = s.scalars(select(Venture).where(Venture.status == "chartered").order_by(Venture.created_at.asc())).all()
            plan_needed = []
            draft_needed = []
            deploy_approval_needed = []
            for venture in ventures:
                has_plan = s.scalars(select(Plan).where(Plan.venture_id == venture.id)).first() is not None
                if not has_plan:
                    plan_needed.append(venture.id)
                site = s.scalars(select(Site).where(Site.venture_id == venture.id).order_by(Site.created_at.desc())).first()
                content = s.scalars(select(SiteContent).where(SiteContent.venture_id == venture.id, SiteContent.kind == "landing_page")).first()
                if site and not content:
                    draft_needed.append((venture.id, site.slug))
                if site and content:
                    pending_deploy = s.scalars(select(Approval).where(Approval.action == "deploy_landing_page", Approval.status == "pending")).all()
                    any_deploy = any((a.payload or {}).get("site_id") == site.id for a in pending_deploy)
                    if not any_deploy and site.status == "staging":
                        deploy_approval_needed.append(site.id)
    except SystemHalted:
        log.info("System halted; skipping venture tick."); return
    for venture_id in plan_needed:
        try:
            plan_id = draft_plan(venture_id); task_ids = break_down_first_30(plan_id)
            with session_scope() as s: s.add(Event(kind="venture_pod_complete", actor="scheduler", message=f"Venture pod planned venture #{venture_id}: plan #{plan_id}, {len(task_ids)} tasks", payload={"venture_id": venture_id, "plan_id": plan_id, "task_ids": task_ids}))
        except Exception as e:
            log.exception("venture pod failed for venture %s", venture_id)
            with session_scope() as s: s.add(Event(kind="error", actor="venture_pod", message=f"Venture pod failed for venture {venture_id}: {e}"))
    for venture_id, slug in draft_needed:
        try:
            signup_url = f"/api/public/sites/{slug}/signup"
            content_id = draft_landing_page(venture_id, signup_url)
            with session_scope() as s: s.add(Event(kind="copywriter_complete", actor="scheduler", message=f"Copywriter drafted content #{content_id} for venture #{venture_id}", payload={"venture_id": venture_id, "site_content_id": content_id}))
        except Exception as e:
            log.exception("copywriter failed for venture %s", venture_id)
            with session_scope() as s: s.add(Event(kind="error", actor="copywriter", message=f"Copywriter failed for venture {venture_id}: {e}"))
    for site_id in deploy_approval_needed:
        try: request_site_approval(site_id, "deploy_landing_page", requested_by="scheduler")
        except Exception as e:
            log.exception("deploy approval request failed for site %s", site_id)
    try:
        synced = sync_approval_tasks()
        advanced = run_site_tick()
        if synced or advanced:
            with session_scope() as s: s.add(Event(kind="venture_tasks_synced", actor="scheduler", message=f"Synced {synced} task(s); advanced {advanced} site(s).", payload={"synced": synced, "advanced": advanced}))
    except Exception as e:
        log.exception("task/site sync failed")
        with session_scope() as s: s.add(Event(kind="error", actor="venture_pod", message=f"Task/site sync failed: {e}"))


def digest_tick() -> None:
    try: send_digest()
    except Exception as e:
        log.exception("digest_tick failed")
        with session_scope() as s: s.add(Event(kind="error", actor="digest", message=f"Digest tick failed: {e}"))


def site_tick() -> None:
    from orchestrator.tools.sites import site_tick as run_site_tick
    try:
        advanced = run_site_tick()
        if advanced:
            with session_scope() as s: s.add(Event(kind="site_tick", actor="scheduler", message=f"Advanced {advanced} site(s).", payload={"advanced": advanced}))
    except Exception as e:
        log.exception("site_tick failed")
        with session_scope() as s: s.add(Event(kind="error", actor="sites", message=f"Site tick failed: {e}"))



def kill_loop_tick() -> dict:
    """Weekly conservative kill review. Files approvals only; never kills directly."""
    from orchestrator.agents.kill_evaluator import evaluate_venture

    created: list[int] = []
    evaluated = 0
    try:
        with session_scope() as s:
            check_active(s)
            ventures = s.scalars(select(Venture).where(Venture.status.in_(["chartered", "active"])).order_by(Venture.created_at.asc())).all()
            venture_ids = [v.id for v in ventures]
    except SystemHalted:
        log.info("System halted; skipping kill loop tick.")
        return {"evaluated": 0, "created_approvals": []}

    for venture_id in venture_ids:
        try:
            with session_scope() as s:
                existing = s.scalars(select(Approval).where(Approval.action == "kill_venture")).all()
                if any((a.payload or {}).get("venture_id") == venture_id for a in existing):
                    continue
            result = evaluate_venture(venture_id)
            evaluated += 1
            if not result.get("should_kill"):
                continue
            with session_scope() as s:
                venture = s.get(Venture, venture_id)
                if venture is None or venture.status not in {"chartered", "active"}:
                    continue
                approval = Approval(
                    requested_by="kill_evaluator",
                    action="kill_venture",
                    payload={
                        "venture_id": venture_id,
                        "criteria_hit": result.get("criteria_hit", []),
                        "rationale": result.get("rationale", ""),
                        "evidence": result.get("evidence", {}),
                        "agent_run_id": result.get("agent_run_id"),
                    },
                    rationale=result.get("rationale") or f"Kill criteria hit for venture #{venture_id}",
                    status="pending",
                )
                venture.status = "kill_pending"
                s.add(approval); s.flush()
                created.append(approval.id)
                s.add(Event(kind="kill_approval_requested", actor="kill_evaluator", message=f"Kill approval #{approval.id} requested for venture #{venture_id}", payload={"venture_id": venture_id, "approval_id": approval.id, "criteria_hit": result.get("criteria_hit", [])}))
        except Exception as e:
            log.exception("kill loop failed for venture %s", venture_id)
            with session_scope() as s: s.add(Event(kind="error", actor="kill_evaluator", message=f"Kill loop failed for venture {venture_id}: {e}"))
    if not created:
        with session_scope() as s:
            s.add(Event(kind="no_kill_candidates", actor="kill_evaluator", message=f"Kill loop evaluated {evaluated} venture(s); no approvals filed.", payload={"evaluated": evaluated}))
    return {"evaluated": evaluated, "created_approvals": created}

def teardown_tick() -> dict:
    """File teardown_site approvals for killed ventures after 24h grace. Never executes teardown directly."""
    created: list[int] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with session_scope() as s:
        ventures = s.scalars(select(Venture).where(Venture.status == "killed", Venture.killed_at.is_not(None), Venture.killed_at <= cutoff)).all()
        for venture in ventures:
            site = s.scalars(select(Site).where(Site.venture_id == venture.id).order_by(Site.created_at.desc())).first()
            if site is None:
                continue
            existing = s.scalars(select(Approval).where(Approval.action == "teardown_site", Approval.status.in_(["pending", "approved"]))).all()
            if any((a.payload or {}).get("site_id") == site.id for a in existing):
                continue
            approval = Approval(
                requested_by="kill_loop",
                action="teardown_site",
                payload={"site_id": site.id, "venture_id": venture.id, "domain": site.domain, "reason": "killed_24h_grace_elapsed"},
                rationale=f"Venture {venture.slug} was killed more than 24h ago; approve teardown of live site artifacts only.",
                status="pending",
            )
            s.add(approval); s.flush(); created.append(approval.id)
            s.add(Event(kind="teardown_approval_requested", actor="teardown_tick", message=f"Teardown approval #{approval.id} requested for venture #{venture.id} site #{site.id}", payload={"approval_id": approval.id, "venture_id": venture.id, "site_id": site.id}))
    return {"created_approvals": created, "count": len(created)}


def revive_venture(venture_id: int, *, actor: str = "founder", extension_hours: int | None = None) -> dict:
    with session_scope() as s:
        venture = s.get(Venture, int(venture_id))
        if venture is None:
            raise ValueError(f"Venture {venture_id} not found")
        venture.status = "chartered"
        venture.killed_at = None
        venture.kill_reason = None
        cancelled = 0
        approvals = s.scalars(select(Approval).where(Approval.action == "teardown_site", Approval.status == "pending")).all()
        for approval in approvals:
            if (approval.payload or {}).get("venture_id") == venture.id:
                approval.status = "cancelled"
                approval.decided_by = actor
                approval.decided_at = datetime.now(timezone.utc)
                cancelled += 1
        s.add(Event(kind="venture_revived", actor=actor, message=f"Venture #{venture.id} revived; cancelled {cancelled} teardown approval(s)", payload={"venture_id": venture.id, "cancelled_teardowns": cancelled, "extension_hours": extension_hours}))
        return {"venture_id": venture.id, "status": venture.status, "cancelled_teardowns": cancelled}


def memory_reindex_tick() -> dict:
    from orchestrator.memory import memory_reindex_tick as run_memory_reindex_tick
    try:
        return run_memory_reindex_tick()
    except Exception as e:
        log.exception("memory_reindex_tick failed")
        with session_scope() as s: s.add(Event(kind="error", actor="memory", message=f"Memory reindex failed: {e}"))
        return {"indexed": 0, "errors": 1, "available": False}


def start_scheduler() -> None:
    scheduler.add_job(discovery_tick, "interval", hours=4, id="discovery_tick", replace_existing=True)
    scheduler.add_job(board_tick, "interval", minutes=15, id="board_tick", replace_existing=True)
    scheduler.add_job(validator_tick, "interval", minutes=30, id="validator_tick", replace_existing=True)
    scheduler.add_job(venture_tick, "interval", minutes=30, id="venture_tick", replace_existing=True)
    scheduler.add_job(site_tick, "interval", minutes=5, id="site_tick", replace_existing=True)
    scheduler.add_job(digest_tick, "cron", hour=8, minute=0, id="digest_tick", replace_existing=True)
    scheduler.add_job(kill_loop_tick, "cron", day_of_week="sun", hour=9, minute=0, id="kill_loop_tick", replace_existing=True)
    scheduler.add_job(teardown_tick, "interval", hours=1, id="teardown_tick", replace_existing=True)
    scheduler.add_job(memory_reindex_tick, "cron", hour=4, minute=0, id="memory_reindex_tick", replace_existing=True)
    if not scheduler.running: scheduler.start()
    log.info("Scheduler started.")
