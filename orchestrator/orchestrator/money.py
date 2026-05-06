from datetime import datetime, timezone

from sqlalchemy.orm import Session

from orchestrator.budget import reserve_venture_money, record_venture_money_spend
from orchestrator.db.models import SystemState

MAX_PER_ACTION_USD = 25.0


class MoneyCapExceeded(Exception):
    pass


def _rollover_if_new_day(state: SystemState) -> None:
    today = datetime.now(timezone.utc).date()
    if state.money_spend_day.date() != today:
        state.money_spend_day = datetime.now(timezone.utc)
        state.money_spend_today_usd = 0.0


def check_money_budget(s: Session, amount_usd: float, venture_id: int | None = None) -> SystemState:
    if amount_usd > MAX_PER_ACTION_USD:
        raise MoneyCapExceeded(f"Per-action cap ${MAX_PER_ACTION_USD:.2f} would be exceeded by ${amount_usd:.2f}.")
    state = s.get(SystemState, 1)
    if state is None:
        raise MoneyCapExceeded("System state missing.")
    _rollover_if_new_day(state)
    reserve_venture_money(s, venture_id, amount_usd)
    if state.money_spend_today_usd + amount_usd > state.money_daily_cap_usd:
        raise MoneyCapExceeded(
            f"Daily money cap ${state.money_daily_cap_usd:.2f} would be exceeded "
            f"(today: ${state.money_spend_today_usd:.2f}, requested: ${amount_usd:.2f})."
        )
    return state


def record_money_spend(s: Session, amount_usd: float, venture_id: int | None = None) -> None:
    state = s.get(SystemState, 1)
    if state is None:
        return
    _rollover_if_new_day(state)
    state.money_spend_today_usd += amount_usd
    record_venture_money_spend(s, venture_id, amount_usd)
