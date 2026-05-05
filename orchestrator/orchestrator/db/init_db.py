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
]


def _run_column_migrations() -> None:
    with engine.begin() as conn:
        for table, column, ddl in _COLUMN_MIGRATIONS:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {ddl}'))
        conn.execute(text("UPDATE system_state SET money_daily_cap_usd = COALESCE(money_daily_cap_usd, :cap)"), {"cap": settings.money_daily_cap_usd})
        conn.execute(text("UPDATE system_state SET money_spend_today_usd = COALESCE(money_spend_today_usd, 0.0)"))
        conn.execute(text("UPDATE system_state SET money_spend_day = COALESCE(money_spend_day, now())"))


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
