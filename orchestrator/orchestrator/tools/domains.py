from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, MoneyTransaction, SystemState, Task
from orchestrator.db.session import session_scope
from orchestrator.money import MAX_PER_ACTION_USD, MoneyCapExceeded, check_money_budget, record_money_spend

PORKBUN_BASE = "https://api.porkbun.com/api/json/v3"


class ToolUnavailable(Exception):
    pass


def _credentials() -> dict[str, str]:
    if not settings.porkbun_api_key or not settings.porkbun_api_secret:
        raise ToolUnavailable("Porkbun API credentials are not configured")
    return {"apikey": settings.porkbun_api_key, "secretapikey": settings.porkbun_api_secret}


def _estimate_price(payload: dict[str, Any]) -> float:
    for key in ("price", "registration_price", "registrationPrice"):
        value = payload.get(key)
        if value is not None:
            try:
                return float(str(value).replace("$", ""))
            except ValueError:
                pass
    prices = payload.get("prices") if isinstance(payload.get("prices"), dict) else {}
    for key in ("registration", "register", "price"):
        if key in prices:
            try:
                return float(str(prices[key]).replace("$", ""))
            except ValueError:
                pass
    return 12.0


def domain_check(name: str) -> dict:
    domain = name.strip().lower()
    if not domain or "." not in domain:
        raise ValueError("Domain name must include a TLD")
    payload = _credentials()
    resp = httpx.post(f"{PORKBUN_BASE}/domain/checkDomain/{domain}", json=payload, timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(f"Porkbun checkDomain returned HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    price = _estimate_price(data)
    available = str(data.get("avail") or data.get("available") or "").lower() in {"yes", "true", "1"}
    return {"name": domain, "available": available, "price_usd": price, "currency": "USD", "raw_status": data.get("status")}


def domain_register(name: str, years: int = 1) -> dict:
    domain = name.strip().lower()
    years = max(1, int(years or 1))
    estimated = 12.0
    if settings.porkbun_api_key and settings.porkbun_api_secret:
        try:
            estimated = float(domain_check(domain).get("price_usd") or estimated) * years
        except Exception:
            estimated = 12.0 * years
    if estimated > MAX_PER_ACTION_USD:
        raise MoneyCapExceeded(f"Estimated domain registration ${estimated:.2f} exceeds per-action cap ${MAX_PER_ACTION_USD:.2f}")
    with session_scope() as s:
        existing = s.scalars(select(Approval).where(Approval.action == "register_domain", Approval.status == "pending")).all()
        for approval in existing:
            payload = approval.payload or {}
            if payload.get("name") == domain and int(payload.get("years", 1)) == years:
                return {"approval_id": approval.id, "status": "pending_approval", "estimated_usd": payload.get("estimated_usd", estimated)}
        approval = Approval(
            requested_by="domain_tool",
            action="register_domain",
            payload={"name": domain, "years": years, "estimated_usd": estimated},
            rationale=f"Register {domain} for {years} year(s); estimated ${estimated:.2f}.",
            status="pending",
        )
        s.add(approval)
        s.flush()
        s.add(Event(kind="approval_requested", actor="domain_tool", message=f"Domain registration approval #{approval.id}: {domain}", payload={"approval_id": approval.id, "estimated_usd": estimated}))
        return {"approval_id": approval.id, "status": "pending_approval", "estimated_usd": estimated}


def _transaction_dict(tx: MoneyTransaction) -> dict:
    return {c.name: getattr(tx, c.name) for c in tx.__table__.columns}


def execute_domain_registration(approval_id: int) -> dict:
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        approval = s.get(Approval, approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        if approval.action != "register_domain":
            raise ValueError(f"Approval {approval_id} action is {approval.action}, expected register_domain")
        if approval.status != "approved":
            raise ValueError(f"Approval {approval_id} is {approval.status}, expected approved")
        existing = s.scalars(select(MoneyTransaction).where(MoneyTransaction.approval_id == approval_id)).first()
        if existing and existing.status in {"done", "simulated"}:
            _sync_tasks_for_approval(s, approval_id, existing.status)
            return _transaction_dict(existing)
        payload = approval.payload or {}
        domain = str(payload.get("name") or "").strip().lower()
        years = int(payload.get("years") or 1)
        amount = float(payload.get("estimated_usd") or 12.0)
        check_money_budget(s, amount)
        state = s.get(SystemState, 1)
        dry_run = bool(state.dry_run if state else settings.dry_run)
        tx = existing or MoneyTransaction(
            action="register_domain",
            amount_usd=amount,
            vendor="porkbun",
            idempotency_key=f"register:{domain}:{approval_id}",
            status="pending",
            approval_id=approval_id,
            result_json=None,
        )
        s.add(tx)
        s.flush()
        tx_id = tx.id

    result: dict[str, Any]
    status = "simulated"
    if dry_run or not (settings.porkbun_api_key and settings.porkbun_api_secret):
        result = {"dry_run": True, "domain": domain, "years": years, "message": "Simulated domain registration; no Porkbun call made."}
    else:
        body = {**_credentials(), "years": years}
        resp = httpx.post(f"{PORKBUN_BASE}/domain/create/{domain}", json=body, timeout=30)
        result = {"http_status": resp.status_code, "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:500]}
        if resp.status_code >= 400:
            status = "failed"
        else:
            status = "done"

    with session_scope() as s:
        tx = s.get(MoneyTransaction, tx_id)
        tx.status = status
        tx.result_json = result
        tx.completed_at = now
        if status == "done":
            record_money_spend(s, tx.amount_usd)
        _sync_tasks_for_approval(s, approval_id, status)
        s.add(Event(kind="money_transaction", actor="domain_tool", message=f"Domain registration {status}: {domain}", payload={"approval_id": approval_id, "transaction_id": tx.id, "amount_usd": tx.amount_usd, "status": status}))
        return _transaction_dict(tx)


def _sync_tasks_for_approval(s, approval_id: int, tx_status: str) -> None:
    tasks = s.scalars(select(Task).where(Task.approval_id == approval_id)).all()
    for task in tasks:
        if tx_status in {"done", "simulated"}:
            task.status = "done"
            task.completed_at = datetime.now(timezone.utc)
        elif tx_status == "failed":
            task.status = "skipped"
