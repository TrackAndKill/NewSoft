import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from orchestrator.agents.discovery import run_discovery
from orchestrator.budget import SystemHalted, check_active
from orchestrator.db.models import Event, Goal
from orchestrator.db.session import session_scope

log = logging.getLogger("rituals")

scheduler = AsyncIOScheduler()


def discovery_tick() -> None:
    """Pick the most recent active goal and run one discovery pass."""
    try:
        with session_scope() as s:
            check_active(s)
            goal = s.scalar(
                select(Goal).where(Goal.status == "active").order_by(Goal.created_at.desc())
            )
            if goal is None:
                return
            goal_id, goal_text = goal.id, f"{goal.title}\n\n{goal.description}"
    except SystemHalted:
        log.info("System halted; skipping discovery tick.")
        return

    try:
        result = run_discovery(goal_id, goal_text)
        with session_scope() as s:
            s.add(
                Event(
                    kind="discovery_complete",
                    actor="scheduler",
                    message=(
                        f"Discovery run complete: {len(result.idea_ids)} ideas, "
                        f"top={result.top_idea_id}, memo={result.memo_id}, "
                        f"cost=${result.total_cost_usd:.4f}"
                    ),
                    payload={
                        "idea_ids": result.idea_ids,
                        "top_idea_id": result.top_idea_id,
                        "memo_id": result.memo_id,
                        "cost_usd": result.total_cost_usd,
                    },
                )
            )
    except Exception as e:
        log.exception("discovery_tick failed")
        with session_scope() as s:
            s.add(
                Event(
                    kind="error",
                    actor="scheduler",
                    message=f"Discovery tick failed: {e}",
                )
            )


def start_scheduler() -> None:
    # Phase 1: hourly discovery tick. Cheap with caps in place.
    scheduler.add_job(discovery_tick, "interval", hours=1, id="discovery_tick", replace_existing=True)
    scheduler.start()
    log.info("Scheduler started.")
