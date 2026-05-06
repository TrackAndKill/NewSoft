from sqlalchemy import text

from orchestrator.config import settings
from orchestrator.db.models import Base, SystemState
from orchestrator.db.session import SessionLocal, engine

_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("memos", "decision", "VARCHAR(40) NOT NULL DEFAULT 'pending'"),
    ("memos", "decision_at", "TIMESTAMP WITH TIME ZONE"),
    ("system_state", "money_spend_today_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0.0"),
    ("system_state", "money_daily_cap_usd", "DOUBLE PRECISION NOT NULL DEFAULT 50.0"),
    ("system_state", "money_spend_day", "TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()"),
    ("approvals", "execute_live", "BOOLEAN"),
    ("experiments", "current_stage", "VARCHAR(40)"),
    ("ventures", "kill_criteria_json", "JSON"),
    ("ventures", "killed_at", "TIMESTAMP WITH TIME ZONE"),
    ("ventures", "kill_reason", "TEXT"),
    ("outreach_sends", "recipient_domain", "VARCHAR(255)"),
    ("outreach_sends", "audit_payload", "JSON"),
    ("outreach_sends", "error", "TEXT"),
    ("outreach_sends", "delivered_at", "TIMESTAMP WITH TIME ZONE"),
    ("outreach_sends", "opened_at", "TIMESTAMP WITH TIME ZONE"),
    ("outreach_sends", "clicked_at", "TIMESTAMP WITH TIME ZONE"),
    ("outreach_sends", "bounced_at", "TIMESTAMP WITH TIME ZONE"),
    ("outreach_sends", "complained_at", "TIMESTAMP WITH TIME ZONE"),
    ("outreach_sends", "cancelled", "BOOLEAN NOT NULL DEFAULT false"),
    ("ventures", "daily_llm_cap_usd", "DOUBLE PRECISION NOT NULL DEFAULT 5.0"),
    ("ventures", "daily_money_cap_usd", "DOUBLE PRECISION NOT NULL DEFAULT 10.0"),
    ("ventures", "total_money_cap_usd", "DOUBLE PRECISION NOT NULL DEFAULT 50.0"),
    ("ventures", "llm_spend_today_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0.0"),
    ("ventures", "money_spend_today_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0.0"),
    ("ventures", "money_spend_lifetime_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0.0"),
    ("ventures", "spend_day", "TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()"),
    ("agent_runs", "venture_id", "INTEGER REFERENCES ventures(id)"),
    ("money_transactions", "venture_id", "INTEGER REFERENCES ventures(id)"),
]


def _run_column_migrations() -> None:
    with engine.begin() as conn:
        for table, column, ddl in _COLUMN_MIGRATIONS:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {ddl}'))
        conn.execute(text("UPDATE system_state SET money_daily_cap_usd = COALESCE(money_daily_cap_usd, :cap)"), {"cap": settings.money_daily_cap_usd})
        conn.execute(text("UPDATE system_state SET money_spend_today_usd = COALESCE(money_spend_today_usd, 0.0)"))
        conn.execute(text("UPDATE system_state SET money_spend_day = COALESCE(money_spend_day, now())"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_email_suppressions_domain ON email_suppressions (recipient_domain)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_email_domain_blocks_domain ON email_domain_blocks (domain)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_outreach_sends_experiment_domain ON outreach_sends (experiment_id, recipient_domain)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_venture_id ON agent_runs (venture_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_money_transactions_venture_id ON money_transactions (venture_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_clarifications_status ON clarifications (status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_clarifications_venture_id ON clarifications (venture_id)"))


def _run_vector_migrations() -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    id BIGSERIAL PRIMARY KEY,
                    source_kind VARCHAR(40) NOT NULL,
                    source_id INT NOT NULL,
                    chunk_index INT NOT NULL,
                    content_chunk TEXT NOT NULL,
                    embedding vector(1024) NOT NULL,
                    metadata JSONB,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (source_kind, source_id, chunk_index)
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS memory_embeddings_hnsw
                ON memory_embeddings USING hnsw (embedding vector_cosine_ops)
            """))
    except Exception:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    id BIGSERIAL PRIMARY KEY,
                    source_kind VARCHAR(40) NOT NULL,
                    source_id INT NOT NULL,
                    chunk_index INT NOT NULL,
                    content_chunk TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    metadata JSONB,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (source_kind, source_id, chunk_index)
                )
            """))


def init_db() -> None:
    _run_vector_migrations()
    Base.metadata.create_all(engine)
    _run_column_migrations()
    with SessionLocal() as s:
        state = s.get(SystemState, 1)
        if state is None:
            s.add(
                SystemState(
                    id=1,
                    active=settings.system_active,
                    dry_run=settings.dry_run,
                    daily_spend_cap_usd=settings.daily_spend_cap_usd,
                    money_daily_cap_usd=settings.money_daily_cap_usd,
                )
            )
            s.commit()
        else:
            state.money_daily_cap_usd = settings.money_daily_cap_usd or state.money_daily_cap_usd or 50.0
            s.commit()


if __name__ == "__main__":
    init_db()
    print("DB initialized.")
