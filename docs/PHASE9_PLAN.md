# Phase 9 build plan

For the hermes coding agent. Build spec — hermes implements, deploys,
writes its own short upgrade runbook (`infra/UPGRADE_PHASE9.md`), and
pushes to origin.

**Branch:** `claude/setup-project-architecture-t7WMs` (continue from HEAD).
**Repo:** `https://github.com/trackandkill/newsoft.git`

---

## Where we are

After Phase 8:
- Discovery → Board → Validator (3-stage gated) → CEO → CTO + Engineer
  pod → Copywriter → Site deploy pipeline → Outreach (production-grade
  safety rails) — every individual capability needed to actually run
  ventures is now built.
- Vector memory wired into key agents.
- Per-approval execute_live override; sticky global dry_run.
- Full audit log; backups daily.
- One seed venture; no live deployments yet.

Phase 9 is about **operational discipline for running more than one
venture at a time**, plus closing the kill-loop teardown gap, plus
giving agents an open-ended channel to ask the operator clarifying
questions (so we stop forcing every agent thought into a binary
approval).

What forces this now: every cap in the system is global
(`daily_spend_cap_usd`, `money_daily_cap_usd`). The moment two
ventures run in parallel, one runaway can starve the other. Per-
venture budgets fix that. The kill loop currently flips
`venture.status = killed` but leaves the live site, DNS records,
and (eventually) cert renewal in place forever — auto-teardown
finishes the loop. And clarifying questions remove a real friction
point (today the operator sees agents file weird "explore" votes
that mean "I'm uncertain" — better to let them just ask).

## Operator decisions baked into this plan

| Question | Default | Override |
|---|---|---|
| Default per-venture LLM cap (daily) | $5/day | per-venture column override |
| Default per-venture money cap (daily) | $10/day | per-venture column override |
| Default per-venture money cap (lifetime) | $50 | per-venture column override |
| Auto-teardown grace period | 24 hours after `status='killed'` (operator can revoke during window) | code default |
| Clarification question timeout behavior | agents proceed without an answer; the question stays open in the inbox until answered | hard rule — never block agent runs on operator |
| Clarification answers indexed into vector memory | yes (so future agents see them) | always |

## New operator inputs

None. Phase 9 reuses existing env. New behavior is per-venture column
overrides + a new approval action (`teardown_site`), both visible in
the dashboard.

---

## Scope — three deliverables

### 9A. Per-venture budgets (medium, must-ship)

Goal: each venture has its own daily LLM cap, daily money cap, and
lifetime money cap. Spend is counted per venture. A runaway one
venture cannot starve the others.

#### Data model
Add columns to `ventures` (idempotent migration):
- `daily_llm_cap_usd FLOAT NOT NULL DEFAULT 5.0`
- `daily_money_cap_usd FLOAT NOT NULL DEFAULT 25.0`
- `total_money_cap_usd FLOAT NOT NULL DEFAULT 50.0`
- `llm_spend_today_usd FLOAT NOT NULL DEFAULT 0.0`
- `money_spend_today_usd FLOAT NOT NULL DEFAULT 0.0`
- `money_spend_lifetime_usd FLOAT NOT NULL DEFAULT 0.0`
- `spend_day TIMESTAMPTZ NOT NULL DEFAULT now()`

Add nullable column to `agent_runs`:
- `venture_id INT REFERENCES ventures(id)`

Add nullable column to `money_transactions`:
- `venture_id INT REFERENCES ventures(id)`

(Old rows retain `venture_id = NULL`; only firm-level work is naturally
nullable.)

#### Context propagation
`runtime.run_agent` gains an optional kwarg `venture_id: int | None = None`.
- When set: enforces the venture's caps in addition to global caps.
  Records spend against the venture row's `llm_spend_today_usd` (and
  appears on the agent_run row).
- When unset: existing global-only behavior.

Each agent invocation site decides whether to pass `venture_id`:
- Discovery, Board, CEO charter — no venture context (firm-level work).
- Validator stages — pass `venture_id` if a Venture exists for the
  memo; otherwise leave unset.
- CTO `draft_plan(venture_id)` — pass the venture_id.
- Engineer `break_down_first_30(plan_id)` — look up `plan.venture_id`,
  pass it.
- Copywriter `draft_landing_page(venture_id, ...)` — pass it.
- Postmortem writer — pass it.
- Kill evaluator — pass it.

Same for `money_transactions`: the dispatcher in `api.py` looks up
the action's payload, finds the related venture (via
`payload.venture_id`, `payload.site_id → site.venture_id`,
`payload.experiment_id → experiment.memo_id → memo... → venture` —
hermes picks the cleanest lookup chain), and stamps `venture_id` on
the transaction row before enforcing caps.

#### Cap enforcement
Add to `orchestrator/budget.py`:
```python
class VentureBudgetExceeded(BudgetExceeded):
    pass

def reserve_venture_llm(s, venture_id, est_usd):
    v = s.get(Venture, venture_id)
    if v is None:
        return
    _rollover_venture_if_new_day(v)
    if v.llm_spend_today_usd + est_usd > v.daily_llm_cap_usd:
        raise VentureBudgetExceeded(
            f"Venture {v.slug}: daily LLM cap ${v.daily_llm_cap_usd:.2f} "
            f"would be exceeded (today: ${v.llm_spend_today_usd:.4f}, "
            f"requested: ${est_usd:.4f})"
        )

def record_venture_llm_spend(s, venture_id, actual_usd):
    v = s.get(Venture, venture_id)
    if v is None:
        return
    _rollover_venture_if_new_day(v)
    v.llm_spend_today_usd += actual_usd
```

Same shape for `reserve_venture_money` / `record_venture_money_spend`,
which also enforces lifetime cap.

`runtime.run_agent` calls `reserve_venture_llm` before
`reserve_budget` (global) and records both after success.

`api.decide_approval` (and the per-action dispatchers) call
`reserve_venture_money` before any `MoneyTransaction.status='done'`.

#### API
- `POST /api/ventures/{slug}/budget` — body
  `{daily_llm_cap_usd?, daily_money_cap_usd?, total_money_cap_usd?}` —
  operator overrides. Basic-auth required.
- `GET /api/ventures/{slug}` — extend response to include all six
  budget fields.

#### Dashboard
- `dashboard/app/ventures/[slug]/page.tsx` — add a "Budgets" card
  showing all three caps, current day's spend, lifetime money spent,
  and per-cap utilization pills (green / yellow >70% / red >90%).
  Inline edit form for the operator to bump caps.
- `dashboard/app/ventures/page.tsx` — add a "Spend today" column.

#### Acceptance
- A new venture defaults to $5/$10/$50 caps and $0 spend.
- Bumping `daily_llm_cap_usd` via the API immediately reflects in
  cap enforcement (without restart).
- Forcing an agent run against a venture with `daily_llm_cap_usd=0.01`
  raises `VentureBudgetExceeded` cleanly.
- `agent_runs` and `money_transactions` rows for venture-scoped
  activity have `venture_id` populated.
- Existing/seed venture's pre-Phase-9 history is unaffected; backfill
  is not required (NULL venture_id on old rows is fine).

### 9B. Site auto-teardown for killed ventures (small, must-ship)

Goal: when `venture.status='killed'` (Phase 6 kill loop), the firm
files an approval to tear down the venture's live site after a
24-hour grace period. Operator can approve, reject (cancel
teardown), or extend the grace.

#### Trigger
New ritual `teardown_tick` (every 1 hour):
- For every venture with `status='killed'` and `killed_at` ≥ 24h ago
  AND a non-torn-down Site:
  - If a `teardown_site` Approval already exists for this venture:
    skip.
  - Else: file Approval `action='teardown_site'`,
    `payload={venture_id, site_id, slug, domain}`,
    `requested_by='kill_loop'`,
    `rationale='Venture killed at <ts>; tearing down live site after 24h grace.'`

Add to APScheduler.

#### Action handler
`POST /api/approvals/{id}` for `teardown_site` →
`tools.sites.execute_teardown(approval_id)`:
- Pre-flight: confirm venture still in `killed` status (operator may
  have reversed it).
- DELETE Porkbun DNS records for the domain (root + www).
  Use existing `tools/domains.py::delete_dns_record`. Skip if
  Porkbun keys unset; log a warning event.
- REMOVE nginx vhost: `rm /etc/nginx/newsoft-sites/<slug>.conf`,
  then `sudo /usr/local/sbin/newsoft-nginx-reload`.
- ARCHIVE site files:
  `mv /var/lib/newsoft/sites/<slug> /var/lib/newsoft/sites/.archived/<slug>-<ts>`
  (don't delete; cheap insurance against premature teardowns).
- DO NOT touch the cert. Let it expire on its own. (Adds load to
  certbot if we revoke; not worth it.)
- Set `site.status='torn_down'`, `site.deployed_at` unchanged.
- Write Event `kind='site_torn_down'`.

In dry-run / `execute_live=false`: simulate all of the above; write
`Event(kind='site_torn_down_simulated')`; do not modify nginx or
Porkbun or filesystem.

#### Operator override
- `POST /api/ventures/{slug}/revive` — set `status='chartered'`,
  `killed_at=NULL`, `kill_reason=NULL`. Cancels any pending
  `teardown_site` approval (sets it to `status='cancelled'`).
- `POST /api/ventures/{slug}/extend_grace` — body `{hours: int}` —
  shifts `killed_at` forward by N hours so teardown_tick re-evaluates
  later. Logs an Event.

#### Dashboard
- On `/ventures/[slug]` for killed ventures: show a "Pending
  teardown in <Xh>" banner with revive + extend buttons.
- `/approvals` already renders the `teardown_site` row generically;
  no special-case needed beyond the existing approval card.

#### Acceptance
- Kill the seed venture (postmortem path from Phase 6 already works).
  Set `killed_at` to >24h ago manually for the smoke. Run
  `teardown_tick`. A `teardown_site` approval appears.
- Approving in dry-run: simulated teardown event; no real changes.
- Approving in execute_live=true (with Porkbun keys set): real
  teardown happens; nginx vhost gone; archived dir present.
- Reviving a killed venture before teardown approval cancels it.
- After test: revive seed venture so Phase 10 has clean state.

### 9C. Operator clarifying-question inbox (small, must-ship)

Goal: agents can file open-ended questions for the operator without
forcing the question into a yes/no Approval. Answers are persisted
and indexed into vector memory so future agent runs benefit.

#### Data model
New table `clarifications`:
```
id BIGSERIAL PRIMARY KEY,
asked_by_agent VARCHAR(80) NOT NULL,
agent_run_id INT REFERENCES agent_runs(id),
venture_id INT REFERENCES ventures(id),
memo_id INT REFERENCES memos(id),
question_md TEXT NOT NULL,
context_md TEXT,                  -- short context block from agent
priority VARCHAR(20) DEFAULT 'normal',   -- low / normal / high
status VARCHAR(20) NOT NULL DEFAULT 'open',  -- open / answered / dismissed
answer_md TEXT,
answered_by VARCHAR(80),
answered_at TIMESTAMPTZ,
created_at TIMESTAMPTZ DEFAULT now()
```

#### Tool
New tool exposed to agents:
```python
{
  "name": "ask_operator",
  "description": "Open-ended question for the operator. NOT for "
                 "approve/reject decisions (use the approval queue "
                 "for those). Use when uncertain and a short text "
                 "answer would unblock you. The answer is async; "
                 "do NOT wait for it. Future agent runs will see "
                 "the answer via search_memory.",
  "input_schema": {
    "type": "object",
    "properties": {
      "question": {"type": "string"},
      "context": {"type": "string"},
      "priority": {"type": "string", "enum": ["low", "normal", "high"]}
    },
    "required": ["question"]
  }
}
```
Implementation: writes a `Clarification` row, returns
`{clarification_id, status: "open"}`. Always succeeds (or graceful
errors); never blocks the agent.

Wire `ask_operator` into the same agents that already have
`search_memory`: Board, CEO, Validator (all stages), CTO,
Postmortem.

#### API
- `GET /api/clarifications?status=open` — list open clarifications.
- `POST /api/clarifications/{id}/answer` — body
  `{answer: str, answered_by?: str = "founder"}`. Sets
  `status='answered'`, `answered_at=now()`, fires Event
  `kind='clarification_answered'`. Triggers `memory.reindex_source`
  for `kind='clarification', id=<answer's id>`.
- `POST /api/clarifications/{id}/dismiss` — operator marks the
  question dismissed without answering (e.g., agent should figure
  it out).

#### Memory integration
Add `clarification` to the allowed `source_kind` values in Phase 7's
memory.py. When indexed, the chunk text is
`Q: {question_md}\nA: {answer_md}` so search_memory returns useful
content.

#### Dashboard
- `dashboard/app/clarifications/page.tsx` — list of open and recent
  clarifications. Inline answer textarea per row.
- Add nav link.
- On `/ventures/[slug]`: show count of open clarifications scoped to
  that venture; deep-link to `/clarifications?venture_id=N`.

#### Acceptance
- A test call to `ask_operator(question="...")` from an agent
  context creates a Clarification row with `status='open'`.
- Dashboard `/clarifications` shows it; submitting an answer flips
  the row and triggers memory reindex.
- A subsequent `search_memory("...")` returns the Q+A chunk.
- Agent runs are not blocked by unanswered clarifications.

---

## Cross-cutting requirements

### Migrations
- `ventures` columns: 7 new (idempotent ALTER).
- `agent_runs.venture_id` (idempotent ALTER, INT NULL FK).
- `money_transactions.venture_id` (idempotent ALTER, INT NULL FK).
- `clarifications` table — `Base.metadata.create_all`.
- No destructive changes; existing rows keep `venture_id = NULL` and
  legacy behavior.

### Env vars added in Phase 9
None.

### Module layout (additions only)
```
orchestrator/orchestrator/
  budget.py                    (extend with venture cap helpers)
  runtime.py                   (extend with venture_id kwarg)
  api.py                       (extend endpoints; teardown dispatch;
                                clarification endpoints)
  rituals/scheduler.py         (extend with teardown_tick)
  tools/
    sites.py                   (extend with execute_teardown)
    operator.py                (new — ask_operator tool)
  memory.py                    (extend allowed kinds)
  db/models.py                 (extend Venture, AgentRun, MoneyTransaction;
                                new Clarification)
  db/init_db.py                (extend with new ALTERs + table creation)
  agents/                      (each agent invocation site updated to
                                pass venture_id where applicable; tool
                                lists extended with ask_operator where
                                they already include search_memory)
dashboard/
  app/ventures/[slug]/page.tsx (extend with Budgets card + revive/
                                extend buttons + clarifications count)
  app/ventures/page.tsx        (extend with spend column)
  app/clarifications/page.tsx  (new)
  app/layout.tsx               (add nav link)
  lib/api.ts                   (extend)
infra/
  UPGRADE_PHASE9.md            (new — hermes writes this)
docs/
  PHASE9_PLAN.md               (this file)
```

### Things hermes should NOT do
- Do NOT block agent execution on a clarification answer. Async,
  always.
- Do NOT auto-teardown inside the kill loop itself. The 24-hour
  grace is on purpose; the operator must approve the teardown.
- Do NOT lower any existing global cap. Per-venture caps are
  additive, not replacements.
- Do NOT propagate `venture_id` to firm-level agents (Discovery,
  Board, CEO charter). They run on the firm's account, not a venture.
- Do NOT delete venture data (postmortems, plans, tasks, leads,
  outreach_sends, agent_runs) on teardown. Only the live site
  artifacts (DNS, nginx vhost, files) are removed.
- Do NOT remove certs on teardown. Let them expire.
- Do NOT migrate to React 19.
- Do NOT add a second money tool.
- Do NOT log raw operator answers to clarifications outside the
  `clarifications` table and `memory_embeddings` (i.e., don't echo
  to systemd journal).

### Acceptance for Phase 9 as a whole
- Code: pushed; new commit hash on origin.
- Migrations: 7 ventures columns, 1 column on agent_runs, 1 column
  on money_transactions, clarifications table all present.
- Per-venture caps: a forced over-cap test on the seed venture
  raises `VentureBudgetExceeded` cleanly without affecting global
  state.
- Teardown: seed venture (after Phase 6 smoke kills it again, then
  manually backdating `killed_at`) gets a `teardown_site` approval
  filed by `teardown_tick`. Approving in dry-run produces a
  simulated teardown event. Reviving cancels.
- Clarification: a manual SQL insert + agent reading via memory
  search proves the round trip; OR hermes adds a small
  `/api/clarifications/test` endpoint for the smoke (and removes it
  before final commit; OR keeps it gated behind `dry_run=true`).
- Reset state for Phase 10: revive the seed venture; delete any
  test clarifications.
- System ends ACTIVE, dry-run, $20 LLM cap, $50 money cap,
  per-venture defaults at $5/$10/$50.

---

## Final report from hermes

| Section | Content |
|---|---|
| Code | latest commit hash; deliverables shipped (9A, 9B, 9C) |
| Migrations | confirm columns + table |
| Per-venture caps | proven by forced over-cap test; venture_id stamping verified on a fresh agent run |
| Teardown | approval filed by teardown_tick; dry-run simulated event; revive flow tested |
| Clarification | round trip Q→A→memory verified |
| State | system active? caps? dry-run? today's spend (LLM and money) |
| Issues | non-fatal warnings, things deferred to Phase 10 |

End of plan.
