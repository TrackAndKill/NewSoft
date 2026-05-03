from sqlalchemy import text

from orchestrator.config import settings
from orchestrator.db.models import Base, SystemState
from orchestrator.db.session import SessionLocal, engine

_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("memos", "decision", "VARCHAR(40) NOT NULL DEFAULT 'pending'"),
    ("memos", "decision_at", "TIMESTAMP WITH TIME ZONE"),
]


def _run_column_migrations() -> None:
    with engine.begin() as conn:
        for table, column, ddl in _COLUMN_MIGRATIONS:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {ddl}'))


def init_db() -> None:
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
                )
            )
            s.commit()


if __name__ == "__main__":
    init_db()
    print("DB initialized.")
