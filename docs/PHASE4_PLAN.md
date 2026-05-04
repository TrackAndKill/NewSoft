# Phase 4 build plan

For the hermes coding agent. Build spec — hermes implements, deploys,
writes its own short upgrade runbook (`infra/UPGRADE_PHASE4.md`), and
pushes to origin.

**Branch:** `claude/setup-project-architecture-t7WMs` (continue from HEAD).
**Repo:** `https://github.com/trackandkill/newsoft.git`

---

## Where we are

After Phase 3:
- Discovery (URL-grounded), Board, Validator, Backups, Costs, Run inspector
  all live at `https://firm.profithub.me`.
- System runs ACTIVE in dry-run with a $20/day LLM cap.
- No ventures chartered yet (memo #1 went PASS after re-vote).

Phase 4 adds the production engine: when a venture is chartered, it
gets a plan; the firm can act on the world (starting with one safe,
cheap real-money capability — domain registration); and the founder
gets a daily digest so they don't have to babysit the activity feed.

## Operator decisions baked into this plan (override if you disagree)

| Question | Default | Override |
|---|---|---|
| Next.js target | latest stable 15.x | hermes picks specific version, reports it |
| Email transport | Resend (single API key, works from Hetzner) | SMTP fallback supported |
| Domain registrar | Porkbun (cheap, simple API) | n/a — Porkbun only in Phase 4 |
| Real-money daily cap | $50/day | `MONEY_DAILY_CAP_USD` env var |
| Per-action money cap | $25 | code constant; raise per-tool if needed later |
| Venture pod cadence | every 30 min on chartered-without-plan | code default |
| Digest send time | 08:00 in the VM's local time | code default |

If the operator hasn't stated a preference, hermes uses these.

---

## Scope — four deliverables, in priority order

### 4A. Next.js security bump (small, must-ship)

Goal: clear the CVE hermes flagged in Phase 2 deploy.

Build:
- `dashboard/package.json`: bump `next` from `15.0.3` to the latest
  stable `15.x` (do not jump to `15.2`/`16` if not stable on React
  18.3.1). Acceptable choices: 15.1.7, 15.1.8, 15.1.x latest.
- Keep `react`/`react-dom` at `18.3.1`. Do NOT migrate to React 19.
- Run `npm install && npm run build` in the dev clone to verify.

Acceptance:
- `npm run build` succeeds with no warnings about deprecated APIs we
  use (`use(params)` etc.).
- Dashboard still loads after deploy; all pages render.

### 4B. Daily founder digest (small)

Goal: 8 AM email summarizing what the firm did overnight + what needs
your attention. Reduces babysitting.

Build:
- `orchestrator/orchestrator/digest.py` — module with:
  ```python
  def build_digest() -> dict:
      """Returns {subject, text_body, html_body, summary_dict}."""
  def send_digest() -> None:
      """Builds + sends. Logs an Event either way."""
  ```
- Content sections (in this order):
  1. **State**: active/halted, dry-run, today's LLM spend / cap, today's
     money spend / cap.
  2. **Last 24 hours**: count by event kind (`agent_run: 12`,
     `discovery_complete: 6`, `board_decision: 3`, etc.) plus the most
     recent message in each kind.
  3. **Pending approvals**: list (id, action, requested_by, age) with
     deep-link URL to `https://firm.profithub.me/approvals`.
  4. **New ideas / memos / ventures since yesterday**: short titles
     with deep links.
  5. **Errors**: count of `error` events in the last 24h.
- Email transport in `orchestrator/orchestrator/email.py`:
  - Primary: Resend (`RESEND_API_KEY` env var). Use the REST API
    directly via httpx; no SDK dependency. POST to
    `https://api.resend.com/emails`.
  - Fallback: SMTP via stdlib `smtplib` if `RESEND_API_KEY` unset and
    `SMTP_HOST` is set.
  - If neither is configured: skip send; still write the digest content
    as an Event (`kind='digest_skipped'`) so it's visible in the
    activity log.
- Schedule: APScheduler cron job, daily at 08:00 (server local time).
  Add to `start_scheduler()` in `rituals/scheduler.py`.
- Manual trigger: `POST /api/digest/run` (background task).

Env vars:
| Var | Required | Notes |
|---|---|---|
| `RESEND_API_KEY` | optional | Get at resend.com — free tier covers daily mail |
| `SMTP_HOST` | optional | Used only if `RESEND_API_KEY` unset |
| `SMTP_PORT` | optional | Default 587 |
| `SMTP_USER` | optional | |
| `SMTP_PASSWORD` | optional | |
| `SMTP_USE_TLS` | optional | Default true |
| `DIGEST_TO_EMAIL` | required for send | operator's email |
| `DIGEST_FROM_EMAIL` | required for send | e.g. `founder@profithub.me` (must be on a domain you've verified with Resend) |

Acceptance:
- With `RESEND_API_KEY` + `DIGEST_TO_EMAIL` + `DIGEST_FROM_EMAIL` set:
  `curl -X POST /api/digest/run` produces an email in operator's inbox
  within 60s. Subject like `NewSoft daily digest — 2026-05-05`.
- Without transport configured: same call writes a `digest_skipped`
  event with the digest body in `payload`.
- Errors during send (4xx from Resend, SMTP failure) are logged as
  `kind='error'` events; do not crash the orchestrator.

### 4C. Real-money tool: Porkbun domain registration (medium, must-ship)

Goal: first capability the firm has to spend real money in the world,
gated by hard caps and the approval queue.

Data model additions:
- `SystemState` add columns (idempotent migration):
  - `money_spend_today_usd FLOAT NOT NULL DEFAULT 0.0`
  - `money_daily_cap_usd FLOAT NOT NULL DEFAULT 50.0`
  - `money_spend_day TIMESTAMPTZ NOT NULL DEFAULT now()`
- New table `money_transactions`:
  ```
  id, action (str), amount_usd (float), vendor (str),
  idempotency_key (str, unique), status (pending|done|failed|simulated),
  approval_id (FK approvals nullable), result_json (JSON nullable),
  created_at, completed_at
  ```

Per-action cap: `MAX_PER_ACTION_USD = 25.0` constant in
`orchestrator/money.py`. Reject any action above it.

Module layout:
- `orchestrator/orchestrator/money.py`:
  - `class MoneyCapExceeded(Exception)`
  - `def check_money_budget(s, amount_usd) -> SystemState`
  - `def record_money_spend(s, amount_usd)` (with daily rollover, same
    pattern as `budget.py`)
  - `MAX_PER_ACTION_USD = 25.0`
- `orchestrator/orchestrator/tools/domains.py`:
  - `def domain_check(name: str) -> dict` — Porkbun "checkDomain"
    endpoint. No money. Returns `{available, price_usd, currency}`.
  - `def domain_register(name: str, years: int = 1) -> dict` — refuses
    to execute directly; instead files an Approval with action
    `register_domain` and payload `{name, years, estimated_usd}`.
    Returns `{approval_id, status:"pending_approval"}`.
  - `def execute_domain_registration(approval_id: int) -> dict` —
    called from the API when the operator approves. Hits Porkbun
    "register" endpoint. In dry-run mode, simulates success without
    API call. Either way: writes `MoneyTransaction` row. On real
    success, calls `record_money_spend`.
- Idempotency: every Porkbun POST uses
  `idempotency_key = f"register:{name}:{approval_id}"`. Refuse to
  execute the same approval twice (look up existing
  `MoneyTransaction` by approval_id; if `done` or `simulated`, return
  it instead of re-calling).

API extension:
- `POST /api/approvals/{id}` decision dispatch (extend the existing
  switch from Phase 3):
  - `run_experiment` → `validator.run_experiment` (Phase 3, unchanged)
  - `register_domain` → `domains.execute_domain_registration` (Phase 4)
- New read-only endpoints:
  - `GET /api/money/transactions` — list, newest first, limit 50
  - `GET /api/money/status` — `{spend_today_usd, daily_cap_usd,
    per_action_cap_usd, dry_run}`

Env vars:
| Var | Required | Notes |
|---|---|---|
| `PORKBUN_API_KEY` | for live registration | from porkbun.com → account → API access |
| `PORKBUN_API_SECRET` | for live registration | issued together with the key |
| `MONEY_DAILY_CAP_USD` | optional | overrides default $50 |

Without keys, `domain_check` raises `ToolUnavailable`; `domain_register`
still files Approvals (operator can approve them later once keys are
configured); `execute_domain_registration` runs in dry-run mode and
writes a `simulated` MoneyTransaction.

Dashboard:
- `dashboard/app/money/page.tsx` — table of `MoneyTransaction` rows
  (date, action, amount, vendor, status, approval). Add to nav.
- `/approvals` page: when an approval's `payload` has
  `estimated_usd`, render it prominently (so operator sees what
  they're approving costs).

Acceptance:
- `domain_check("foo-test-12345.com")` returns availability + price
  when keys are set.
- `domain_register("foo-test-12345.com")` creates an Approval row
  with action `register_domain` and the estimated cost in payload.
- Approving the Approval:
  - In dry-run: writes a `simulated` MoneyTransaction, no Porkbun
    call, money spend counter unchanged.
  - In live mode (`DRY_RUN=false` AND keys set): calls Porkbun,
    writes `done` MoneyTransaction, money spend counter increments.
- A second approve on the same Approval is a no-op (idempotency).
- An action with `amount_usd > MAX_PER_ACTION_USD` is rejected before
  any external call.

### 4D. Venture pod (CTO + Engineer) (medium)

Goal: when a venture is chartered, the pod produces a 30/60/90-day
plan plus a list of concrete first-30-day tasks. Tasks that need money
or external action automatically file approvals.

Data model additions:
- New table `plans`:
  ```
  id, venture_id (FK ventures), content_md (Text),
  plan_30 (Text), plan_60 (Text), plan_90 (Text),
  agent_run_id (FK agent_runs nullable), created_at
  ```
- New table `tasks`:
  ```
  id, venture_id (FK ventures), plan_id (FK plans),
  title (str 200), description (Text),
  needs_approval (bool default false),
  approval_id (FK approvals nullable),
  approval_action (str nullable),  -- e.g. 'register_domain'
  approval_payload (JSON nullable),
  status (pending|approved|done|skipped),
  agent_run_id (FK agent_runs nullable),
  created_at, completed_at
  ```

Agents:
- `orchestrator/orchestrator/agents/cto.py`:
  - `CTO = AgentSpec(model=opus, ...)` — reads charter, produces
    `{plan_30:str, plan_60:str, plan_90:str, summary:str}` as JSON.
  - `def draft_plan(venture_id) -> int (plan_id)`.
- `orchestrator/orchestrator/agents/engineer.py`:
  - `ENGINEER = AgentSpec(model=sonnet, ...)` — reads `plan_30`,
    produces a JSON list of tasks. Each task may include
    `approval_action` and `approval_payload` if it requires gated
    execution (e.g., `{"approval_action":"register_domain",
    "approval_payload":{"name":"...","years":1,"estimated_usd":12}}`).
    Allowed actions in Phase 4: `register_domain` only. Anything
    else is treated as a manual task (`needs_approval=false`,
    operator does it).
  - `def break_down_first_30(plan_id) -> list[int]` (task ids).
  - For tasks with `approval_action=register_domain`, file an
    Approval (calling `domains.domain_register` under the hood) and
    set `task.approval_id`.

Ritual:
- `venture_tick` in `rituals/scheduler.py`, every 30 min:
  1. Find chartered Ventures without a Plan. For each: run
     `cto.draft_plan` then `engineer.break_down_first_30`.
  2. Find Tasks with `status='approved'` and `approval_action` set
     where the approval is `approved` and no MoneyTransaction has run
     yet — trigger execution. (For `register_domain`, that's already
     handled by the API approval dispatch; this tick just keeps tasks'
     status in sync — when `MoneyTransaction.status='done'`, set
     `task.status='done'` and `completed_at`.)

API extensions:
- `GET /api/ventures/{slug}/plan` — returns plan + tasks.
- `POST /api/venture/run` — manually trigger `venture_tick`.

Dashboard:
- Extend `dashboard/app/ventures/[slug]/page.tsx`:
  - Plan section (30/60/90 columns or stacked).
  - Tasks table: title, description, status, needs_approval pill,
    approval link if any.

Acceptance:
- The currently-chartered ventures (likely zero — none have FUND'd
  yet) get plans on the next tick. If zero, hermes seeds a test:
  manually charter one via SQL or via a temporary endpoint, verify
  pod runs end to end, then delete the seed. (Document the seed in
  the runbook so it's reproducible.)
- A test plan produces ≥3 tasks, at least one with
  `needs_approval=true` and `approval_action='register_domain'`.
- The corresponding Approval shows up in `/approvals` with
  `payload.estimated_usd` visible.
- Approving (in dry-run) flips the task to `done` and writes a
  `simulated` MoneyTransaction — fully end-to-end, no real spend.

---

## Cross-cutting requirements

### Migrations
- All new columns use `ADD COLUMN IF NOT EXISTS` via the
  `_COLUMN_MIGRATIONS` list in `init_db.py`. New tables use
  `Base.metadata.create_all`.
- Backfill: existing SystemState row needs the new money_* columns
  populated. The IF NOT EXISTS clause + DEFAULT handles this for new
  rows; for the existing singleton row, after migration run a
  `UPDATE system_state SET money_daily_cap_usd = COALESCE(money_daily_cap_usd, 50.0)`
  in `init_db.py` for safety.

### Env vars added in Phase 4
| Var | Required | Notes |
|---|---|---|
| `RESEND_API_KEY` | digest only | optional |
| `SMTP_*` | digest only | optional fallback |
| `DIGEST_TO_EMAIL` | digest only | |
| `DIGEST_FROM_EMAIL` | digest only | must be on Resend-verified domain |
| `PORKBUN_API_KEY` | live domains only | |
| `PORKBUN_API_SECRET` | live domains only | |
| `MONEY_DAILY_CAP_USD` | optional | default 50.0 |

Add all to `.env.example` with placeholder values + comments.

### Module layout (additions only)
```
orchestrator/orchestrator/
  agents/
    cto.py             (new)
    engineer.py        (new)
  tools/
    domains.py         (new)
  digest.py            (new)
  email.py             (new)
  money.py             (new)
  db/models.py         (extend: MoneyTransaction, Plan, Task; SystemState cols)
  rituals/scheduler.py (extend: venture_tick, digest cron)
  api.py               (extend endpoints + approval dispatch)
dashboard/
  app/money/page.tsx                (new)
  app/ventures/[slug]/page.tsx      (extend for plan+tasks)
  lib/api.ts                        (extend)
infra/
  UPGRADE_PHASE4.md                 (new — hermes writes this)
docs/
  PHASE4_PLAN.md                    (this file)
```

### Things hermes should NOT do
- Do not add a second money tool. Domain registration only in Phase 4.
- Do not auto-approve any money action. Every `register_domain` goes
  through the approval queue without exception.
- Do not include API keys, hashed passwords, or tokens in digest email
  bodies (links to dashboard only).
- Do not migrate to React 19. Stay on 18.3.1.
- Do not introduce new Python deps unless strictly necessary. `httpx`
  for Porkbun + Resend is already in the dependency list.
- Do not log Porkbun API key/secret, Resend key, or SMTP password.
- Do not switch reverse proxy. Nginx stays.

### Acceptance for Phase 4 as a whole
After hermes deploys and runs the smoke checks:
- `next --version` in the built dashboard reports a patched 15.x.
- `POST /api/digest/run` either sends a real email or writes a
  `digest_skipped` event with the body in payload.
- `GET /api/money/status` returns the four fields.
- Test domain check + register flow:
  - With Porkbun keys configured: `domain_check("nonexistent-test-XYZ.com")`
    returns availability JSON.
  - `domain_register("..."")` creates Approval; approving (in dry-run)
    creates `simulated` MoneyTransaction; approving twice is a no-op.
- Venture pod end-to-end on at least one venture (real or seeded):
  Plan exists; ≥3 Tasks; ≥1 of them with `needs_approval=true` and
  `approval_action='register_domain'`; approving (dry-run) flips task
  to `done`.
- Daily 08:00 cron job is scheduled (visible via APScheduler logs).
- System ends ACTIVE, dry-run, $20 LLM cap, $50 money cap.

---

## Final report from hermes

| Section | Content |
|---|---|
| Code | latest commit hash on origin; deliverables shipped (4A/4B/4C/4D) |
| Migrations | confirm new tables created; new SystemState columns present |
| Env | which Phase-4 vars provided; which deferred |
| Build | Next.js version that landed |
| Digest | sent (with timestamp) or skipped (with reason) |
| Money | result of test domain_check + dry-run register_domain end to end |
| Pod | venture(s) processed, plan id, # of tasks, # needing approval |
| State | system active? caps? dry-run? today's spend (LLM and money) |
| Issues | non-fatal warnings, things deferred to Phase 5 |

End of plan.
