import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from orchestrator.agents.board import review_memo
from orchestrator.agents.ceo import charter_venture
from orchestrator.agents.discovery import run_discovery
from orchestrator.agents.validator import design_experiment
from orchestrator.budget import SystemHalted, check_active
from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, Experiment, Goal, Memo, Plan, Site, SiteContent, Venture
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
            memo_ids = []
            for memo in explore:
                has_experiment = s.scalars(select(Experiment).where(Experiment.memo_id == memo.id)).first() is not None
                pending_approvals = s.scalars(select(Approval).where(Approval.action == "run_experiment", Approval.status == "pending")).all()
                has_approval = any((a.payload or {}).get("memo_id") == memo.id for a in pending_approvals)
                if not has_experiment and not has_approval: memo_ids.append(memo.id)
    except SystemHalted:
        log.info("System halted; skipping validator tick."); return
    for memo_id in memo_ids:
        try: design_experiment(memo_id)
        except Exception as e:
            log.exception("design_experiment(%s) failed", memo_id)
            with session_scope() as s: s.add(Event(kind="error", actor="validator", message=f"Validator design failed for memo {memo_id}: {e}"))


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


def start_scheduler() -> None:
    scheduler.add_job(discovery_tick, "interval", hours=4, id="discovery_tick", replace_existing=True)
    scheduler.add_job(board_tick, "interval", minutes=15, id="board_tick", replace_existing=True)
    scheduler.add_job(validator_tick, "interval", minutes=30, id="validator_tick", replace_existing=True)
    scheduler.add_job(venture_tick, "interval", minutes=30, id="venture_tick", replace_existing=True)
    scheduler.add_job(site_tick, "interval", minutes=5, id="site_tick", replace_existing=True)
    scheduler.add_job(digest_tick, "cron", hour=8, minute=0, id="digest_tick", replace_existing=True)
    if not scheduler.running: scheduler.start()
    log.info("Scheduler started.")
