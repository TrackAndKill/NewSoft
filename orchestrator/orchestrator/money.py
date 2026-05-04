from datetime import datetime, timezone

from sqlalchemy.orm import Session

from orchestrator.db.models import SystemState

MAX_PER_ACTION_USD = 25.0


class MoneyCapExceeded(Exception):
    pass


def _rollover_if_new_day(state: SystemState) -> None:
    today = datetime.now(timezone.utc).date()
    if state.money_spend_day.date() != today:
        state.money_spend_day = datetime.now(timezone.utc)
        state.money_spend_today_usd = 0.0


def check_money_budget(s: Session, amount_usd: float) -> SystemState:
    if amount_usd > MAX_PER_ACTION_USD:
        raise MoneyCapExceeded(f"Per-action cap ${MAX_PER_ACTION_USD:.2f} would be exceeded by ${amount_usd:.2f}.")
    state = s.get(SystemState, 1)
    if state is None:
        raise MoneyCapExceeded("System state missing.")
    _rollover_if_new_day(state)
    if state.money_spend_today_usd + amount_usd > state.money_daily_cap_usd:
        raise MoneyCapExceeded(
            f"Daily money cap ${state.money_daily_cap_usd:.2f} would be exceeded "
            f"(today: ${state.money_spend_today_usd:.2f}, requested: ${amount_usd:.2f})."
        )
    return state


def record_money_spend(s: Session, amount_usd: float) -> None:
    state = s.get(SystemState, 1)
    if state is None:
        return
    _rollover_if_new_day(state)
    state.money_spend_today_usd += amount_usd
