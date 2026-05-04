from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape

from sqlalchemy import desc, func, select

from orchestrator.config import settings
from orchestrator.db.models import AgentRun, Approval, Event, Idea, Memo, SystemState, Venture
from orchestrator.db.session import session_scope
from orchestrator.email import EmailUnavailable, send_email


def _dashboard(path: str = "") -> str:
    return settings.public_dashboard_url.rstrip("/") + path


def build_digest() -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    with session_scope() as s:
        state = s.get(SystemState, 1)
        events = s.scalars(select(Event).where(Event.created_at >= since).order_by(desc(Event.created_at))).all()
        approvals = s.scalars(select(Approval).where(Approval.status == "pending").order_by(Approval.created_at.asc())).all()
        ideas = s.scalars(select(Idea).where(Idea.created_at >= since).order_by(desc(Idea.created_at)).limit(10)).all()
        memos = s.scalars(select(Memo).where(Memo.created_at >= since).order_by(desc(Memo.created_at)).limit(10)).all()
        ventures = s.scalars(select(Venture).where(Venture.created_at >= since).order_by(desc(Venture.created_at)).limit(10)).all()
        llm_today = float(state.spend_today_usd if state else 0.0)
        llm_cap = float(state.daily_spend_cap_usd if state else 0.0)
        money_today = float(getattr(state, "money_spend_today_usd", 0.0) if state else 0.0)
        money_cap = float(getattr(state, "money_daily_cap_usd", 50.0) if state else 50.0)
        active = bool(state.active) if state else False
        dry_run = bool(state.dry_run) if state else True

    by_kind: dict[str, dict] = {}
    for ev in events:
        slot = by_kind.setdefault(ev.kind, {"count": 0, "latest": ""})
        slot["count"] += 1
        if not slot["latest"]:
            slot["latest"] = ev.message
    error_count = sum(1 for ev in events if ev.kind == "error")
    subject = f"NewSoft daily digest — {date.today().isoformat()}"
    lines = [
        subject,
        "",
        "State",
        f"- active={active}, dry_run={dry_run}",
        f"- LLM spend today: ${llm_today:.4f} / ${llm_cap:.2f}",
        f"- Money spend today: ${money_today:.2f} / ${money_cap:.2f}",
        "",
        "Last 24 hours",
    ]
    if by_kind:
        for kind, data in sorted(by_kind.items()):
            lines.append(f"- {kind}: {data['count']} — {data['latest']}")
    else:
        lines.append("- No events recorded.")
    lines += ["", f"Pending approvals ({len(approvals)}) — {_dashboard('/approvals')}"]
    for a in approvals[:10]:
        age_h = max(0, int((datetime.now(timezone.utc) - a.created_at).total_seconds() // 3600))
        lines.append(f"- #{a.id} {a.action} by {a.requested_by}, age {age_h}h")
    lines += ["", "New ideas / memos / ventures"]
    for idea in ideas[:5]:
        lines.append(f"- Idea #{idea.id}: {idea.title} ({_dashboard('/ideas')})")
    for memo in memos[:5]:
        lines.append(f"- Memo #{memo.id}: {memo.recommendation} ({_dashboard('/memos')})")
    for venture in ventures[:5]:
        lines.append(f"- Venture #{venture.id}: {venture.name} ({_dashboard('/ventures/' + venture.slug)})")
    lines += ["", f"Errors: {error_count}"]
    text_body = "\n".join(lines)
    html_body = "<pre>" + escape(text_body) + "</pre>"
    return {
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "summary_dict": {
            "active": active,
            "dry_run": dry_run,
            "llm_spend_today_usd": llm_today,
            "llm_daily_cap_usd": llm_cap,
            "money_spend_today_usd": money_today,
            "money_daily_cap_usd": money_cap,
            "event_kinds": by_kind,
            "pending_approvals": len(approvals),
            "new_ideas": len(ideas),
            "new_memos": len(memos),
            "new_ventures": len(ventures),
            "errors": error_count,
        },
    }


def send_digest() -> None:
    digest = build_digest()
    try:
        result = send_email(subject=digest["subject"], text_body=digest["text_body"], html_body=digest["html_body"])
    except EmailUnavailable as e:
        with session_scope() as s:
            s.add(Event(kind="digest_skipped", actor="digest", message=f"Daily digest skipped: {e}", payload=digest))
        return
    except Exception as e:
        with session_scope() as s:
            s.add(Event(kind="error", actor="digest", message=f"Daily digest send failed: {e}", payload={"subject": digest["subject"]}))
        return
    with session_scope() as s:
        s.add(Event(kind="digest_sent", actor="digest", message=f"Daily digest sent: {digest['subject']}", payload={"result": result, "summary": digest["summary_dict"]}))
