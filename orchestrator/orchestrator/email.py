import smtplib
from email.message import EmailMessage

import httpx

from orchestrator.config import settings


class EmailUnavailable(Exception):
    pass


def send_email(*, subject: str, text_body: str, html_body: str) -> dict:
    if not settings.digest_to_email or not settings.digest_from_email:
        raise EmailUnavailable("DIGEST_TO_EMAIL and DIGEST_FROM_EMAIL must be set")
    if settings.resend_api_key:
        payload = {
            "from": settings.digest_from_email,
            "to": [settings.digest_to_email],
            "subject": subject,
            "text": text_body,
            "html": html_body,
        }
        headers = {"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"}
        resp = httpx.post("https://api.resend.com/emails", headers=headers, json=payload, timeout=20)
        if resp.status_code >= 400:
            raise RuntimeError(f"Resend returned HTTP {resp.status_code}: {resp.text[:200]}")
        return {"transport": "resend", "response": resp.json()}
    if settings.smtp_host:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.digest_from_email
        msg["To"] = settings.digest_to_email
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return {"transport": "smtp"}
    raise EmailUnavailable("No email transport configured")
