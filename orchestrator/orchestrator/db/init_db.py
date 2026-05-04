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
]


def _run_column_migrations() -> None:
    with engine.begin() as conn:
        for table, column, ddl in _COLUMN_MIGRATIONS:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {ddl}'))
        conn.execute(text("UPDATE system_state SET money_daily_cap_usd = COALESCE(money_daily_cap_usd, :cap)"), {"cap": settings.money_daily_cap_usd})
        conn.execute(text("UPDATE system_state SET money_spend_today_usd = COALESCE(money_spend_today_usd, 0.0)"))
        conn.execute(text("UPDATE system_state SET money_spend_day = COALESCE(money_spend_day, now())"))


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
