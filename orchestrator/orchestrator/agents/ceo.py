"""CEO agent. Turns a Board-FUND'd memo into a venture charter."""

import json
import re
from dataclasses import dataclass

from orchestrator.config import settings
from orchestrator.db.models import Event, Idea, Memo, Venture
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent

CEO = AgentSpec(
    name="ceo",
    role="CEO",
    model=settings.model_opus,
    max_tokens=1500,
    system_prompt=(
        "You are the CEO of an autonomous venture firm. The Board has voted FUND "
        "on the attached memo. Produce a one-page venture charter as STRICT JSON: "
        '{"name":"venture name","slug":"kebab-case-slug","mission":"...",'
        '"okrs":["O1: ...","KR1: ...",...],"kill_criteria":["..."],'
        '"first_90_days":"..."}. '
        "The slug must be 3-30 lowercase chars, kebab-case, no leading digit. "
        "OKRs: 1 objective with 3 measurable KRs. Kill criteria: 2-3 specific, "
        "time-bound conditions under which the venture should be shut down. "
        "No prose outside the JSON."
    ),
)


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


@dataclass
class CharterResult:
    venture_id: int
    slug: str
    name: str
    cost_usd: float


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in CEO output: {text[:200]}")
    return json.loads(match.group(0))


def _safe_slug(raw: str, fallback: str) -> str:
    slug = _SLUG_RE.sub("-", raw.lower()).strip("-")
    if not slug or len(slug) < 3 or slug[0].isdigit():
        slug = fallback
    return slug[:30]


def _ensure_unique_slug(s, base: str) -> str:
    from sqlalchemy import select

    n = 0
    candidate = base
    while s.scalars(select(Venture).where(Venture.slug == candidate)).first() is not None:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def charter_venture(memo_id: int) -> CharterResult:
    with session_scope() as s:
        memo = s.get(Memo, memo_id)
        if memo is None:
            raise ValueError(f"Memo {memo_id} not found")
        if memo.decision != "fund":
            raise ValueError(f"Memo {memo_id} decision is {memo.decision}, expected fund")
        idea = s.get(Idea, memo.idea_id)
        memo_content = memo.content
        idea_title = idea.title if idea else "Untitled"
        idea_id = memo.idea_id

    out = run_agent(
        CEO,
        [
            {
                "role": "user",
                "content": (
                    f"Idea title: {idea_title}\n\n"
                    f"Investment memo:\n{memo_content}\n\n"
                    "Produce the venture charter."
                ),
            }
        ],
        expected_output_tokens=1200,
    )
    data = _parse_json(out.text)
    name = data.get("name") or idea_title
    raw_slug = data.get("slug") or name
    base_slug = _safe_slug(raw_slug, fallback="venture")

    charter_md = "\n".join(
        [
            f"# {name}",
            "",
            f"**Mission.** {data.get('mission', '').strip()}",
            "",
            "## 90-day OKRs",
            *[f"- {item}" for item in data.get("okrs", [])],
            "",
            "## Kill criteria",
            *[f"- {item}" for item in data.get("kill_criteria", [])],
            "",
            "## First 90 days",
            data.get("first_90_days", "").strip(),
        ]
    )

    with session_scope() as s:
        slug = _ensure_unique_slug(s, base_slug)
        v = Venture(
            idea_id=idea_id,
            memo_id=memo_id,
            slug=slug,
            name=name,
            charter=charter_md,
            status="chartered",
        )
        s.add(v)
        s.flush()
        venture_id = v.id
        s.add(
            Event(
                kind="venture_chartered",
                actor="ceo",
                message=f"Venture chartered: {name} ({slug})",
                payload={"venture_id": venture_id, "memo_id": memo_id, "slug": slug},
            )
        )

    return CharterResult(venture_id=venture_id, slug=slug, name=name, cost_usd=out.cost_usd)
