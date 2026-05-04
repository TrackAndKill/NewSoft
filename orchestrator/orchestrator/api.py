from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib
import re

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from orchestrator.agents.discovery import run_discovery
from orchestrator.agents.validator import run_experiment
from orchestrator.db.init_db import init_db
from orchestrator.db.models import (
    AgentRun, Approval, BoardReview, Event, Experiment, Goal, Idea, Lead, Memo,
    MoneyTransaction, Plan, Postmortem, Site, SiteContent, SystemState, Task, ToolCall, Venture,
)
from orchestrator.db.session import session_scope
from orchestrator.digest import send_digest
from orchestrator.money import MAX_PER_ACTION_USD
from orchestrator.config import settings
from orchestrator.rituals.scheduler import board_tick, discovery_tick, kill_loop_tick, site_tick, start_scheduler, validator_tick, venture_tick
from orchestrator.tools.domains import execute_domain_registration
from orchestrator.tools.sites import execute_site_approval
from orchestrator.agents.postmortem_writer import write_postmortem

_rate_hits: dict[tuple[int, str], list[datetime]] = {}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(); start_scheduler(); yield

app = FastAPI(title="NewSoft Orchestrator", version="0.5.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class GoalIn(BaseModel):
    title: str
    description: str = ""
class ApprovalDecision(BaseModel):
    decided_by: str = "founder"
    approve: bool
    execute_live: bool | None = None
class SystemUpdate(BaseModel):
    active: bool | None = None
    dry_run: bool | None = None
    daily_spend_cap_usd: float | None = None
    money_daily_cap_usd: float | None = None
class SignupIn(BaseModel):
    email: str
    source: str = "landing"

def _row_to_dict(row: Any) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
def _empty_cost_bucket() -> dict:
    return {"total_usd": 0.0, "by_agent": {}}

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _ip_hash(ip: str) -> str:
    return hashlib.sha256(f"{settings.lead_ip_hash_pepper}:{ip}".encode()).hexdigest()

@app.get("/api/status")
def status() -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        return _row_to_dict(state) if state else {}

@app.get("/api/costs")
def costs() -> dict:
    today = datetime.now(timezone.utc).date(); start = datetime.combine(today - timedelta(days=6), datetime.min.time(), tzinfo=timezone.utc)
    buckets = {"today": _empty_cost_bucket(), "yesterday": _empty_cost_bucket(), "last_7d": _empty_cost_bucket()}
    day_bucket = func.date_trunc("day", AgentRun.started_at).label("day_bucket")
    with session_scope() as s:
        rows = s.execute(select(day_bucket, AgentRun.agent, func.sum(AgentRun.cost_usd)).where(AgentRun.started_at >= start).group_by(day_bucket, AgentRun.agent)).all()
    for day_dt, agent, total in rows:
        if day_dt is None: continue
        day = day_dt.date() if hasattr(day_dt, "date") else day_dt; amount = float(total or 0)
        buckets["last_7d"]["total_usd"] += amount; buckets["last_7d"]["by_agent"][agent] = buckets["last_7d"]["by_agent"].get(agent, 0.0) + amount
        key = "today" if day == today else "yesterday" if day == today - timedelta(days=1) else None
        if key:
            buckets[key]["total_usd"] += amount; buckets[key]["by_agent"][agent] = buckets[key]["by_agent"].get(agent, 0.0) + amount
    for bucket in buckets.values():
        bucket["total_usd"] = round(bucket["total_usd"], 6); bucket["by_agent"] = {k: round(v, 6) for k, v in sorted(bucket["by_agent"].items())}
    return buckets

@app.post("/api/system")
def update_system(payload: SystemUpdate) -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        if state is None: raise HTTPException(404, "system_state missing")
        if payload.active is not None:
            state.active = payload.active; s.add(Event(kind="system", actor="founder", message=f"System {'activated' if payload.active else 'HALTED'}"))
        if payload.dry_run is not None: state.dry_run = payload.dry_run
        if payload.daily_spend_cap_usd is not None: state.daily_spend_cap_usd = payload.daily_spend_cap_usd
        if payload.money_daily_cap_usd is not None: state.money_daily_cap_usd = payload.money_daily_cap_usd
        return _row_to_dict(state)

@app.post("/api/system/kill")
def kill_switch() -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        if state is None: raise HTTPException(404, "system_state missing")
        state.active = False; s.add(Event(kind="system", actor="founder", message="KILL SWITCH engaged")); return _row_to_dict(state)

@app.get("/api/goals")
def list_goals() -> list[dict]:
    with session_scope() as s: return [_row_to_dict(r) for r in s.scalars(select(Goal).order_by(desc(Goal.created_at))).all()]
@app.post("/api/goals")
def create_goal(payload: GoalIn) -> dict:
    with session_scope() as s:
        g = Goal(title=payload.title, description=payload.description, status="active"); s.add(g); s.flush(); s.add(Event(kind="goal_created", actor="founder", message=f"Goal: {g.title}", payload={"goal_id": g.id})); return _row_to_dict(g)
@app.post("/api/discovery/run")
def trigger_discovery(background: BackgroundTasks) -> dict:
    background.add_task(discovery_tick); return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}
@app.post("/api/board/run")
def trigger_board(background: BackgroundTasks) -> dict:
    background.add_task(board_tick); return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}
@app.post("/api/validator/run")
def trigger_validator(background: BackgroundTasks) -> dict:
    background.add_task(validator_tick); return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}
@app.post("/api/venture/run")
def trigger_venture(background: BackgroundTasks) -> dict:
    background.add_task(venture_tick); return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}
@app.post("/api/site/run")
def trigger_site(background: BackgroundTasks) -> dict:
    background.add_task(site_tick); return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}
@app.post("/api/digest/run")
def trigger_digest(background: BackgroundTasks) -> dict:
    background.add_task(send_digest); return {"queued": True, "ts": datetime.now(timezone.utc).isoformat()}
@app.post("/api/kill_loop/run")
def trigger_kill_loop() -> dict:
    return kill_loop_tick()

@app.get("/api/ideas")
def list_ideas(limit: int = 50) -> list[dict]:
    with session_scope() as s: return [_row_to_dict(r) for r in s.scalars(select(Idea).order_by(desc(Idea.created_at)).limit(limit)).all()]
@app.get("/api/memos")
def list_memos(limit: int = 20) -> list[dict]:
    with session_scope() as s: return [_row_to_dict(r) for r in s.scalars(select(Memo).order_by(desc(Memo.created_at)).limit(limit)).all()]
@app.get("/api/board/reviews")
def list_board_reviews(limit: int = 100, memo_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(BoardReview).order_by(desc(BoardReview.created_at)).limit(limit)
        if memo_id is not None: q = select(BoardReview).where(BoardReview.memo_id == memo_id).order_by(BoardReview.created_at.asc())
        return [_row_to_dict(r) for r in s.scalars(q).all()]

@app.get("/api/ventures")
def list_ventures() -> list[dict]:
    with session_scope() as s: return [_row_to_dict(r) for r in s.scalars(select(Venture).order_by(desc(Venture.created_at))).all()]
@app.get("/api/ventures/{slug}")
def get_venture(slug: str) -> dict:
    with session_scope() as s:
        v = s.scalar(select(Venture).where(Venture.slug == slug))
        if v is None: raise HTTPException(404, "venture not found")
        memo = s.get(Memo, v.memo_id); idea = s.get(Idea, v.idea_id)
        reviews = s.scalars(select(BoardReview).where(BoardReview.memo_id == v.memo_id).order_by(BoardReview.created_at.asc())).all()
        plan = s.scalars(select(Plan).where(Plan.venture_id == v.id).order_by(desc(Plan.created_at))).first()
        tasks = s.scalars(select(Task).where(Task.venture_id == v.id).order_by(Task.created_at.asc())).all()
        site = s.scalars(select(Site).where(Site.venture_id == v.id).order_by(desc(Site.created_at))).first()
        postmortem = s.scalars(select(Postmortem).where(Postmortem.venture_id == v.id).order_by(desc(Postmortem.created_at))).first()
        return {"venture": _row_to_dict(v), "memo": _row_to_dict(memo) if memo else None, "idea": _row_to_dict(idea) if idea else None, "reviews": [_row_to_dict(r) for r in reviews], "plan": _row_to_dict(plan) if plan else None, "tasks": [_row_to_dict(t) for t in tasks], "site": _row_to_dict(site) if site else None, "postmortem": _row_to_dict(postmortem) if postmortem else None}
@app.get("/api/ventures/{slug}/plan")
def get_venture_plan(slug: str) -> dict:
    data = get_venture(slug); return {"venture": data["venture"], "plan": data.get("plan"), "tasks": data.get("tasks", [])}
@app.get("/api/ventures/{slug}/postmortem")
def get_venture_postmortem(slug: str) -> dict:
    data = get_venture(slug)
    if not data.get("postmortem"):
        raise HTTPException(404, "postmortem not found")
    return {"venture": data["venture"], "postmortem": data["postmortem"]}
@app.post("/api/ventures/{slug}/postmortem")
def create_venture_postmortem(slug: str) -> dict:
    with session_scope() as s:
        venture = s.scalar(select(Venture).where(Venture.slug == slug))
        if venture is None: raise HTTPException(404, "venture not found")
        venture_id = venture.id
        reason = venture.kill_reason or "Manual postmortem requested by operator."
    postmortem_id = write_postmortem(venture_id, reason)
    with session_scope() as s:
        row = s.get(Postmortem, postmortem_id)
        return _row_to_dict(row)
@app.get("/api/postmortems")
def list_postmortems() -> list[dict]:
    with session_scope() as s:
        rows = s.execute(select(Postmortem, Venture).join(Venture, Venture.id == Postmortem.venture_id).order_by(desc(Postmortem.created_at))).all()
        out=[]
        for pm, v in rows:
            data = _row_to_dict(pm); data["venture"] = _row_to_dict(v); out.append(data)
        return out

@app.get("/api/sites")
def list_sites() -> list[dict]:
    with session_scope() as s:
        rows = s.execute(select(Site, func.count(Lead.id)).outerjoin(Lead, Lead.site_id == Site.id).group_by(Site.id).order_by(desc(Site.created_at))).all()
        out=[]
        for site, count in rows:
            data=_row_to_dict(site); data["lead_count"] = int(count or 0); out.append(data)
        return out
@app.get("/api/sites/{slug}")
def get_site(slug: str) -> dict:
    with session_scope() as s:
        site = s.scalar(select(Site).where(Site.slug == slug))
        if site is None: raise HTTPException(404, "site not found")
        venture = s.get(Venture, site.venture_id)
        content = s.scalars(select(SiteContent).where(SiteContent.venture_id == site.venture_id).order_by(desc(SiteContent.created_at))).first()
        leads = s.scalars(select(Lead).where(Lead.site_id == site.id).order_by(desc(Lead.created_at)).limit(100)).all()
        return {"site": _row_to_dict(site), "venture": _row_to_dict(venture) if venture else None, "content": _row_to_dict(content) if content else None, "leads": [_row_to_dict(l) for l in leads]}
@app.get("/api/sites/{slug}/leads")
def get_site_leads(slug: str) -> list[dict]:
    return get_site(slug)["leads"]

@app.post("/api/public/sites/{slug}/signup")
def public_signup(slug: str, payload: SignupIn, request: Request) -> dict:
    email = payload.email.strip().lower()
    if not EMAIL_RE.match(email): raise HTTPException(400, "invalid email")
    source = (payload.source or "landing")[:80]
    ip_hash = _ip_hash(_client_ip(request))
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        site = s.scalar(select(Site).where(Site.slug == slug))
        if site is None: raise HTTPException(404, "site not found")
        key = (site.id, ip_hash)
        hits = [t for t in _rate_hits.get(key, []) if t > now - timedelta(minutes=10)]
        if len(hits) >= 5:
            raise HTTPException(429, "rate limited")
        hits.append(now); _rate_hits[key] = hits
        lead = Lead(site_id=site.id, email=email, source=source, ip_hash=ip_hash, user_agent=(request.headers.get("user-agent") or "")[:500], referer=(request.headers.get("referer") or None))
        s.add(lead); s.flush(); s.add(Event(kind="lead_captured", actor="public", message=f"Lead captured for site {site.slug}", payload={"site_id": site.id, "lead_id": lead.id, "source": source}))
        return {"ok": True}

@app.get("/api/events")
def list_events(limit: int = 100) -> list[dict]:
    with session_scope() as s: return [_row_to_dict(r) for r in s.scalars(select(Event).order_by(desc(Event.created_at)).limit(limit)).all()]
@app.get("/api/agent_runs/{run_id}")
def get_agent_run(run_id: int) -> dict:
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        if run is None: raise HTTPException(404, "agent run not found")
        data = _row_to_dict(run); tool_rows = s.scalars(select(ToolCall).where(ToolCall.agent_run_id == run_id).order_by(ToolCall.created_at.asc())).all(); data["tool_call_rows"] = [_row_to_dict(r) for r in tool_rows]; return data
@app.get("/api/experiments")
def list_experiments(limit: int = 100, memo_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(Experiment).order_by(desc(Experiment.created_at)).limit(limit)
        if memo_id is not None: q = select(Experiment).where(Experiment.memo_id == memo_id).order_by(desc(Experiment.created_at))
        return [_row_to_dict(r) for r in s.scalars(q).all()]
@app.get("/api/approvals")
def list_approvals(status: str = "pending") -> list[dict]:
    with session_scope() as s: return [_row_to_dict(r) for r in s.scalars(select(Approval).where(Approval.status == status).order_by(desc(Approval.created_at))).all()]

@app.post("/api/approvals/{approval_id}")
def decide_approval(approval_id: int, payload: ApprovalDecision, background: BackgroundTasks) -> dict:
    should_run_experiment = should_register_domain = should_site_action = should_kill_venture = False
    force_live = payload.execute_live if payload.approve else None
    with session_scope() as s:
        a = s.get(Approval, approval_id)
        if a is None: raise HTTPException(404, "approval not found")
        if a.status in {"approved", "rejected"}:
            return _row_to_dict(a)
        a.status = "approved" if payload.approve else "rejected"
        a.execute_live = force_live
        a.decided_by = payload.decided_by
        a.decided_at = datetime.now(timezone.utc)
        should_run_experiment = a.status == "approved" and a.action == "run_experiment"
        should_register_domain = a.status == "approved" and a.action == "register_domain"
        should_site_action = a.status == "approved" and a.action in {"configure_dns", "deploy_landing_page", "teardown_site"}
        should_kill_venture = a.status == "approved" and a.action == "kill_venture"
        execute_mode = "live" if force_live is True else "simulated" if force_live is False else "system"
        s.add(Event(kind="approval_decided", actor=payload.decided_by, message=f"Approval #{a.id} {a.status}: {a.action}", payload={"approval_id": a.id, "execute_mode": execute_mode}))
        data = _row_to_dict(a)
    if should_run_experiment: background.add_task(run_experiment, approval_id)
    if should_register_domain: background.add_task(execute_domain_registration, approval_id, force_live)
    if should_site_action: background.add_task(execute_site_approval, approval_id, force_live)
    if should_kill_venture: background.add_task(execute_kill_venture_approval, approval_id)
    return data


def execute_kill_venture_approval(approval_id: int) -> dict:
    with session_scope() as s:
        approval = s.get(Approval, approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        if approval.status != "approved" or approval.action != "kill_venture":
            raise ValueError(f"Approval {approval_id} must be approved kill_venture")
        payload = approval.payload or {}
        venture_id = int(payload.get("venture_id") or 0)
        rationale = str(payload.get("rationale") or approval.rationale or "Kill venture approved by operator.")
        venture = s.get(Venture, venture_id)
        if venture is None:
            raise ValueError(f"Venture {venture_id} not found")
    postmortem_id = write_postmortem(venture_id, rationale)
    with session_scope() as s:
        venture = s.get(Venture, venture_id)
        venture.status = "killed"
        venture.killed_at = datetime.now(timezone.utc)
        venture.kill_reason = rationale
        tasks = s.scalars(select(Task).where(Task.approval_id == approval_id)).all()
        for task in tasks:
            task.status = "done"
            task.completed_at = datetime.now(timezone.utc)
        s.add(Event(kind="venture_killed", actor="approval_dispatcher", message=f"Venture #{venture_id} killed after approval #{approval_id}; postmortem #{postmortem_id}", payload={"venture_id": venture_id, "approval_id": approval_id, "postmortem_id": postmortem_id}))
        return {"venture_id": venture_id, "postmortem_id": postmortem_id, "status": "killed"}

@app.get("/api/money/status")
def money_status() -> dict:
    with session_scope() as s:
        state = s.get(SystemState, 1)
        return {"spend_today_usd": float(getattr(state, "money_spend_today_usd", 0.0) if state else 0.0), "daily_cap_usd": float(getattr(state, "money_daily_cap_usd", 50.0) if state else 50.0), "per_action_cap_usd": MAX_PER_ACTION_USD, "dry_run": bool(state.dry_run if state else True)}
@app.get("/api/money/transactions")
def money_transactions(limit: int = 50) -> list[dict]:
    with session_scope() as s: return [_row_to_dict(r) for r in s.scalars(select(MoneyTransaction).order_by(desc(MoneyTransaction.created_at)).limit(limit)).all()]
