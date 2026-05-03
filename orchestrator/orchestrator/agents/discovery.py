"""Discovery pod: Market Scout -> Opportunity Analyst -> Memo Writer.

Phase 1 implementation: no live web access yet. Scout ideates from priors
and the founder's goal. Analyst scores against a fixed rubric. Memo Writer
produces an investment memo for the highest-scoring idea.
"""

import json
import re
from dataclasses import dataclass

from orchestrator.config import settings
from orchestrator.db.models import Event, Idea, Memo
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent

SCOUT = AgentSpec(
    name="market_scout",
    role="Market Scout",
    model=settings.model_sonnet,
    max_tokens=2000,
    system_prompt=(
        "You are the Market Scout for an autonomous venture firm. "
        "Given a founder goal, propose candidate small-business / SaaS ideas "
        "the firm could pursue. Favor ideas with: small initial capex, fast "
        "time-to-revenue, narrow ICP, and at least one channel that can be "
        "tested with under $200. Return STRICT JSON: "
        '{"ideas":[{"title":"...","summary":"...","source":"prior knowledge"}]}. '
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


def run_discovery(goal_id: int, goal_text: str) -> DiscoveryResult:
    total_cost = 0.0

    scout_out = run_agent(
        SCOUT,
        [{"role": "user", "content": f"Founder goal:\n{goal_text}\n\nPropose 5 candidate ideas."}],
    )
    total_cost += scout_out.cost_usd
    scout_data = _parse_json(scout_out.text)

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
    analyst_data = _parse_json(analyst_out.text)

    idea_ids: list[int] = []
    top: dict | None = None
    with session_scope() as s:
        for entry in analyst_data["scored"]:
            idea = Idea(
                goal_id=goal_id,
                title=entry["title"],
                summary=entry.get("rationale", ""),
                source="discovery_pod",
                score=entry["composite"],
                rubric=entry["rubric"],
                status="scored",
            )
            s.add(idea)
            s.flush()
            idea_ids.append(idea.id)
            if top is None or entry["composite"] > top["composite"]:
                top = {**entry, "id": idea.id}

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
