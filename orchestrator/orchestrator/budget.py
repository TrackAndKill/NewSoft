from datetime import datetime, timezone

from sqlalchemy.orm import Session

from orchestrator.db.models import SystemState, Venture


# Per-million-token USD prices. Update when Anthropic pricing changes.
MODEL_PRICES = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


class BudgetExceeded(Exception):
    pass


class SystemHalted(Exception):
    pass


class VentureBudgetExceeded(BudgetExceeded):
    pass


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_per_m, out_per_m = MODEL_PRICES.get(model, (3.0, 15.0))
    return (input_tokens / 1_000_000) * in_per_m + (output_tokens / 1_000_000) * out_per_m


def _rollover_if_new_day(state: SystemState) -> None:
    today = datetime.now(timezone.utc).date()
    if state.spend_day.date() != today:
        state.spend_day = datetime.now(timezone.utc)
        state.spend_today_usd = 0.0


def check_active(s: Session) -> SystemState:
    state = s.get(SystemState, 1)
    if state is None or not state.active:
        raise SystemHalted("System is not active (kill switch engaged).")
    _rollover_if_new_day(state)
    return state


def reserve_budget(s: Session, estimated_usd: float) -> SystemState:
    state = check_active(s)
    if state.spend_today_usd + estimated_usd > state.daily_spend_cap_usd:
        raise BudgetExceeded(
            f"Daily cap ${state.daily_spend_cap_usd:.2f} would be exceeded "
            f"(today: ${state.spend_today_usd:.4f}, requested: ${estimated_usd:.4f})."
        )
    return state


def record_spend(s: Session, actual_usd: float) -> None:
    state = s.get(SystemState, 1)
    if state is None:
        return
    _rollover_if_new_day(state)
    state.spend_today_usd += actual_usd


def _rollover_venture_if_new_day(venture: Venture) -> None:
    today = datetime.now(timezone.utc).date()
    spend_day = venture.spend_day
    if spend_day.tzinfo is None:
        spend_day = spend_day.replace(tzinfo=timezone.utc)
    if spend_day.date() != today:
        venture.spend_day = datetime.now(timezone.utc)
        venture.llm_spend_today_usd = 0.0
        venture.money_spend_today_usd = 0.0


def reserve_venture_llm(s: Session, venture_id: int | None, estimated_usd: float) -> Venture | None:
    if venture_id is None:
        return None
    venture = s.get(Venture, int(venture_id))
    if venture is None:
        return None
    _rollover_venture_if_new_day(venture)
    if venture.llm_spend_today_usd + estimated_usd > venture.daily_llm_cap_usd:
        raise VentureBudgetExceeded(
            f"Venture {venture.slug}: daily LLM cap ${venture.daily_llm_cap_usd:.2f} would be exceeded "
            f"(today: ${venture.llm_spend_today_usd:.4f}, requested: ${estimated_usd:.4f})."
        )
    return venture


def record_venture_llm_spend(s: Session, venture_id: int | None, actual_usd: float) -> None:
    if venture_id is None:
        return
    venture = s.get(Venture, int(venture_id))
    if venture is None:
        return
    _rollover_venture_if_new_day(venture)
    venture.llm_spend_today_usd += actual_usd


def reserve_venture_money(s: Session, venture_id: int | None, amount_usd: float) -> Venture | None:
    if venture_id is None:
        return None
    venture = s.get(Venture, int(venture_id))
    if venture is None:
        return None
    _rollover_venture_if_new_day(venture)
    if venture.money_spend_today_usd + amount_usd > venture.daily_money_cap_usd:
        raise VentureBudgetExceeded(
            f"Venture {venture.slug}: daily money cap ${venture.daily_money_cap_usd:.2f} would be exceeded "
            f"(today: ${venture.money_spend_today_usd:.2f}, requested: ${amount_usd:.2f})."
        )
    if venture.money_spend_lifetime_usd + amount_usd > venture.total_money_cap_usd:
        raise VentureBudgetExceeded(
            f"Venture {venture.slug}: lifetime money cap ${venture.total_money_cap_usd:.2f} would be exceeded "
            f"(lifetime: ${venture.money_spend_lifetime_usd:.2f}, requested: ${amount_usd:.2f})."
        )
    return venture


def record_venture_money_spend(s: Session, venture_id: int | None, amount_usd: float) -> None:
    if venture_id is None:
        return
    venture = s.get(Venture, int(venture_id))
    if venture is None:
        return
    _rollover_venture_if_new_day(venture)
    venture.money_spend_today_usd += amount_usd
    venture.money_spend_lifetime_usd += amount_usd
