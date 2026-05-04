# Phase 3 build plan

For the hermes coding agent. Build spec, not a deploy runbook — hermes
implements the code, then writes its own short upgrade runbook (in the same
shape as `infra/UPGRADE_PHASE2.md`) before deploying.

**Branch to work on:** `claude/setup-project-architecture-t7WMs` (continue
from current HEAD; do not start a new branch).

**Repo:** `https://github.com/trackandkill/newsoft.git`

---

## Where we are

Phase 1 + 2 are deployed at `https://firm.profithub.me`, fronted by nginx
basicauth (user `founder`). The org runs:

- **Discovery pod** (Scout → Analyst → Memo Writer) on an hourly tick
- **Board pod** (Growth, Operator, Skeptic) every 15 min on pending memos
- **CEO** charters a Venture when the Board votes FUND

First smoke memo got an `EXPLORE` decision. No ventures yet.

Cost on first board run: ~$0.30. At current cadence the system would burn
through the $5/day cap quickly.

## Operator decisions baked into this plan (override if you disagree)

| Question | Default chosen | How to override |
|---|---|---|
| Daily spend cap | raise from $5 → $20 | `DAILY_SPEND_CAP_USD=...` in `/opt/newsoft/.env` |
| Discovery cadence | hourly → every 4 hours | code default in `rituals/scheduler.py` |
| Board cadence | 15 min (unchanged) | code default |
| Web search provider | Brave Search API (free 2k/month) | env-driven; pluggable in `tools/search.py` |
| Validator experiment budget | hard cap $200/experiment | code constant + per-experiment approval |

If the operator hasn't stated a preference at deploy time, hermes uses these.

---

## Scope — six deliverables, in priority order

### 3A. Daily Postgres backup (operational hygiene, must-ship)

Goal: nightly `pg_dump` of the `newsoft` DB, kept for 14 days, no manual ops.

Build:
- `infra/systemd/newsoft-backup.service` — oneshot type, runs as a new
  `newsoft-backup` user (no DB password in env; use `~/.pgpass` for the
  `newsoft` role on `localhost:5432`).
  Command: `pg_dump -Fc -f /var/backups/newsoft/newsoft-$(date +%Y%m%d-%H%M).pgc newsoft`
- `infra/systemd/newsoft-backup.timer` — daily at 03:30 local, persistent.
- Rotation: in the same service script, `find /var/backups/newsoft -name 'newsoft-*.pgc' -mtime +14 -delete`.
- `infra/install.sh` extended to:
  - create `/var/backups/newsoft` (mode 700, owned by `newsoft-backup`)
  - install the service + timer, `systemctl enable --now newsoft-backup.timer`
- Document restore in `infra/BACKUPS.md`: one paragraph + the literal
  `pg_restore -d newsoft <file>` command.

Acceptance:
- `systemctl list-timers | grep newsoft-backup` shows next run time.
- After manual `systemctl start newsoft-backup.service`, a `.pgc` file
  appears in `/var/backups/newsoft/` and is non-empty.
- Old files (mtime > 14d) get deleted (test by `touch -d '15 days ago'`).

### 3B. Cost dashboard panel + cadence tuning (must-ship)

Goal: see what the firm costs at a glance; reduce default activity rate so
the cap doesn't fire on day 1.

Build:
- `orchestrator/orchestrator/api.py`: new endpoint
  `GET /api/costs` returning:
  ```json
  {
    "today": {"total_usd": 0.31, "by_agent": {"market_scout": 0.02, ...}},
    "yesterday": {...},
    "last_7d": {...}
  }
  ```
  Implementation: aggregate `agent_runs.cost_usd` grouped by `agent`, bucketed
  by `started_at::date`. Use `func.date_trunc('day', AgentRun.started_at)`
  in SQLAlchemy.
- `dashboard/app/page.tsx`: extend the overview with a costs card under the
  status pill row. Three columns (today / yesterday / 7-day total) plus a
  small table with per-agent breakdown for "today".
- `orchestrator/orchestrator/rituals/scheduler.py`: change discovery interval
  from `hours=1` to `hours=4`. Leave board at 15 min.

Acceptance:
- `curl /api/costs` returns the three buckets, sums match what's in
  `agent_runs`.
- Overview page shows today's total = sum of per-agent breakdown.
- `systemctl status newsoft-orchestrator` shows discovery_tick scheduled
  4h apart.

### 3C. Per-agent-run inspector (must-ship; data already exists)

Goal: click any event in `/events` and see the full prompt, response, tool
calls, tokens, and cost for the underlying agent run.

Build:
- `orchestrator/orchestrator/api.py`: `GET /api/agent_runs/{id}` returning
  the full `AgentRun` row (including `system_prompt`, `input_messages`,
  `output_text`, `tool_calls`).
- `dashboard/app/events/page.tsx`: each row whose `payload.run_id` is set
  becomes clickable; clicking opens a side drawer (or just navigates to
  `/runs/[id]`).
- `dashboard/app/runs/[id]/page.tsx`: shows the run with sections for
  System / Messages / Output / Tool calls / Stats (model, tokens, cost,
  duration). Pre-formatted, not pretty — this is a debugging surface.

Acceptance:
- From the events page, clicking a `board_vote` event reaches a page that
  shows the partner's full system prompt, the memo+idea blob it received,
  and its raw JSON output.

### 3D. Web search tool (behavioral; gives Scout real eyes)

Goal: Scout calls `web_search(query)` and `fetch_url(url)` instead of
hallucinating from priors.

Build:
- `orchestrator/orchestrator/tools/__init__.py` — empty.
- `orchestrator/orchestrator/tools/search.py` — Brave Search adapter:
  ```python
  def web_search(query: str, count: int = 8) -> list[dict]:
      """Returns [{title, url, snippet}]."""
  def fetch_url(url: str, max_chars: int = 5000) -> str:
      """Returns plain-text body, truncated."""
  ```
  - Brave API key from `BRAVE_API_KEY` env var (add to `.env.example`).
  - If unset, both functions raise `ToolUnavailable` and the Scout falls
    back to its priors-only mode (current behavior).
  - `fetch_url`: use `httpx`, follow redirects, strip HTML with the stdlib
    `html.parser` or a tiny regex; never execute JS. Hard timeout 10s.
- `orchestrator/orchestrator/runtime.py`: add a tool dispatch loop. When the
  model returns `tool_use` blocks, look them up by name in a passed-in
  registry, execute, append `tool_result` blocks to `messages`, call the
  model again. Cap at 4 turns. Log every `ToolCall` to the existing
  `tool_calls` table (`dry_run=false` since search/fetch don't move money).
- Define the Anthropic tool schemas in `tools/search.py`:
  ```python
  TOOLS = [
      {"name": "web_search", "description": "...", "input_schema": {...}},
      {"name": "fetch_url",  "description": "...", "input_schema": {...}},
  ]
  ```
- `orchestrator/orchestrator/agents/discovery.py`: pass `TOOLS` into the
  Scout's `AgentSpec.tools`. Keep its prompt mostly the same but add:
  *"Use web_search to find recent signals (Reddit threads, Indie Hackers,
  ProductHunt, GitHub issues, niche forums). fetch_url for any URL whose
  snippet looks promising. Cite at least one URL per idea."*

Acceptance:
- With `BRAVE_API_KEY` set, a discovery run produces ideas whose
  `source` field references real URLs (visible in `/ideas`).
- Without `BRAVE_API_KEY` set, discovery still works (no crash; falls back).
- `tool_calls` table has rows with `tool='web_search'` after a run.

Risks:
- Brave free tier: 2k/month, ~1 req/sec. Discovery uses ≤ 5 web_search +
  ≤ 5 fetch_url per run. At every-4-hours cadence that's ~60 calls/day.
  Within free tier with margin.
- Don't recurse: a `fetch_url` result must NOT contain another `tool_use`
  re-fetch loop. Cap at 4 model turns total.

### 3E. Validator pod for EXPLORE memos (behavioral)

Goal: `EXPLORE` is no longer a dead end. Validator designs a cheap experiment,
files it as an `Approval`, you click approve, the experiment runs (still
mostly LLM work in this phase — a real ad probe needs Phase 4 money tools),
and results feed back to the Board.

Build:
- `orchestrator/orchestrator/agents/validator.py`:
  - `design_experiment(memo_id) -> Approval` — Validator (Sonnet) reads the
    memo, designs a sub-$200 experiment with: hypothesis, method, success
    metric, deadline, estimated cost. Files an `Approval` row with
    `action='run_experiment'`, `payload={memo_id, design_json}`.
  - `run_experiment(approval_id)` — when the approval is accepted, runs
    whatever tooling exists (Phase 3: only the `web_search`/`fetch_url`
    tools — e.g., scrape competitor pricing, validate channel reachability).
    Writes results back to a new `experiments` table.
- `orchestrator/orchestrator/db/models.py` — new `Experiment` table:
  ```
  id, memo_id, approval_id, design_json (JSON), status (designed|running|done|failed),
  result_md (Text), cost_usd (Float), created_at, completed_at
  ```
  Add an idempotent column migration if any existing table is touched
  (none expected).
- Approval flow extension: `orchestrator/orchestrator/api.py`
  - When `POST /api/approvals/{id}` decides `approve` and the action is
    `run_experiment`, hand off to `validator.run_experiment(id)` in a
    background task.
- `orchestrator/orchestrator/rituals/scheduler.py`:
  - new `validator_tick` every 30 min: find any memo with
    `decision='explore'` and no Experiment row, run `design_experiment`
    (which files the Approval — does NOT auto-run).
- After an experiment completes, write a `validation_report` Event and
  flip the memo back to `decision='pending'`. The Board will pick it up
  on the next board_tick and re-vote with the experiment results in
  context. (Update Board prompt: include experiment results if present.)

Acceptance:
- Existing memo #1 (decision EXPLORE) gets an experiment design filed as
  an Approval within one validator_tick.
- Approving the experiment runs it and writes an `Experiment` row with
  `status='done'` and a populated `result_md`.
- The memo's decision flips to `pending` and the Board re-votes with the
  experiment context.

Risks / constraints:
- Hard cap: a single experiment may not spend more than $0.50 in agent
  costs (separate from the $200 spend cap, which is for real-world spend
  and is dry-run only in Phase 3). Enforce in `validator.run_experiment`
  by accumulating `agent_run.cost_usd` and aborting if exceeded.
- Don't auto-run experiments without operator approval. The Approval gate
  is the entire point.

### 3F. Re-engage system + sanity check after deploy

Goal: deploy ends with a controlled re-activation, not the kill switch on.

Build (in the upgrade runbook hermes writes, not in code):
- After all migrations + restarts succeed, hermes runs:
  ```bash
  curl -fsS -u "founder:${DASHBOARD_PASSWORD}" -X POST \
      https://firm.profithub.me/api/system \
      -H 'content-type: application/json' \
      -d '{"active":true,"daily_spend_cap_usd":20.0}'
  ```
- Trigger one board run + one validator run for smoke testing.
- Final report includes: backup timer next-fire time, today's cost, # of
  experiments designed, # of approvals waiting.

---

## Cross-cutting requirements

### Migrations
- All schema additions go through the same idempotent path used in Phase 2
  (`init_db.py::_COLUMN_MIGRATIONS` for new columns; `Base.metadata.create_all`
  for new tables). Any new column must use `ADD COLUMN IF NOT EXISTS`.

### Env vars added in Phase 3
| Var | Default | Required? |
|---|---|---|
| `BRAVE_API_KEY` | (unset) | optional; without it Scout falls back to priors |
| `DAILY_SPEND_CAP_USD` | `5.0` (existing) | hermes raises to `20.0` if operator hasn't set it |

### Module layout (additions only)
```
orchestrator/orchestrator/
  agents/validator.py          (new)
  tools/__init__.py            (new)
  tools/search.py              (new)
  tools/registry.py            (new — maps tool name -> callable + schema)
  db/models.py                 (extend with Experiment)
  rituals/scheduler.py         (extend with validator_tick; tune cadence)
  runtime.py                   (extend with tool dispatch loop)
  api.py                       (extend with /api/costs, /api/agent_runs/{id})
dashboard/
  app/page.tsx                 (extend with costs panel)
  app/runs/[id]/page.tsx       (new)
  app/events/page.tsx          (extend rows to be clickable)
  lib/api.ts                   (extend)
infra/
  systemd/newsoft-backup.service  (new)
  systemd/newsoft-backup.timer    (new)
  install.sh                      (extend with backup setup)
  BACKUPS.md                      (new)
  UPGRADE_PHASE3.md               (new — hermes writes this)
```

### Things hermes should NOT do
- Do not change the existing memo/board/CEO prompts beyond the small Scout
  update for tool use and the small Board update for experiment results.
- Do not add Alembic. The idempotent ALTER pattern is fine for our scale.
- Do not switch reverse proxy. nginx stays.
- Do not introduce real-money tools. Save Stripe/ad APIs for Phase 4.
- Do not log `BRAVE_API_KEY`, `ANTHROPIC_API_KEY`, or
  `NEWSOFT_BASIC_HASH`.
- Do not exceed Brave's free-tier rate limits — if discovery cadence is
  raised in the future, consider rate-limiting the search adapter.

### What hermes should produce alongside the code
- A `infra/UPGRADE_PHASE3.md` runbook in the same shape as
  `infra/UPGRADE_PHASE2.md`: preconditions, step-by-step deploy with
  verifications, smoke test, rollback recipe, final report template.
- One commit per logical chunk (3A, 3B, ...) is preferred but not required;
  one big commit is also acceptable. Either way: clear messages.
- Push to the existing branch when complete.

### Acceptance for Phase 3 as a whole
After hermes deploys and runs the smoke checks:
- `systemctl list-timers` shows `newsoft-backup.timer` upcoming.
- One non-empty `.pgc` backup file exists in `/var/backups/newsoft/`.
- `/api/costs` returns three buckets that sum correctly.
- `/runs/<id>` shows full prompt + response for a real recent run.
- A discovery run with `BRAVE_API_KEY` set produces ideas whose summary
  text references at least one real URL.
- Memo #1's decision is back to `pending` (Validator designed an
  experiment, operator approved, experiment ran, Board re-voting).
- System ends ACTIVE with cap raised to $20 (or whatever the operator
  set).

---

## Final report hermes should produce

| Section | Content |
|---|---|
| Code | latest commit hash; one-line list of pieces shipped (3A/3B/3C/3D/3E/3F) |
| Migrations | confirm `experiments` table created; no `ProgrammingError` in orchestrator log |
| Env | which vars added; whether `BRAVE_API_KEY` was provided |
| Backups | next-fire time of timer; size of first backup |
| Costs | today, yesterday, 7d totals from `/api/costs` |
| Smoke | # of agent_runs in last hour; # of experiments designed; # of approvals waiting |
| State | system active? cap value? dry_run? |
| Issues | any non-fatal warnings or things the operator should know |

End of plan.
