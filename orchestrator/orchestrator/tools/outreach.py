from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from sqlalchemy import desc, func, select

from orchestrator.config import settings
from orchestrator.db.models import Approval, EmailDomainBlock, EmailSuppression, Event, MoneyTransaction, OutreachSend, SystemState
from orchestrator.db.session import session_scope

log = logging.getLogger(__name__)

MAX_PER_EXPERIMENT = 100
MAX_PER_DAY = 50
_FROM_DOMAIN_VERIFIED: bool | None = None
_FROM_DOMAIN_CHECKED_AT: datetime | None = None


def _recipient_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _recipient_domain(email: str) -> str:
    return email.strip().lower().split("@")[-1]


def _unsubscribe_secret() -> str:
    return settings.unsubscribe_secret or settings.lead_ip_hash_pepper


def make_unsubscribe_token(email_or_hash: str, experiment_id: int, *, is_hash: bool = False) -> str:
    recipient_hash = email_or_hash if is_hash else _recipient_hash(email_or_hash)
    payload = {"rh": recipient_hash, "eid": int(experiment_id)}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(_unsubscribe_secret().encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def verify_unsubscribe_token(token: str) -> dict:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    except Exception as exc:
        raise ValueError("invalid unsubscribe token") from exc
    expected = hmac.new(_unsubscribe_secret().encode(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("invalid unsubscribe token")
    data = json.loads(raw.decode())
    if not data.get("rh") or not isinstance(data.get("eid"), int):
        raise ValueError("invalid unsubscribe token")
    return data


def unsubscribe_url(email_or_hash: str, experiment_id: int, *, is_hash: bool = False) -> str:
    token = make_unsubscribe_token(email_or_hash, experiment_id, is_hash=is_hash)
    return f"{settings.public_dashboard_url.rstrip('/')}/api/public/unsubscribe?t={quote(token)}"


def is_suppressed(email: str) -> tuple[bool, str | None]:
    rh = _recipient_hash(email)
    domain = _recipient_domain(email)
    with session_scope() as s:
        if s.scalar(select(EmailSuppression.id).where(EmailSuppression.recipient_hash == rh)):
            return True, "recipient_suppressed"
        if s.scalar(select(EmailDomainBlock.id).where(EmailDomainBlock.domain == domain)):
            return True, "domain_blocked"
    return False, None


def add_suppression(email: str, *, reason: str = "manual", source: str = "operator", provider_id: str | None = None, metadata: dict | None = None) -> dict:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("valid recipient email required")
    rh = _recipient_hash(email)
    domain = _recipient_domain(email)
    with session_scope() as s:
        row = s.scalars(select(EmailSuppression).where(EmailSuppression.recipient_hash == rh)).first()
        if row is None:
            row = EmailSuppression(recipient_hash=rh, recipient_domain=domain, reason=reason[:40], source=source[:120], provider_id=provider_id, metadata_json=metadata or {})
            s.add(row); s.flush()
        s.add(Event(kind="email_suppression_added", actor=source, message=f"Email suppression added ({reason})", payload={"suppression_id": row.id, "reason": reason, "domain": domain}))
        return {"id": row.id, "recipient_hash": row.recipient_hash, "recipient_domain": row.recipient_domain, "reason": row.reason, "source": row.source}


def add_domain_block(domain: str, *, reason: str = "manual", source: str = "operator", metadata: dict | None = None) -> dict:
    domain = domain.strip().lower().lstrip("@")
    if not domain or "." not in domain:
        raise ValueError("valid domain required")
    with session_scope() as s:
        row = s.scalars(select(EmailDomainBlock).where(EmailDomainBlock.domain == domain)).first()
        if row is None:
            row = EmailDomainBlock(domain=domain, reason=reason[:40], source=source[:120], metadata_json=metadata or {})
            s.add(row); s.flush()
        s.add(Event(kind="email_domain_block_added", actor=source, message=f"Email domain blocked ({reason})", payload={"domain_block_id": row.id, "reason": reason, "domain": domain}))
        return {"id": row.id, "domain": row.domain, "reason": row.reason, "source": row.source}


def verify_from_domain() -> bool | None:
    global _FROM_DOMAIN_VERIFIED, _FROM_DOMAIN_CHECKED_AT
    if not settings.resend_api_key:
        _FROM_DOMAIN_VERIFIED = None
        _FROM_DOMAIN_CHECKED_AT = datetime.now(timezone.utc)
        log.warning("RESEND_API_KEY unset; outreach will dry-run only")
        return None
    if not settings.digest_from_email or "@" not in settings.digest_from_email:
        _FROM_DOMAIN_VERIFIED = False
        _FROM_DOMAIN_CHECKED_AT = datetime.now(timezone.utc)
        log.warning("DIGEST_FROM_EMAIL unset; live outreach will refuse")
        return False
    domain = _recipient_domain(settings.digest_from_email)
    try:
        resp = httpx.get("https://api.resend.com/domains", headers={"Authorization": f"Bearer {settings.resend_api_key}"}, timeout=10)
        resp.raise_for_status()
        verified = any(d.get("name") == domain and d.get("status") == "verified" for d in resp.json().get("data", []))
    except Exception as exc:
        verified = False
        log.warning("from_domain_verified=false domain=%s check_error=%s", domain, exc.__class__.__name__)
    else:
        log.info("from_domain_verified=%s domain=%s", str(verified).lower(), domain)
    _FROM_DOMAIN_VERIFIED = verified
    _FROM_DOMAIN_CHECKED_AT = datetime.now(timezone.utc)
    return verified


def _ensure_live_allowed() -> None:
    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY not configured")
    if _FROM_DOMAIN_VERIFIED is not True:
        raise RuntimeError("from-domain not verified with Resend")


def _payload_with_unsubscribe(to: str, subject: str, body_text: str, experiment_id: int, body_html: str | None = None) -> dict:
    url = unsubscribe_url(to, experiment_id)
    footer_text = f"\n\n--\nUnsubscribe: {url}"
    final_text = body_text if "unsubscribe" in body_text.lower() else body_text.rstrip() + footer_text
    html_footer = f'<p style="font-size:12px;color:#666"><a href="{url}">Unsubscribe</a></p>'
    final_html = (body_html.rstrip() + html_footer) if body_html else None
    headers = {
        "List-Unsubscribe": f"<{url}>, <mailto:unsub@profithub.me?subject=unsub-{make_unsubscribe_token(to, experiment_id)}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    payload = {"from": settings.digest_from_email, "to": [to], "subject": subject, "text": final_text, "headers": headers}
    if final_html:
        payload["html"] = final_html
    return payload


def _insert_send_row(*, s, experiment_id: int, rh: str, domain: str, subject: str, status: str, dry_run: bool, audit_payload: dict, reason: str | None = None) -> OutreachSend:
    row = OutreachSend(experiment_id=experiment_id, recipient_hash=rh, recipient_domain=domain, subject=subject[:300], status=status, dry_run=dry_run, audit_payload=audit_payload, error=reason)
    s.add(row); s.flush()
    return row


def send_email(to: str, subject: str, body: str, *, experiment_id: int, dry_run: bool = True, body_html: str | None = None) -> dict:
    to = to.strip().lower()
    if not to or "@" not in to:
        raise ValueError("valid recipient email required")
    # Keep the operator-supplied unsubscribe substring check as requested.
    if "unsubscribe" not in body.lower():
        raise ValueError("operator-supplied unsubscribe footer required")
    rh = _recipient_hash(to)
    domain = _recipient_domain(to)
    today = datetime.now(timezone.utc).date()
    suppressed, suppress_reason = is_suppressed(to)
    audit_payload = _payload_with_unsubscribe(to, subject, body, experiment_id, body_html)
    if suppressed:
        with session_scope() as s:
            existing = s.scalars(select(OutreachSend).where(OutreachSend.experiment_id == experiment_id, OutreachSend.recipient_hash == rh)).first()
            if existing:
                return {"status": existing.status, "idempotent": True, "outreach_send_id": existing.id, "reason": existing.error}
            row = _insert_send_row(s=s, experiment_id=experiment_id, rh=rh, domain=domain, subject=subject, status="suppressed", dry_run=dry_run, audit_payload=audit_payload, reason=suppress_reason)
            return {"status": "suppressed", "outreach_send_id": row.id, "reason": suppress_reason}
    with session_scope() as s:
        existing = s.scalars(select(OutreachSend).where(OutreachSend.experiment_id == experiment_id, OutreachSend.recipient_hash == rh)).first()
        if existing:
            return {"status": existing.status, "idempotent": True, "outreach_send_id": existing.id}
        domain_count = s.scalar(select(func.count(OutreachSend.id)).where(OutreachSend.experiment_id == experiment_id, OutreachSend.recipient_domain == domain, OutreachSend.cancelled == False, OutreachSend.status.notin_(["suppressed", "failed"]))) or 0
        if domain_count >= settings.outreach_domain_cap:
            raise RuntimeError(f"per-domain cap reached for {domain}")
        last = s.scalar(select(OutreachSend.created_at).where(OutreachSend.experiment_id == experiment_id, OutreachSend.status.notin_(["suppressed", "failed"])).order_by(desc(OutreachSend.created_at)).limit(1))
        if last is not None:
            now = datetime.now(timezone.utc)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (now - last).total_seconds()
            if elapsed < settings.outreach_throttle_seconds:
                wait = settings.outreach_throttle_seconds - elapsed
                raise RuntimeError(f"throttled; retry in {wait:.0f}s")
        exp_count = s.scalar(select(func.count(OutreachSend.id)).where(OutreachSend.experiment_id == experiment_id, OutreachSend.status.notin_(["suppressed", "failed"]))) or 0
        if exp_count >= MAX_PER_EXPERIMENT:
            raise RuntimeError("per-experiment outreach cap reached")
        day_count = s.scalar(select(func.count(OutreachSend.id)).where(func.date(OutreachSend.created_at) == today, OutreachSend.status.notin_(["suppressed", "failed"]))) or 0
        if day_count >= MAX_PER_DAY:
            raise RuntimeError("daily outreach cap reached")
        if settings.resend_api_key and _FROM_DOMAIN_VERIFIED is False:
            raise RuntimeError("from-domain not verified with Resend")
        if not dry_run:
            _ensure_live_allowed()
        row = _insert_send_row(s=s, experiment_id=experiment_id, rh=rh, domain=domain, subject=subject, status="dry_run" if dry_run else "queued", dry_run=dry_run, audit_payload=audit_payload)
        tx = MoneyTransaction(action="outreach_send_email", amount_usd=0.0, vendor="resend", idempotency_key=f"outreach:{experiment_id}:{rh}", status="simulated" if dry_run else "pending", result_json={"outreach_send_id": row.id})
        s.add(tx)
        send_id = row.id
    if dry_run:
        return {"status": "dry_run", "outreach_send_id": send_id}
    with httpx.Client(timeout=20) as client:
        resp = client.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"}, json=audit_payload)
        resp.raise_for_status()
        provider_id = resp.json().get("id")
    with session_scope() as s:
        row = s.get(OutreachSend, send_id); row.status = "sent"; row.provider_id = provider_id
    return {"status": "sent", "provider_id": provider_id, "outreach_send_id": send_id}


def execute_batch(approval_id: int, force_live: bool | None = None) -> dict:
    with session_scope() as s:
        approval = s.get(Approval, approval_id)
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        if approval.status not in {"approved", "completed"} or approval.action != "send_outreach_batch":
            raise ValueError(f"Approval {approval_id} must be approved send_outreach_batch")
        payload = approval.payload or {}
        recipients = [str(x).strip().lower() for x in payload.get("recipients", []) if str(x).strip()]
        if len(recipients) > settings.outreach_per_batch_max:
            raise RuntimeError("outreach batch exceeds OUTREACH_PER_BATCH_MAX")
        experiment_id = int(payload.get("experiment_id") or 0)
        subject = str(payload.get("subject") or "")
        body_text = str(payload.get("body_text") or payload.get("body") or "")
        body_html = payload.get("body_html")
        system = s.get(SystemState, 1)
        dry_run = bool(system.dry_run if system else True)
        if force_live is False:
            dry_run = True
        # Global dry_run is sticky: execute_live=true only matters after the operator disables system dry-run.
    outcomes = []
    throttled = False
    for recipient in recipients:
        try:
            result = send_email(recipient, subject, body_text, experiment_id=experiment_id, dry_run=dry_run, body_html=body_html)
        except RuntimeError as exc:
            if "throttled" in str(exc):
                outcomes.append({"recipient_hash": _recipient_hash(recipient), "status": "throttled", "error": str(exc)})
                throttled = True
                break
            outcomes.append({"recipient_hash": _recipient_hash(recipient), "status": "error", "error": str(exc)})
        else:
            outcomes.append({"recipient_hash": _recipient_hash(recipient), **result})
    with session_scope() as s:
        approval = s.get(Approval, approval_id)
        if not throttled:
            approval.status = "completed"
        s.add(Event(kind="outreach_batch_result", actor="outreach", message=f"Outreach batch #{approval_id} {'throttled' if throttled else 'completed'}", payload={"approval_id": approval_id, "outcomes": outcomes, "dry_run": dry_run}))
    return {"approval_id": approval_id, "status": "throttled" if throttled else "completed", "outcomes": outcomes, "dry_run": dry_run}
