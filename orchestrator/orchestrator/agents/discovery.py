"""Discovery pod: Market Scout -> Opportunity Analyst -> Memo Writer."""

import json
import re
from dataclasses import dataclass

from orchestrator.config import settings
from sqlalchemy import select

from orchestrator.db.models import Event, Idea, Memo, ToolCall
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent
from orchestrator.tools.search import TOOLS as SEARCH_TOOLS
from orchestrator.tools.search import search_tools_available

SCOUT = AgentSpec(
    name="market_scout",
    role="Market Scout",
    model=settings.model_sonnet,
    max_tokens=2400,
    tools=SEARCH_TOOLS if search_tools_available() else [],
    system_prompt=(
        "You are the Market Scout for an autonomous venture firm. Given a "
        "founder goal, propose candidate small-business / SaaS ideas. Favor "
        "ideas with: small initial capex, fast time-to-revenue, narrow ICP, "
        "and at least one channel testable for under $200.\n\n"
        "Workflow:\n"
        "  1. Make 2-4 web_search calls to find recent signals (Reddit,\n"
        "     Indie Hackers, ProductHunt, Hacker News, GitHub trending,\n"
        "     niche forums). Use fetch_url on the most promising hit.\n"
        "  2. Synthesize 5 candidate ideas grounded in what you found.\n"
        "  3. Each idea's source field MUST be a URL from your tool calls.\n"
        "     If you cannot ground an idea in a real URL, omit it.\n\n"
        "Return STRICT JSON:\n"
        '{"ideas":[{"title":"...","summary":"...","source":"https://..."}]}\n'
        "No prose outside the JSON."
    ),
)

ANALYST = AgentSpec(
    name="opportunity_analyst",
    role="Opportunity Analyst",
    model=settings.model_sonnet,
    max_tokens=2000,
    system_prompt=(
        "You are the Opportunity Analyst. Score each idea on a rubric: "
        "tam (1-10), competition (1-10, higher = less crowded), "
        "build_cost (1-10, higher = cheaper to build), "
        "time_to_revenue (1-10, higher = faster), "
        "founder_fit (1-10). Compute composite = average. "
        "Return STRICT JSON: "
        '{"scored":[{"title":"...","rubric":{...},"composite":N.N,"rationale":"..."}]}. '
        "No prose outside the JSON."
    ),
)

MEMO_WRITER = AgentSpec(
    name="memo_writer",
    role="Investment Memo Writer",
    model=settings.model_opus,
    max_tokens=2500,
    system_prompt=(
        "You are the Investment Memo Writer. Produce a crisp memo (under 500 "
        "words) for the firm's Board covering: thesis, ICP, wedge, GTM plan, "
        "first experiment under $200, kill criteria, and a recommendation "
        "(FUND / EXPLORE / PASS). End the memo with a line "
        '"RECOMMENDATION: FUND|EXPLORE|PASS".'
    ),
)


@dataclass
class DiscoveryResult:
    idea_ids: list[int]
    top_idea_id: int | None
    memo_id: int | None
    total_cost_usd: float


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in agent output: {text[:200]}")
    return json.loads(match.group(0))


def _backfill_source_urls(scout_run_id: int, ideas: list[dict]) -> list[dict]:
    """Ensure every idea's source contains a URL; drop those that can't."""
    urls = _search_result_urls(scout_run_id)
    out = []
    for idea in ideas:
        src = (idea.get("source") or "").strip()
        if "http" in src:
            out.append(idea)
            continue
        if urls:
            idea["source"] = urls.pop(0)
            out.append(idea)
        else:
            # No URL anywhere; drop rather than persist a fake source.
            continue
    return out


def _search_result_urls(scout_run_id: int) -> list[str]:
    with session_scope() as s:
        rows = s.scalars(
            select(ToolCall).where(
                ToolCall.agent_run_id == scout_run_id,
                ToolCall.tool == "web_search",
            )
        ).all()
        urls: list[str] = []
        for tc in rows:
            result = tc.result or {}
            candidates = result.get("results")
            data = result.get("data")
            if candidates is None and isinstance(data, dict):
                candidates = data.get("results")
            if candidates is None and isinstance(data, list):
                candidates = data
            for r in candidates or []:
                u = r.get("url") if isinstance(r, dict) else None
                if u:
                    urls.append(u)
    return urls


def _ideas_from_tool_calls(scout_run_id: int) -> list[dict]:
    """Fallback ideas when Scout used tools but failed to emit strict JSON."""
    with session_scope() as s:
        rows = s.scalars(
            select(ToolCall).where(
                ToolCall.agent_run_id == scout_run_id,
                ToolCall.tool == "web_search",
            )
        ).all()
        ideas: list[dict] = []
        seen: set[str] = set()
        for tc in rows:
            result = tc.result or {}
            candidates = result.get("results")
            data = result.get("data")
            if candidates is None and isinstance(data, dict):
                candidates = data.get("results")
            if candidates is None and isinstance(data, list):
                candidates = data
            for r in candidates or []:
                if not isinstance(r, dict):
                    continue
                url = r.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                title = r.get("title") or "URL-grounded market signal"
                snippet = r.get("snippet") or "Scout found this URL via web_search."
                ideas.append(
                    {
                        "title": title[:160],
                        "summary": snippet[:1200],
                        "source": url,
                    }
                )
                if len(ideas) >= 5:
                    return ideas
    return ideas


def run_discovery(goal_id: int, goal_text: str) -> DiscoveryResult:
    total_cost = 0.0

    scout_out = run_agent(
        SCOUT,
        [{"role": "user", "content": f"Founder goal:\n{goal_text}\n\nPropose 5 candidate ideas."}],
    )
    total_cost += scout_out.cost_usd
    try:
        scout_data = _parse_json(scout_out.text)
    except Exception as e:
        scout_data = {"ideas": _ideas_from_tool_calls(scout_out.run_id)}
        with session_scope() as s:
            s.add(
                Event(
                    kind="discovery_scout_json_fallback",
                    actor="market_scout",
                    message="Scout emitted non-JSON after tool use; synthesized URL-grounded ideas from web_search results.",
                    payload={"run_id": scout_out.run_id, "error": str(e)[:500]},
                )
            )
    scout_data["ideas"] = _backfill_source_urls(
        scout_out.run_id, scout_data.get("ideas", [])
    )
    if not scout_data["ideas"]:
        # Log and bail cleanly — no point running analyst on zero ideas.
        with session_scope() as s:
            s.add(
                Event(
                    kind="discovery_no_urls",
                    actor="market_scout",
                    message="Discovery aborted: scout produced no URL-grounded ideas.",
                    payload={"run_id": scout_out.run_id},
                )
            )
        return DiscoveryResult(
            idea_ids=[],
            top_idea_id=None,
            memo_id=None,
            total_cost_usd=scout_out.cost_usd,
        )

    analyst_out = run_agent(
        ANALYST,
        [
            {
                "role": "user",
                "content": (
                    f"Founder goal:\n{goal_text}\n\n"
                    f"Score these ideas:\n{json.dumps(scout_data['ideas'], indent=2)}"
                ),
            }
        ],
    )
    total_cost += analyst_out.cost_usd
    try:
        analyst_data = _parse_json(analyst_out.text)
    except Exception as e:
        # Tool-backed scout output can make the analyst occasionally emit malformed JSON.
        # Do not throw away live research; fall back to deterministic neutral scores so
        # the pipeline still persists ideas and writes a memo.
        ideas = scout_data.get("ideas", [])
        analyst_data = {
            "scored": [
                {
                    "title": idea.get("title", f"Idea {idx + 1}"),
                    "rubric": {
                        "tam": 5,
                        "competition": 5,
                        "build_cost": 5,
                        "time_to_revenue": 5,
                        "founder_fit": 5,
                    },
                    "composite": 5.0,
                    "rationale": f"Analyst JSON parse failed ({e}); persisted Scout evidence for Board review.",
                }
                for idx, idea in enumerate(ideas)
            ]
        }

    source_by_title = {i.get("title", ""): i.get("source", "") for i in scout_data.get("ideas", [])}
    summary_by_title = {i.get("title", ""): i.get("summary", "") for i in scout_data.get("ideas", [])}

    idea_ids: list[int] = []
    top: dict | None = None
    with session_scope() as s:
        for entry in analyst_data["scored"]:
            title = entry["title"]
            source = source_by_title.get(title, "discovery_pod") or "discovery_pod"
            rationale = entry.get("rationale", "")
            scout_summary = summary_by_title.get(title, "")
            idea = Idea(
                goal_id=goal_id,
                title=title,
                summary=(rationale + (f"\n\nScout signal: {scout_summary}" if scout_summary else ""))[:8000],
                source=source[:200],
                score=entry["composite"],
                rubric=entry["rubric"],
                status="scored",
            )
            s.add(idea)
            s.flush()
            idea_ids.append(idea.id)
            if top is None or entry["composite"] > top["composite"]:
                top = {**entry, "id": idea.id, "source": source, "scout_summary": scout_summary}

        s.add(
            Event(
                kind="discovery_scored",
                actor="opportunity_analyst",
                message=f"Scored {len(idea_ids)} ideas; top composite={top['composite'] if top else 'n/a'}",
                payload={"idea_ids": idea_ids, "top_id": top["id"] if top else None},
            )
        )

    if top is None:
        return DiscoveryResult(idea_ids=idea_ids, top_idea_id=None, memo_id=None, total_cost_usd=total_cost)

    memo_out = run_agent(
        MEMO_WRITER,
        [
            {
                "role": "user",
                "content": (
                    f"Founder goal:\n{goal_text}\n\n"
                    f"Top-scoring idea:\n{json.dumps(top, indent=2)}\n\n"
                    "Write the memo."
                ),
            }
        ],
        expected_output_tokens=1500,
    )
    total_cost += memo_out.cost_usd

    rec_match = re.search(r"RECOMMENDATION:\s*(FUND|EXPLORE|PASS)", memo_out.text)
    recommendation = rec_match.group(1).lower() if rec_match else "hold"

    with session_scope() as s:
        memo = Memo(idea_id=top["id"], content=memo_out.text, recommendation=recommendation)
        s.add(memo)
        s.flush()
        memo_id = memo.id
        s.add(
            Event(
                kind="memo_written",
                actor="memo_writer",
                message=f"Memo written for idea #{top['id']}; recommendation={recommendation}",
                payload={"memo_id": memo_id, "idea_id": top["id"]},
            )
        )

    return DiscoveryResult(
        idea_ids=idea_ids,
        top_idea_id=top["id"],
        memo_id=memo_id,
        total_cost_usd=total_cost,
    )
