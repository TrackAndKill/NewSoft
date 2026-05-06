from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import EmailSuppression, Event, OutreachSend
from orchestrator.db.session import session_scope
from orchestrator.tools.outreach import _recipient_hash, add_suppression

REPLAY_WINDOW_SECONDS = 300


def _header(headers: Mapping[str, str], name: str) -> str:
    lname = name.lower()
    for key, value in headers.items():
        if key.lower() == lname:
            return value
    return ""


def verify_resend_signature(body_bytes: bytes, headers: Mapping[str, str]) -> bool:
    secret = settings.resend_webhook_secret
    if not secret:
        return False
    svix_id = _header(headers, "svix-id")
    svix_ts = _header(headers, "svix-timestamp")
    svix_sig = _header(headers, "svix-signature")
    if not svix_id or not svix_ts or not svix_sig:
        return False
    try:
        ts = int(svix_ts)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > REPLAY_WINDOW_SECONDS:
        return False
    signed = svix_id.encode() + b"." + svix_ts.encode() + b"." + body_bytes
    expected = base64.b64encode(hmac.new(secret.encode(), signed, hashlib.sha256).digest()).decode()
    for part in svix_sig.split():
        for sig in part.split(","):
            if sig.startswith("v1,"):
                candidate = sig[3:]
            elif part.startswith("v1,") and sig != "v1":
                candidate = sig
            else:
                continue
            if hmac.compare_digest(candidate, expected):
                return True
    # Resend/Svix commonly sends one comma-separated value: v1,<base64>
    if svix_sig.startswith("v1,"):
        return hmac.compare_digest(svix_sig.split(",", 1)[1], expected)
    return False


def _extract_email(data: dict) -> str:
    for key in ("to", "email", "recipient"):
        value = data.get(key)
        if isinstance(value, str) and "@" in value:
            return value.strip().lower()
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and "@" in first:
                return first.strip().lower()
    return ""


def _extract_provider_id(payload: dict, data: dict) -> str | None:
    for key in ("email_id", "message_id", "id"):
        value = data.get(key) or payload.get(key)
        if value:
            return str(value)
    return None


def handle_resend_event(payload: dict) -> dict:
    event_type = str(payload.get("type") or payload.get("event") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    provider_id = _extract_provider_id(payload, data)
    email = _extract_email(data)
    now = datetime.now(timezone.utc)
    reason: str | None = None
    suppress = False
    if event_type == "email.bounced":
        bounce_type = str(data.get("bounce_type") or data.get("type") or "hard").lower()
        if bounce_type != "soft":
            reason = "bounce"; suppress = True
    elif event_type == "email.complained":
        reason = "complaint"; suppress = True
    elif event_type in {"email.unsubscribed", "email.unsubscribe"}:
        reason = "unsubscribe"; suppress = True

    rh = _recipient_hash(email) if email else None
    updated_send_id = None
    with session_scope() as s:
        send = None
        if provider_id:
            send = s.scalars(select(OutreachSend).where(OutreachSend.provider_id == provider_id).order_by(OutreachSend.created_at.desc())).first()
        if send is None and rh:
            send = s.scalars(select(OutreachSend).where(OutreachSend.recipient_hash == rh).order_by(OutreachSend.created_at.desc())).first()
        if send is not None:
            updated_send_id = send.id
            if event_type == "email.delivered":
                send.delivered_at = now; send.status = "delivered"
            elif event_type == "email.opened":
                send.opened_at = now
            elif event_type == "email.clicked":
                send.clicked_at = now
            elif event_type == "email.bounced":
                send.bounced_at = now; send.status = "bounced"
            elif event_type == "email.complained":
                send.complained_at = now; send.status = "complained"
        if suppress and email and reason:
            # Insert outside this session helper would open nested session; do it inline.
            domain = email.split("@")[-1].lower()
            existing = s.scalars(select(EmailSuppression).where(EmailSuppression.recipient_hash == rh)).first()
            if existing is None:
                s.add(EmailSuppression(recipient_hash=rh, recipient_domain=domain, reason=reason, source="resend_webhook", provider_id=provider_id, metadata_json={"event_type": event_type}))
            sends = s.scalars(select(OutreachSend).where(OutreachSend.recipient_hash == rh, OutreachSend.status.in_(["queued", "dry_run"]))).all()
            for row in sends:
                row.cancelled = True
                if row.status == "queued":
                    row.status = "cancelled"
        s.add(Event(kind="resend_webhook", actor="resend", message=f"Resend event {event_type or 'unknown'} processed", payload={"event_type": event_type, "provider_id": provider_id, "outreach_send_id": updated_send_id, "suppressed": bool(suppress and email)}))
    return {"ok": True, "event_type": event_type, "suppressed": bool(suppress and email), "outreach_send_id": updated_send_id}


def sign_test_payload(body_bytes: bytes, secret: str, svix_id: str, timestamp: int) -> str:
    signed = f"{svix_id}.{timestamp}.".encode() + body_bytes
    return "v1," + base64.b64encode(hmac.new(secret.encode(), signed, hashlib.sha256).digest()).decode()
