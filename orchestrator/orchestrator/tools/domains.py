from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import Approval, Event, MoneyTransaction, Site, SystemState, Task
from orchestrator.db.session import session_scope
from orchestrator.money import MAX_PER_ACTION_USD, MoneyCapExceeded, check_money_budget, record_money_spend

PORKBUN_BASE = "https://api.porkbun.com/api/json/v3"


class ToolUnavailable(Exception):
    pass


def _credentials() -> dict[str, str]:
    if not settings.porkbun_api_key or not settings.porkbun_api_secret:
        raise ToolUnavailable("Porkbun API credentials are not configured")
    return {"apikey": settings.porkbun_api_key, "secretapikey": settings.porkbun_api_secret}


def _post(path: str, payload: dict[str, Any] | None = None) -> dict:
    body = {**_credentials(), **(payload or {})}
    resp = httpx.post(f"{PORKBUN_BASE}{path}", json=body, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Porkbun returned HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if str(data.get("status", "SUCCESS")).upper() not in {"SUCCESS", "OK"}:
        raise RuntimeError(f"Porkbun status {data.get('status')}: {str(data)[:200]}")
    return data


def _domain_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Porkbun wraps checkDomain fields under response on the live API."""
    response = payload.get("response")
    return response if isinstance(response, dict) else payload


def _estimate_price(payload: dict[str, Any]) -> float:
    data = _domain_response(payload)
    for key in ("price", "registration_price", "registrationPrice", "regularPrice"):
        value = data.get(key)
        if value is not None:
            try:
                return float(str(value).replace("$", ""))
            except ValueError:
                pass
    prices = data.get("prices") if isinstance(data.get("prices"), dict) else {}
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
    data = _post(f"/domain/checkDomain/{domain}")
    response = _domain_response(data)
    price = _estimate_price(data)
    available = str(response.get("avail") or response.get("available") or "").lower() in {"yes", "true", "1"}
    return {
        "name": domain,
        "available": available,
        "price_usd": price,
        "cost_pennies": int(round(price * 100)),
        "currency": "USD",
        "raw_status": data.get("status"),
        "min_duration": response.get("minDuration"),
    }


def list_dns_records(domain: str) -> list[dict]:
    data = _post(f"/dns/retrieve/{domain.strip().lower()}")
    return list(data.get("records") or [])


def add_dns_record(domain: str, *, name: str, type: str, content: str, ttl: int = 600) -> dict:
    payload = {"name": name, "type": type.upper(), "content": content, "ttl": str(int(ttl or 600))}
    data = _post(f"/dns/create/{domain.strip().lower()}", payload)
    return {"domain": domain.strip().lower(), "record": payload, "porkbun": data}


def delete_dns_record(domain: str, record_id: str) -> dict:
    data = _post(f"/dns/delete/{domain.strip().lower()}/{record_id}")
    return {"domain": domain.strip().lower(), "record_id": str(record_id), "porkbun": data}


def domain_register(name: str, years: int = 1) -> dict:
    domain = name.strip().lower()
    years = max(1, int(years or 1))
    estimated = 12.0
    cost_pennies = None
    if settings.porkbun_api_key and settings.porkbun_api_secret:
        check = domain_check(domain)
        if not check.get("available"):
            raise RuntimeError(f"Domain {domain} is not available")
        estimated = float(check.get("price_usd") or estimated)
        cost_pennies = int(check.get("cost_pennies") or round(estimated * 100))
    amount = estimated * years
    if amount > MAX_PER_ACTION_USD:
        raise MoneyCapExceeded(f"Estimated domain registration ${amount:.2f} exceeds per-action cap ${MAX_PER_ACTION_USD:.2f}")
    with session_scope() as s:
        existing = s.scalars(select(Approval).where(Approval.action == "register_domain", Approval.status == "pending")).all()
        for approval in existing:
            payload = approval.payload or {}
            if payload.get("name") == domain and int(payload.get("years", 1)) == years:
                return {"approval_id": approval.id, "status": "pending_approval", "estimated_usd": payload.get("estimated_usd", amount)}
        payload = {"name": domain, "years": years, "estimated_usd": amount}
        if cost_pennies is not None:
            payload["cost_pennies"] = cost_pennies
        approval = Approval(requested_by="domain_tool", action="register_domain", payload=payload, rationale=f"Register {domain} for {years} year(s); estimated ${amount:.2f}.", status="pending")
        s.add(approval); s.flush()
        s.add(Event(kind="approval_requested", actor="domain_tool", message=f"Domain registration approval #{approval.id}: {domain}", payload={"approval_id": approval.id, "estimated_usd": amount}))
        return {"approval_id": approval.id, "status": "pending_approval", "estimated_usd": amount}


def _transaction_dict(tx: MoneyTransaction) -> dict:
    return {c.name: getattr(tx, c.name) for c in tx.__table__.columns}


def _venture_id_from_payload(s, payload: dict[str, Any]) -> int | None:
    venture_id = payload.get("venture_id")
    if venture_id is not None:
        try:
            return int(venture_id)
        except (TypeError, ValueError):
            return None
    site_id = payload.get("site_id")
    if site_id is not None:
        try:
            site = s.get(Site, int(site_id))
            return site.venture_id if site else None
        except (TypeError, ValueError):
            return None
    domain = str(payload.get("name") or payload.get("domain") or "").strip().lower()
    if domain:
        site = s.scalars(select(Site).where(Site.domain == domain)).first()
        return site.venture_id if site else None
    return None


def _resolve_dry_run(state: SystemState | None, force_live: bool | None = None) -> bool:
    if force_live is True:
        return False
    if force_live is False:
        return True
    return bool(state.dry_run if state else settings.dry_run)


def execute_domain_registration(approval_id: int, force_live: bool | None = None) -> dict:
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
        cost_pennies = int(payload.get("cost_pennies") or round(amount * 100))
        venture_id = _venture_id_from_payload(s, payload)
        check_money_budget(s, amount, venture_id=venture_id)
        state = s.get(SystemState, 1)
        dry_run = _resolve_dry_run(state, force_live)
        execute_mode = "live" if not dry_run else "simulated"
        tx = existing or MoneyTransaction(venture_id=venture_id, action="register_domain", amount_usd=amount, vendor="porkbun", idempotency_key=f"register:{domain}:{approval_id}", status="pending", approval_id=approval_id, result_json=None)
        s.add(tx); s.flush(); tx_id = tx.id

    result: dict[str, Any]
    status = "simulated"
    try:
        if dry_run or not (settings.porkbun_api_key and settings.porkbun_api_secret):
            result = {"dry_run": True, "execute_mode": execute_mode, "domain": domain, "years": years, "message": "Simulated domain registration; no Porkbun call made."}
        else:
            data = _post(f"/domain/create/{domain}", {"cost": cost_pennies, "agreeToTerms": "yes"})
            result = {"dry_run": False, "execute_mode": execute_mode, "body": data}
            status = "done"
    except Exception as exc:
        result = {"error": str(exc)[:500], "execute_mode": execute_mode, "domain": domain, "years": years}
        status = "failed"

    with session_scope() as s:
        tx = s.get(MoneyTransaction, tx_id)
        tx.status = status
        tx.result_json = result
        tx.completed_at = now
        if status == "done":
            record_money_spend(s, tx.amount_usd, venture_id=tx.venture_id)
        _sync_tasks_for_approval(s, approval_id, status)
        s.add(Event(kind="money_transaction", actor="domain_tool", message=f"Domain registration {status}: {domain}", payload={"approval_id": approval_id, "transaction_id": tx.id, "amount_usd": tx.amount_usd, "status": status, "execute_mode": execute_mode}))
        return _transaction_dict(tx)


def _sync_tasks_for_approval(s, approval_id: int, tx_status: str) -> None:
    tasks = s.scalars(select(Task).where(Task.approval_id == approval_id)).all()
    for task in tasks:
        if tx_status in {"done", "simulated"}:
            task.status = "done"
            task.completed_at = datetime.now(timezone.utc)
        elif tx_status == "failed":
            task.status = "skipped"
