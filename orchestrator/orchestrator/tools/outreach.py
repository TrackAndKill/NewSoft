from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select

from orchestrator.config import settings
from orchestrator.db.models import MoneyTransaction, OutreachSend
from orchestrator.db.session import session_scope

MAX_PER_EXPERIMENT = 100
MAX_PER_DAY = 50

def _recipient_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()

def send_email(to: str, subject: str, body: str, *, experiment_id: int, dry_run: bool = True) -> dict:
    to = to.strip().lower()
    if not to or "@" not in to:
        raise ValueError("valid recipient email required")
    if "unsubscribe" not in body.lower():
        raise ValueError("operator-supplied unsubscribe footer required")
    rh = _recipient_hash(to)
    today = datetime.now(timezone.utc).date()
    with session_scope() as s:
        existing = s.scalars(select(OutreachSend).where(OutreachSend.experiment_id == experiment_id, OutreachSend.recipient_hash == rh)).first()
        if existing:
            return {"status": existing.status, "idempotent": True, "outreach_send_id": existing.id}
        exp_count = s.scalar(select(func.count(OutreachSend.id)).where(OutreachSend.experiment_id == experiment_id)) or 0
        if exp_count >= MAX_PER_EXPERIMENT:
            raise RuntimeError("per-experiment outreach cap reached")
        day_count = s.scalar(select(func.count(OutreachSend.id)).where(func.date(OutreachSend.created_at) == today)) or 0
        if day_count >= MAX_PER_DAY:
            raise RuntimeError("daily outreach cap reached")
        row = OutreachSend(experiment_id=experiment_id, recipient_hash=rh, subject=subject[:300], status="dry_run" if dry_run else "queued", dry_run=dry_run)
        s.add(row); s.flush()
        tx = MoneyTransaction(action="outreach_send_email", amount_usd=0.0, vendor="resend", idempotency_key=f"outreach:{experiment_id}:{rh}", status="simulated" if dry_run else "pending", result_json={"outreach_send_id": row.id})
        s.add(tx)
        send_id = row.id
    if dry_run:
        return {"status": "dry_run", "outreach_send_id": send_id}
    if not settings.resend_api_key:
        with session_scope() as s:
            row = s.get(OutreachSend, send_id); row.status = "failed"
        raise RuntimeError("RESEND_API_KEY not configured")
    from_email = settings.digest_from_email
    if not from_email:
        raise RuntimeError("verified From address not configured")
    with httpx.Client(timeout=20) as client:
        resp = client.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"}, json={"from": from_email, "to": [to], "subject": subject, "text": body})
        resp.raise_for_status()
        provider_id = resp.json().get("id")
    with session_scope() as s:
        row = s.get(OutreachSend, send_id); row.status = "sent"; row.provider_id = provider_id
    return {"status": "sent", "provider_id": provider_id, "outreach_send_id": send_id}
