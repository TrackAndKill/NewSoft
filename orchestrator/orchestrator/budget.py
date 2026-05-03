from datetime import datetime, timezone

from sqlalchemy.orm import Session

from orchestrator.db.models import SystemState


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
