"""Board pod. Three partners with diverse priors review a memo and vote."""

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import BoardReview, Event, Experiment, Idea, Memo
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent
from orchestrator.tools.memory import TOOLS as MEMORY_TOOLS


def _board_spec(persona: str, prior: str) -> AgentSpec:
    return AgentSpec(
        name=f"board_{persona}",
        role=f"Board Partner ({persona})",
        model=settings.model_opus,
        max_tokens=1000,
        tools=MEMORY_TOOLS,
        system_prompt=(
            f"You are the {persona.title()} Partner on the Board of an autonomous "
            f"venture firm. Your prior: {prior} "
            "Read the memo and any validation experiment results, then vote one of: fund, explore, pass. "
            "Before making the judgment, call search_memory once for related lessons or prior decisions; if it returns empty, continue. "
            "Be specific in your rationale: cite the strongest point, weakest point, "
            "and the single experiment/result that would change your mind. "
            'Return STRICT JSON: {"vote":"fund|explore|pass","rationale":"..."}. '
            "No prose outside the JSON."
        ),
    )


BOARD = [
    _board_spec(
        "growth",
        "Bias toward TAM and channel scale. You want ventures that can plausibly reach $10k MRR within 12 months. Discount ideas with thin top-of-funnel.",
    ),
    _board_spec(
        "operator",
        "Bias toward execution feasibility. You want crisp ICP, short time-to-revenue, and a first experiment that can run for under $200. Discount vague GTM plans.",
    ),
    _board_spec(
        "skeptic",
        "Bias toward risk and kill criteria. You assume most ideas fail. Discount anything with strong incumbents, regulatory exposure, or thin defensibility. You vote 'pass' unless credible kill criteria and evidence exist.",
    ),
]

_CONSERVATIVE_ORDER = ["pass", "explore", "fund"]


@dataclass
class BoardOutcome:
    memo_id: int
    decision: str
    votes: dict[str, str]
    cost_usd: float


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in board agent output: {text[:200]}")
    return json.loads(match.group(0))


def _aggregate(votes: list[str]) -> str:
    counts = Counter(votes)
    top = counts.most_common()
    if len(top) == 1:
        return top[0][0]
    if top[0][1] > top[1][1]:
        return top[0][0]
    tied_at_top = [v for v, c in top if c == top[0][1]]
    for choice in _CONSERVATIVE_ORDER:
        if choice in tied_at_top:
            return choice
    return tied_at_top[0]


def review_memo(memo_id: int) -> BoardOutcome:
    """Run all three Board partners on the memo, aggregate, write decision."""
    with session_scope() as s:
        memo = s.get(Memo, memo_id)
        if memo is None:
            raise ValueError(f"Memo {memo_id} not found")
        idea = s.get(Idea, memo.idea_id)
        experiments = s.scalars(
            select(Experiment).where(Experiment.memo_id == memo_id).order_by(Experiment.created_at.asc())
        ).all()
        memo_content = memo.content
        idea_blob = {
            "title": idea.title if idea else "?",
            "score": idea.score if idea else None,
            "rubric": idea.rubric if idea else None,
            "summary": idea.summary if idea else "",
            "source": idea.source if idea else "",
        }
        experiment_blob = [
            {
                "id": e.id,
                "status": e.status,
                "design": e.design_json,
                "result": e.result_md,
                "cost_usd": e.cost_usd,
                "completed_at": e.completed_at.isoformat() if e.completed_at else None,
            }
            for e in experiments
        ]

    user_msg = (
        f"Idea details:\n{json.dumps(idea_blob, indent=2)}\n\n"
        f"Memo:\n{memo_content}\n\n"
        f"Validation experiments/results:\n{json.dumps(experiment_blob, indent=2)}"
    )

    cost = 0.0
    votes: dict[str, str] = {}
    for spec in BOARD:
        persona = spec.name.replace("board_", "")
        out = run_agent(spec, [{"role": "user", "content": user_msg}])
        cost += out.cost_usd
        try:
            data = _parse_json(out.text)
            vote = data["vote"].strip().lower()
            rationale = data.get("rationale", "")
            if vote not in {"fund", "explore", "pass"}:
                vote = "explore"
        except Exception as e:
            vote, rationale = "explore", f"Parse error: {e}; raw={out.text[:200]}"
        votes[persona] = vote
        with session_scope() as s:
            s.add(
                BoardReview(
                    memo_id=memo_id,
                    persona=persona,
                    vote=vote,
                    rationale=rationale,
                    agent_run_id=out.run_id,
                )
            )
            s.add(
                Event(
                    kind="board_vote",
                    actor=spec.name,
                    message=f"Vote on memo #{memo_id}: {vote.upper()}",
                    payload={"memo_id": memo_id, "vote": vote, "run_id": out.run_id, "agent_run_id": out.run_id},
                )
            )

    decision = _aggregate(list(votes.values()))
    with session_scope() as s:
        memo = s.get(Memo, memo_id)
        memo.decision = decision
        memo.decision_at = datetime.now(timezone.utc)
        s.add(
            Event(
                kind="board_decision",
                actor="board",
                message=f"Memo #{memo_id} decision: {decision.upper()} (votes: {votes})",
                payload={"memo_id": memo_id, "decision": decision, "votes": votes},
            )
        )

    return BoardOutcome(memo_id=memo_id, decision=decision, votes=votes, cost_usd=cost)
