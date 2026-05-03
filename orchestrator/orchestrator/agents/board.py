"""Board pod. Three partners with diverse priors review a memo and vote.

Phase 2: outputs strict JSON {vote, rationale}. Decisions: simple majority
across {fund, explore, pass}. Ties resolve to the more conservative option
(pass > explore > fund as the conservative direction when tied).
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from orchestrator.config import settings
from orchestrator.db.models import BoardReview, Event, Idea, Memo
from orchestrator.db.session import session_scope
from orchestrator.runtime import AgentSpec, run_agent


def _board_spec(persona: str, prior: str) -> AgentSpec:
    return AgentSpec(
        name=f"board_{persona}",
        role=f"Board Partner ({persona})",
        model=settings.model_opus,
        max_tokens=900,
        system_prompt=(
            f"You are the {persona.title()} Partner on the Board of an autonomous "
            f"venture firm. Your prior: {prior} "
            "Read the memo and vote one of: fund, explore, pass. "
            "Be specific in your rationale: cite the memo's strongest point, its "
            "weakest point, and the single experiment that would change your mind. "
            'Return STRICT JSON: {"vote":"fund|explore|pass","rationale":"..."}. '
            "No prose outside the JSON."
        ),
    )


BOARD = [
    _board_spec(
        "growth",
        "Bias toward TAM and channel scale. You want ventures that can plausibly "
        "reach $10k MRR within 12 months. Discount ideas with thin top-of-funnel.",
    ),
    _board_spec(
        "operator",
        "Bias toward execution feasibility. You want crisp ICP, short time-to-revenue, "
        "and a first experiment that can run for under $200. Discount vague GTM plans.",
    ),
    _board_spec(
        "skeptic",
        "Bias toward risk and kill criteria. You assume most ideas fail. Discount "
        "anything with strong incumbents, regulatory exposure, or thin defensibility. "
        "You vote 'pass' unless the memo names credible kill criteria.",
    ),
]

# Priority order when there's a tie or split vote. We weight 'pass' first
# (most conservative), then 'explore', then 'fund'.
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
    """Simple majority across {fund, explore, pass}; tie-breaks conservative."""
    counts = Counter(votes)
    top = counts.most_common()
    if len(top) == 1:
        return top[0][0]
    if top[0][1] > top[1][1]:
        return top[0][0]
    # Tie: pick the most conservative tied option.
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
        memo_content = memo.content
        idea_blob = {
            "title": idea.title if idea else "?",
            "score": idea.score,
            "rubric": idea.rubric,
            "summary": idea.summary,
        }

    # Skip personas that have already voted on this memo.
    with session_scope() as s:
        existing = s.scalars(
            select(BoardReview).where(BoardReview.memo_id == memo_id)
        ).all()
        already = {r.persona for r in existing}
        existing_votes = {r.persona: r.vote for r in existing}

    user_msg = (
        f"Idea details:\n{json.dumps(idea_blob, indent=2)}\n\n"
        f"Memo:\n{memo_content}"
    )

    cost = 0.0
    votes: dict[str, str] = dict(existing_votes)
    for spec in BOARD:
        persona = spec.name.replace("board_", "")
        if persona in already:
            continue
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
                    payload={"memo_id": memo_id, "vote": vote},
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
