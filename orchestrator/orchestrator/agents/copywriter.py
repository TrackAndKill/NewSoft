import html as html_mod
import json
import re

from sqlalchemy import desc, select

from orchestrator.config import settings
from orchestrator.db.models import Event, SiteContent, Venture
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent

COPYWRITER = AgentSpec(
    name="copywriter",
    role="Copywriter",
    model=settings.model_sonnet,
    max_tokens=3000,
    system_prompt=(
        "You are the Copywriter for an autonomous venture. Given a venture charter and target ICP, "
        "produce a single-file static landing page (HTML5 with inline CSS, no JS frameworks, no external fonts/CDN). "
        "Required sections: hero with headline + subhead, 3-5 value-prop bullets, a single primary CTA: an email signup form that POSTs to {SIGNUP_URL}. "
        "The form must include an email field, a hidden source=landing field, and a hidden slug={SLUG} field. "
        "After submit, show a thank-you message. The page must be under 30 KB. No tracking scripts. No analytics. No third-party requests. "
        'Return STRICT JSON: {"html":"<!doctype...","meta":{"title":"...","description":"..."}}. No prose outside the JSON.'
    ),
)


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise ValueError("No JSON in Copywriter output")
    return json.loads(m.group(0))


def _fallback_html(name: str, slug: str, charter: str, signup_url: str) -> dict:
    safe_name = html_mod.escape(name)
    safe_charter = html_mod.escape((charter or "A focused new software venture.")[:240])
    safe_signup = html_mod.escape(signup_url, quote=True)
    safe_slug = html_mod.escape(slug, quote=True)
    title = f"{safe_name} — early access"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><meta name="description" content="Join the early-access list for {safe_name}.">
<style>body{{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;background:#0b1020;color:#f8fafc}}main{{max-width:860px;margin:0 auto;padding:72px 24px}}.hero{{padding:48px;border:1px solid #263149;border-radius:28px;background:linear-gradient(135deg,#111a33,#121827)}}h1{{font-size:clamp(36px,7vw,72px);line-height:0.95;margin:0 0 18px}}p,li{{color:#cbd5e1;font-size:18px;line-height:1.6}}ul{{display:grid;gap:10px;margin:28px 0}}form{{display:flex;gap:10px;flex-wrap:wrap;margin-top:28px}}input[type=email]{{flex:1;min-width:240px;border:1px solid #334155;border-radius:14px;background:#020617;color:#fff;padding:16px;font-size:16px}}button{{border:0;border-radius:14px;background:#38bdf8;color:#00111c;font-weight:800;padding:16px 22px;font-size:16px}}small{{display:block;color:#94a3b8;margin-top:16px}}</style></head>
<body><main><section class="hero"><h1>{safe_name} turns a painful workflow into a simple daily habit.</h1><p>{safe_charter}</p><ul><li>Purpose-built for operators who need proof, not dashboards for dashboards' sake.</li><li>Launches with a narrow promise and measures demand before bloat.</li><li>Private by default: no trackers, no analytics pixels, no third-party scripts.</li></ul><form method="post" action="{safe_signup}"><input name="email" type="email" required placeholder="you@example.com" autocomplete="email"><input type="hidden" name="source" value="landing"><input type="hidden" name="slug" value="{safe_slug}"><button type="submit">Join early access</button></form><small>We'll only use your email for this early-access list.</small></section></main></body></html>"""
    return {"html": page, "meta": {"title": f"{name} — early access", "description": f"Join the early-access list for {name}."}}


def _validate(page: str, signup_url: str) -> None:
    lowered = page.lower()
    if len(page.encode("utf-8")) > 30_000:
        raise ValueError("landing page exceeds 30KB")
    if "<form" not in lowered or "email" not in lowered or signup_url not in page:
        raise ValueError("landing page missing required form/signup URL")
    forbidden = ["<script", "https://www.google-analytics", "googletagmanager", "cdn.", "fonts.googleapis"]
    if any(f in lowered for f in forbidden):
        raise ValueError("landing page contains forbidden third-party/tracking content")


def draft_landing_page(venture_id: int, signup_url: str) -> int:
    with session_scope() as s:
        existing = s.scalars(select(SiteContent).where(SiteContent.venture_id == venture_id, SiteContent.kind == "landing_page").order_by(desc(SiteContent.created_at))).first()
        if existing:
            return existing.id
        venture = s.get(Venture, venture_id)
        if venture is None:
            raise ValueError(f"Venture {venture_id} not found")
        context = {"venture": {"id": venture.id, "name": venture.name, "slug": venture.slug, "charter": venture.charter}, "signup_url": signup_url}
    run_id = None
    try:
        out = run_agent(COPYWRITER, [{"role": "user", "content": json.dumps(context, indent=2)}], expected_output_tokens=1800)
        run_id = out.run_id
        data = _parse_json(out.text)
    except Exception:
        data = _fallback_html(context["venture"]["name"], context["venture"]["slug"], context["venture"]["charter"], signup_url)
    page = str(data.get("html") or "")
    try:
        _validate(page, signup_url)
    except Exception:
        data = _fallback_html(context["venture"]["name"], context["venture"]["slug"], context["venture"]["charter"], signup_url)
        page = data["html"]
        _validate(page, signup_url)
    with session_scope() as s:
        row = SiteContent(venture_id=venture_id, kind="landing_page", html=page, meta_json=data.get("meta") if isinstance(data.get("meta"), dict) else {}, agent_run_id=run_id)
        s.add(row); s.flush()
        s.add(Event(kind="site_content_created", actor="copywriter", message=f"Landing page drafted for venture #{venture_id}: content #{row.id}", payload={"venture_id": venture_id, "site_content_id": row.id, "bytes": len(page.encode('utf-8'))}))
        return row.id
