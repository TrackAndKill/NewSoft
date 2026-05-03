from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from orchestrator.agents.discovery import run_discovery
from orchestrator.agents.validator import run_experiment
from orchestrator.db.init_db import init_db
from orchestrator.db.models import (
    AgentRun,
    Approval,
    BoardReview,
    Event,
    Experiment,
    Goal,
    Idea,
    Memo,
    SystemState,
    ToolCall,
    Venture,
)
from orchestrator.db.session import session_scope
from orchestrator.rituals.scheduler import board_tick, discovery_tick, start_scheduler, validator_tick


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(title="NewSoft Orchestrator", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class GoalIn(BaseModel):
    title: str
    description: str = ""


class ApprovalDecision(BaseModel):
    decided_by: str = "founder"
    approve: bool


class SystemUpdate(BaseModel):
    active: bool | None = None
    dry_run: bool | None = None
    daily_spend_cap_usd: float | None = None


def _row_to_dict(row: Any) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _empty_cost_bucket() -> dict:
    return {"total_usd": 0.0, "by_agent": {}}


@app.get("/api/status")
def status() -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        return _row_to_dict(state) if state else {}


@app.get("/api/costs")
def costs() -> dict:
    today = datetime.now(timezone.utc).date()
    start = datetime.combine(today - timedelta(days=6), datetime.min.time(), tzinfo=timezone.utc)
    buckets: dict[str, dict] = {
        "today": _empty_cost_bucket(),
        "yesterday": _empty_cost_bucket(),
        "last_7d": _empty_cost_bucket(),
    }
    day_bucket = func.date_trunc("day", AgentRun.started_at).label("day_bucket")
    with session_scope() as s:
        rows = s.execute(
            select(day_bucket, AgentRun.agent, func.sum(AgentRun.cost_usd))
            .where(AgentRun.started_at >= start)
            .group_by(day_bucket, AgentRun.agent)
        ).all()
    for day_dt, agent, total in rows:
        if day_dt is None:
            continue
        day = day_dt.date() if hasattr(day_dt, "date") else day_dt
        amount = float(total or 0)
        buckets["last_7d"]["total_usd"] += amount
        buckets["last_7d"]["by_agent"][agent] = buckets["last_7d"]["by_agent"].get(agent, 0.0) + amount
        if day == today:
            key = "today"
        elif day == today - timedelta(days=1):
            key = "yesterday"
        else:
            continue
        buckets[key]["total_usd"] += amount
        buckets[key]["by_agent"][agent] = buckets[key]["by_agent"].get(agent, 0.0) + amount
    for bucket in buckets.values():
        bucket["total_usd"] = round(bucket["total_usd"], 6)
        bucket["by_agent"] = {k: round(v, 6) for k, v in sorted(bucket["by_agent"].items())}
    return buckets


@app.post("/api/system")
def update_system(payload: SystemUpdate) -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        if state is None:
            raise HTTPException(404, "system_state missing")
        if payload.active is not None:
            state.active = payload.active
            s.add(Event(kind="system", actor="founder", message=f"System {'activated' if payload.active else 'HALTED'}"))
        if payload.dry_run is not None:
            state.dry_run = payload.dry_run
        if payload.daily_spend_cap_usd is not None:
            state.daily_spend_cap_usd = payload.daily_spend_cap_usd
        return _row_to_dict(state)


@app.post("/api/system/kill")
def kill_switch() -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        if state is None:
            raise HTTPException(404, "system_state missing")
        state.active = False
        s.add(Event(kind="system", actor="founder", message="KILL SWITCH engaged"))
        return _row_to_dict(state)


@app.get("/api/goals")
def list_goals() -> list[dict]:
    with session_scope() as s:
        return [_row_to_dict(r) for r in s.scalars(select(Goal).order_by(desc(Goal.created_at))).all()]


@app.post("/api/goals")
def create_goal(payload: GoalIn) -> dict:
    with session_scope() as s:
        g = Goal(title=payload.title, description=payload.description, status="active")
        s.add(g)
        s.flush()
        s.add(Event(kind="goal_created", actor="founder", message=f"Goal: {g.title}", payload={"goal_id": g.id}))
        return _row_to_dict(g)


@app.post("/api/discovery/run")
def trigger_discovery(background: BackgroundTasks) -> dict:
    background.add_task(discovery_tick)
    return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/api/board/run")
def trigger_board(background: BackgroundTasks) -> dict:
    background.add_task(board_tick)
    return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/api/validator/run")
def trigger_validator(background: BackgroundTasks) -> dict:
    background.add_task(validator_tick)
    return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/ideas")
def list_ideas(limit: int = 50) -> list[dict]:
    with session_scope() as s:
        return [_row_to_dict(r) for r in s.scalars(select(Idea).order_by(desc(Idea.created_at)).limit(limit)).all()]


@app.get("/api/memos")
def list_memos(limit: int = 20) -> list[dict]:
    with session_scope() as s:
        return [_row_to_dict(r) for r in s.scalars(select(Memo).order_by(desc(Memo.created_at)).limit(limit)).all()]


@app.get("/api/board/reviews")
def list_board_reviews(limit: int = 100, memo_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(BoardReview).order_by(desc(BoardReview.created_at)).limit(limit)
        if memo_id is not None:
            q = select(BoardReview).where(BoardReview.memo_id == memo_id).order_by(BoardReview.created_at.asc())
        return [_row_to_dict(r) for r in s.scalars(q).all()]


@app.get("/api/ventures")
def list_ventures() -> list[dict]:
    with session_scope() as s:
        return [_row_to_dict(r) for r in s.scalars(select(Venture).order_by(desc(Venture.created_at))).all()]


@app.get("/api/ventures/{slug}")
def get_venture(slug: str) -> dict:
    with session_scope() as s:
        v = s.scalar(select(Venture).where(Venture.slug == slug))
        if v is None:
            raise HTTPException(404, "venture not found")
        memo = s.get(Memo, v.memo_id)
        idea = s.get(Idea, v.idea_id)
        reviews = s.scalars(select(BoardReview).where(BoardReview.memo_id == v.memo_id).order_by(BoardReview.created_at.asc())).all()
        return {"venture": _row_to_dict(v), "memo": _row_to_dict(memo) if memo else None, "idea": _row_to_dict(idea) if idea else None, "reviews": [_row_to_dict(r) for r in reviews]}


@app.get("/api/events")
def list_events(limit: int = 100) -> list[dict]:
    with session_scope() as s:
        return [_row_to_dict(r) for r in s.scalars(select(Event).order_by(desc(Event.created_at)).limit(limit)).all()]


@app.get("/api/agent_runs/{run_id}")
def get_agent_run(run_id: int) -> dict:
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(404, "agent run not found")
        data = _row_to_dict(run)
        tool_rows = s.scalars(select(ToolCall).where(ToolCall.agent_run_id == run_id).order_by(ToolCall.created_at.asc())).all()
        data["tool_call_rows"] = [_row_to_dict(r) for r in tool_rows]
        return data


@app.get("/api/experiments")
def list_experiments(limit: int = 100, memo_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(Experiment).order_by(desc(Experiment.created_at)).limit(limit)
        if memo_id is not None:
            q = select(Experiment).where(Experiment.memo_id == memo_id).order_by(desc(Experiment.created_at))
        return [_row_to_dict(r) for r in s.scalars(q).all()]


@app.get("/api/approvals")
def list_approvals(status: str = "pending") -> list[dict]:
    with session_scope() as s:
        return [_row_to_dict(r) for r in s.scalars(select(Approval).where(Approval.status == status).order_by(desc(Approval.created_at))).all()]


@app.post("/api/approvals/{approval_id}")
def decide_approval(approval_id: int, payload: ApprovalDecision, background: BackgroundTasks) -> dict:
    should_run_experiment = False
    with session_scope() as s:
        a = s.get(Approval, approval_id)
        if a is None:
            raise HTTPException(404, "approval not found")
        a.status = "approved" if payload.approve else "rejected"
        a.decided_by = payload.decided_by
        a.decided_at = datetime.now(timezone.utc)
        should_run_experiment = a.status == "approved" and a.action == "run_experiment"
        s.add(Event(kind="approval_decided", actor=payload.decided_by, message=f"Approval #{a.id} {a.status}: {a.action}", payload={"approval_id": a.id}))
        data = _row_to_dict(a)
    if should_run_experiment:
        background.add_task(run_experiment, approval_id)
    return data
