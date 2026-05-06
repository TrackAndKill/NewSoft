from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SystemState(Base):
    """Singleton row holding global flags (kill switch, dry-run, etc.)."""

    __tablename__ = "system_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    daily_spend_cap_usd: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    spend_today_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    spend_day: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    money_spend_today_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    money_daily_cap_usd: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    money_spend_day: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Idea(Base):
    __tablename__ = "ideas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rubric: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="candidate", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Memo(Base):
    __tablename__ = "memos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(40), default="hold")
    decision: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    idea: Mapped["Idea"] = relationship("Idea")


class BoardReview(Base):
    __tablename__ = "board_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("memos.id"), nullable=False)
    persona: Mapped[str] = mapped_column(String(60), nullable=False)
    vote: Mapped[str] = mapped_column(String(20), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Venture(Base):
    __tablename__ = "ventures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"), nullable=False)
    memo_id: Mapped[int] = mapped_column(ForeignKey("memos.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    charter: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="chartered", nullable=False)
    kill_criteria_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    killed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kill_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    daily_llm_cap_usd: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    daily_money_cap_usd: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    total_money_cap_usd: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    llm_spend_today_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    money_spend_today_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    money_spend_lifetime_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    spend_day: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venture_id: Mapped[int | None] = mapped_column(ForeignKey("ventures.id"), nullable=True)
    agent: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    input_messages: Mapped[list] = mapped_column(JSON, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    output_text: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requested_by: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    execute_live: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Experiment(Base):
    """Validator-designed experiment parent/container for staged validation."""

    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("memos.id"), nullable=False)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id"), nullable=True)
    design_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="designed", nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_md: Mapped[str] = mapped_column(Text, default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperimentStage(Base):
    __tablename__ = "experiment_stages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(40), nullable=False)
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="designed", nullable=False)
    design_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_md: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id"), nullable=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    real_money_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutreachSend(Base):
    __tablename__ = "outreach_sends"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    recipient_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    audit_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    complained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailSuppression(Base):
    __tablename__ = "email_suppressions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    recipient_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailDomainBlock(Base):
    __tablename__ = "email_domain_blocks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MemoryEmbedding(Base):
    __tablename__ = "memory_embeddings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_chunk: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Clarification(Base):
    __tablename__ = "clarifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asked_by_agent: Mapped[str] = mapped_column(String(80), nullable=False)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    venture_id: Mapped[int | None] = mapped_column(ForeignKey("ventures.id"), nullable=True)
    memo_id: Mapped[int | None] = mapped_column(ForeignKey("memos.id"), nullable=True)
    question_md: Mapped[str] = mapped_column(Text, nullable=False)
    context_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    answer_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MoneyTransaction(Base):
    __tablename__ = "money_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venture_id: Mapped[int | None] = mapped_column(ForeignKey("ventures.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vendor: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id"), nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Postmortem(Base):
    __tablename__ = "postmortems"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venture_id: Mapped[int] = mapped_column(ForeignKey("ventures.id"), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    lessons_md: Mapped[str] = mapped_column(Text, nullable=False)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venture_id: Mapped[int] = mapped_column(ForeignKey("ventures.id"), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, default="")
    plan_30: Mapped[str] = mapped_column(Text, default="")
    plan_60: Mapped[str] = mapped_column(Text, default="")
    plan_90: Mapped[str] = mapped_column(Text, default="")
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venture_id: Mapped[int] = mapped_column(ForeignKey("ventures.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    needs_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id"), nullable=True)
    approval_action: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approval_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venture_id: Mapped[int] = mapped_column(ForeignKey("ventures.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    deploy_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(60), default="staging", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SiteContent(Base):
    __tablename__ = "site_contents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venture_id: Mapped[int] = mapped_column(ForeignKey("ventures.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), default="landing_page", nullable=False)
    html: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(254), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="landing", nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    referer: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
