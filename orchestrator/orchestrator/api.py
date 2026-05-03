from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, select

from orchestrator.agents.discovery import run_discovery
from orchestrator.db.init_db import init_db
from orchestrator.db.models import (
    Approval,
    BoardReview,
    Event,
    Goal,
    Idea,
    Memo,
    SystemState,
    Venture,
)
from orchestrator.db.session import session_scope
from orchestrator.rituals.scheduler import board_tick, discovery_tick, start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(title="NewSoft Orchestrator", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/api/status")
def status() -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        return _row_to_dict(state) if state else {}


@app.post("/api/system")
def update_system(payload: SystemUpdate) -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        if state is None:
            raise HTTPException(404, "system_state missing")
        if payload.active is not None:
            state.active = payload.active
            s.add(
                Event(
                    kind="system",
                    actor="founder",
                    message=f"System {'activated' if payload.active else 'HALTED'}",
                )
            )
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
        rows = s.scalars(select(Goal).order_by(desc(Goal.created_at))).all()
        return [_row_to_dict(r) for r in rows]


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


@app.get("/api/ideas")
def list_ideas(limit: int = 50) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Idea).order_by(desc(Idea.created_at)).limit(limit)).all()
        return [_row_to_dict(r) for r in rows]


@app.get("/api/memos")
def list_memos(limit: int = 20) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Memo).order_by(desc(Memo.created_at)).limit(limit)).all()
        return [_row_to_dict(r) for r in rows]


@app.get("/api/board/reviews")
def list_board_reviews(limit: int = 100, memo_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(BoardReview).order_by(desc(BoardReview.created_at)).limit(limit)
        if memo_id is not None:
            q = select(BoardReview).where(BoardReview.memo_id == memo_id).order_by(BoardReview.created_at.asc())
        rows = s.scalars(q).all()
        return [_row_to_dict(r) for r in rows]


@app.get("/api/ventures")
def list_ventures() -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Venture).order_by(desc(Venture.created_at))).all()
        return [_row_to_dict(r) for r in rows]


@app.get("/api/ventures/{slug}")
def get_venture(slug: str) -> dict:
    with session_scope() as s:
        v = s.scalar(select(Venture).where(Venture.slug == slug))
        if v is None:
            raise HTTPException(404, "venture not found")
        memo = s.get(Memo, v.memo_id)
        idea = s.get(Idea, v.idea_id)
        reviews = s.scalars(
            select(BoardReview).where(BoardReview.memo_id == v.memo_id).order_by(BoardReview.created_at.asc())
        ).all()
        return {
            "venture": _row_to_dict(v),
            "memo": _row_to_dict(memo) if memo else None,
            "idea": _row_to_dict(idea) if idea else None,
            "reviews": [_row_to_dict(r) for r in reviews],
        }


@app.get("/api/events")
def list_events(limit: int = 100) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Event).order_by(desc(Event.created_at)).limit(limit)).all()
        return [_row_to_dict(r) for r in rows]


@app.get("/api/approvals")
def list_approvals(status: str = "pending") -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(
            select(Approval).where(Approval.status == status).order_by(desc(Approval.created_at))
        ).all()
        return [_row_to_dict(r) for r in rows]


@app.post("/api/approvals/{approval_id}")
def decide_approval(approval_id: int, payload: ApprovalDecision) -> dict:
    with session_scope() as s:
        a = s.get(Approval, approval_id)
        if a is None:
            raise HTTPException(404, "approval not found")
        a.status = "approved" if payload.approve else "rejected"
        a.decided_by = payload.decided_by
        a.decided_at = datetime.now(timezone.utc)
        s.add(
            Event(
                kind="approval_decided",
                actor=payload.decided_by,
                message=f"Approval #{a.id} {a.status}: {a.action}",
                payload={"approval_id": a.id},
            )
        )
        return _row_to_dict(a)
