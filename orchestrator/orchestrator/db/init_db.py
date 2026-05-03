from orchestrator.config import settings
from orchestrator.db.models import Base, SystemState
from orchestrator.db.session import SessionLocal, engine


def init_db() -> None:
    Base.metadata.create_all(engine)
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
