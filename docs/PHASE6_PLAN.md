# Phase 6 build plan

For the hermes coding agent. Build spec — hermes implements, deploys,
writes its own short upgrade runbook (`infra/UPGRADE_PHASE6.md`), and
pushes to origin.

**Branch:** `claude/setup-project-architecture-t7WMs` (continue from HEAD).
**Repo:** `https://github.com/trackandkill/newsoft.git`

---

## Where we are

After Phase 5:
- Discovery → Board → Validator → CEO → CTO + Engineer pod →
  Copywriter → Site deploy pipeline (staging → live) → Lead capture
  all live and end-to-end smoke tested in dry-run / simulated mode.
- One seed venture, one site row, multiple Lead rows, Porkbun keys
  still deferred.
- Approval queue covers every side-effectful action.
- Operator must flip global `dry_run` to actually execute. That's a
  blunt instrument: it flips for ALL money tools at once.

Phase 6 closes three operational gaps:

1. **Per-approval execute-live override.** Right now to do one real
   thing you flip global dry_run, then everything is potentially live.
   We want: keep global dry_run = true permanently; opt one specific
   approval into live execution at the moment of approval.
2. **Postmortem + auto-kill loop.** Without it, dead ventures
   accumulate forever. The firm needs a graceful failure mechanism.
3. **npm audit cleanup.** Two outstanding vulns. Patch what we can
   without a React 19 migration; document anything we can't.

Plus a small operational addition: a key-rotation runbook so we have a
documented procedure for rotating each external API key
(Porkbun, Resend, Anthropic, GitHub deploy key).

## Operator decisions baked into this plan

| Question | Default | Override |
|---|---|---|
| Default `execute_live` for new approvals | `null` (follow system dry_run, legacy) | per-approval at decision time |
| Postmortem cadence | weekly check (cron Sunday 09:00 local) | code default |
| Kill criteria source | parsed from `venture.charter` markdown | code default |
| Auto-kill behavior | files an Approval; never kills without operator | hard rule |
| npm-audit policy | patch within React 18.3.x; document anything blocked by React 19 | code default |

If the operator hasn't stated a preference, hermes uses these.

---

## Scope — four deliverables, in priority order

### 6A. Per-approval execute-live override (small, must-ship)

Goal: operator can opt one specific approval into live execution
without flipping `system_state.dry_run`.

#### Data model
Add column to `approvals` (idempotent migration):
- `execute_live BOOLEAN NULL` — `null` means "follow system dry_run"
  (legacy / default), `true` means "execute live regardless of system
  dry_run", `false` means "simulate regardless of system dry_run".

#### API
Extend `POST /api/approvals/{id}` body:
```json
{ "approve": true, "execute_live": true | false | null, "decided_by": "founder" }
```
- If `approve = true` and `execute_live` is unset: use the legacy path
  (follow system dry_run).
- If `approve = true` and `execute_live = true`: pass `force_live=True`
  into the dispatcher.
- If `approve = true` and `execute_live = false`: pass
  `force_simulate=True` into the dispatcher.
- If `approve = false`: reject; ignore execute_live.

#### Tool dispatcher contract
Each tool's "execute" function (e.g.,
`execute_domain_registration`, `sites.execute_deploy`) gains an
optional kwarg `force_live: bool | None = None`. Resolution:
```python
def is_live(state, force_live):
    if force_live is True:  return True
    if force_live is False: return False
    return not state.dry_run
```
Audit log every decision: `MoneyTransaction.metadata` (or a new
`tool_calls.execute_mode` column — pick whichever is cleaner) records
whether the action ran live or simulated.

#### Dashboard
`dashboard/app/approvals/page.tsx`: when an approval's action is in a
known "side-effectful" set (domain_register, configure_dns,
deploy_landing_page, run_experiment, kill_venture, future Stripe/etc.),
render TWO approve buttons:
- **Approve (simulate)** — sets `execute_live=false`
- **Approve (live $X.XX)** — sets `execute_live=true`; uses
  `payload.estimated_usd` for the dollar label if present.

For non-side-effectful approvals (none today; placeholder for future),
keep the single Approve button (execute_live=null).

The Reject button is unchanged.

#### Acceptance
- Approving an existing `register_domain` test approval with
  `execute_live=false` while `system_state.dry_run=false` produces a
  `simulated` MoneyTransaction (override worked downward).
- Approving with `execute_live=true` while `system_state.dry_run=true`
  hits Porkbun for real (override worked upward) — only run this
  path with operator's explicit go-ahead and a $15 money cap.
- Dashboard renders both buttons for `register_domain`,
  `configure_dns`, `deploy_landing_page`, `run_experiment`.
- Audit shows the chosen mode for every executed approval.

### 6B. Postmortem + auto-kill loop (medium, must-ship)

Goal: dead ventures end gracefully with a postmortem and a clean
state transition; the loop runs continuously without operator
prompting.

#### Data model
Add columns to `ventures` (idempotent migration):
- `kill_criteria_json JSON` — parsed once from charter; cached.
- `killed_at TIMESTAMPTZ`
- `kill_reason TEXT`

New table `postmortems`:
```
id, venture_id (FK), content_md (Text),
lessons_md (Text),         -- separate field; future Phase 7 vector
                           -- memory will index this
agent_run_id (FK agent_runs nullable),
created_at
```

Status states for `ventures.status`:
- existing: `chartered`, `active`
- new: `kill_pending` (awaiting operator approval of kill),
  `killed`, `graduated` (reserved; not used in Phase 6).

#### Agents
- `orchestrator/orchestrator/agents/kill_evaluator.py`:
  ```python
  EVALUATOR = AgentSpec(model=sonnet, ...)
  def evaluate_venture(venture_id) -> dict:
      """Reads charter (kill_criteria), plan, tasks, leads count,
      experiments, days-since-charter. Returns
      {should_kill: bool, criteria_hit: [str], rationale: str,
       evidence: {...}}."""
  ```
  Conservative by default. The agent only flags clear, evidenced
  violations. Soft signals do NOT trigger.

- `orchestrator/orchestrator/agents/postmortem_writer.py`:
  ```python
  WRITER = AgentSpec(model=opus, ...)
  def write_postmortem(venture_id, kill_reason: str) -> int:
      """Returns postmortem_id. Reads everything we know about the
      venture and produces:
        - content_md: full narrative postmortem (charter, what
          happened, what failed, what we learned)
        - lessons_md: 3-7 bullet-point lessons (future-self facing)
      Refuses to run if venture.status not in {chartered, kill_pending}."""
  ```

#### Ritual
New `kill_loop_tick` in `rituals/scheduler.py`:
- Cron schedule: weekly, Sunday 09:00 server local time.
- Walks every venture in status `chartered` or `active`.
- Calls `evaluate_venture`. If `should_kill = true`:
  - Creates an Approval with `action='kill_venture'`,
    `payload={venture_id, criteria_hit, rationale, evidence}`,
    `requested_by='kill_evaluator'`.
  - Sets `venture.status='kill_pending'`.
  - Logs an Event.
- Idempotent: if a `kill_venture` Approval already exists for this
  venture (any status), skip.

Manual triggers:
- `POST /api/kill_loop/run` — runs the loop synchronously (operator).
- `POST /api/ventures/{slug}/postmortem` — runs a postmortem on
  demand even without a kill (useful for graduations / pivots).

#### Approval dispatcher extension
- Action `kill_venture` → `postmortem_writer.write_postmortem` then
  `venture.status='killed'`, `killed_at=now`, copy
  `payload.rationale` to `kill_reason`.

If `execute_live=false` (from 6A), still write the postmortem and flip
status — postmortems are not money-moving; the live/simulate axis
doesn't apply. Just always run.

#### Dashboard
- `dashboard/app/ventures/[slug]/page.tsx`: add a Postmortem section
  if one exists; show `killed_at` and `kill_reason` in the header
  pill row when status is `killed` or `kill_pending`.
- `dashboard/app/postmortems/page.tsx`: index of all postmortems
  with venture name, date, lessons summary.

API:
- `GET /api/ventures/{slug}/postmortem` — returns the postmortem if
  any.
- `GET /api/postmortems` — list all postmortems.

#### Acceptance
- For the seed venture (`Phase 4 Smoke Venture`), running
  `evaluate_venture` returns sensible JSON. Whether it flags
  `should_kill=true` depends on data — that's fine; we just need to
  exercise the path.
- A test path: hermes seeds a "fake-fail" Approval directly via SQL
  (`action='kill_venture'`, payload pointing at the seed venture);
  approving it (in simulate mode) writes a postmortem and flips the
  venture to `killed`.
- The seed venture's detail page shows the postmortem; the
  postmortems index lists it.
- After test, manually re-flip the seed venture to `chartered` so
  Phase 7 can resume from a clean state.

### 6C. npm audit cleanup (small)

Goal: clear or document the two npm vulns hermes flagged, without a
React 19 migration.

Build steps:
1. `cd /opt/newsoft/dashboard && npm audit --json > /tmp/audit.json`
   to identify exact CVEs.
2. For each vulnerability:
   - If patchable by `npm update <pkg>` while keeping React 18.3.x,
     update package.json to the patched version, run
     `npm install && npm run build`, verify no runtime errors.
   - If patchable only by jumping a major version that requires
     React 19, document it in `docs/KNOWN_VULNS.md` with: CVE id,
     dependency path, why we're holding, what the practical attack
     surface looks like, and a target date for re-evaluation.
3. After patches: `npm audit` should show fewer (ideally zero)
   vulnerabilities, OR `docs/KNOWN_VULNS.md` documents what's left
   and why.

Acceptance:
- `npm run build` still passes.
- Dashboard still works (smoke-test all top-nav pages).
- `npm audit` count reduced (target: 0; floor: documented).
- `docs/KNOWN_VULNS.md` exists if anything is held.

### 6D. Key rotation runbook (tiny)

Goal: documented, reproducible rotation procedure for every external
secret.

Build:
- `infra/KEY_ROTATION.md` covering each of:
  - `ANTHROPIC_API_KEY` (rotate at console.anthropic.com)
  - `BRAVE_API_KEY` (rotate at brave.com search dashboard)
  - `RESEND_API_KEY` (rotate at resend.com)
  - `PORKBUN_API_KEY` / `PORKBUN_API_SECRET` (rotate at porkbun.com
    account settings)
  - `NEWSOFT_BASIC_HASH` (regenerate with `caddy hash-password`)
  - `LEAD_IP_HASH_PEPPER` (rotate triggers a one-time Lead table
    backfill OR accept that historical IP-hashes won't match new
    submissions; document the trade-off)
  - GitHub deploy key (regenerate keypair, replace on GitHub,
    update remote URL if needed)
- For each: pre-flight checks, the literal commands the operator
  runs on the VM, post-flight verification (e.g., make one API call
  with the new key), rollback if rotation fails.

Acceptance:
- `infra/KEY_ROTATION.md` exists and covers all listed keys.
- Cross-referenced from `infra/UPGRADE_PHASE6.md` and the main
  `README.md`.

---

## Cross-cutting requirements

### Migrations
- `approvals.execute_live` (BOOLEAN NULL) — idempotent ALTER
- `ventures.kill_criteria_json` (JSON NULL) — idempotent ALTER
- `ventures.killed_at` (TIMESTAMPTZ NULL) — idempotent ALTER
- `ventures.kill_reason` (TEXT NULL) — idempotent ALTER
- New table `postmortems` — `Base.metadata.create_all`
- (If chosen) `tool_calls.execute_mode` (VARCHAR(20) NULL) — idempotent
  ALTER. Skip if you instead store this on `MoneyTransaction.metadata`.

### Env vars added in Phase 6
None. Behaviour entirely controlled by existing env + new approval-time
toggle.

### Module layout (additions only)
```
orchestrator/orchestrator/
  agents/
    kill_evaluator.py     (new)
    postmortem_writer.py  (new)
  db/models.py            (extend with Postmortem; add columns)
  rituals/scheduler.py    (extend with kill_loop_tick)
  api.py                  (extend approval payload + new endpoints)
dashboard/
  app/postmortems/page.tsx              (new)
  app/ventures/[slug]/page.tsx          (extend for postmortem section)
  app/approvals/page.tsx                (extend buttons)
  lib/api.ts                            (extend)
infra/
  KEY_ROTATION.md                       (new)
  UPGRADE_PHASE6.md                     (new — hermes writes this)
docs/
  KNOWN_VULNS.md                        (new, only if anything held)
  PHASE6_PLAN.md                        (this file)
```

### Things hermes should NOT do
- Do not auto-execute kill_venture without operator approval.
  Conservative by default: even if every kill criterion is hit, the
  loop only files an Approval.
- Do not auto-flip `system_state.dry_run`. The whole point of 6A is
  that the global flag stays sticky.
- Do not destructively delete venture data on kill. `status='killed'`
  + `killed_at` + postmortem is the entire change.
- Do not change Phase 5 site teardown logic. A killed venture's site
  stays live until manually torn down (Phase 7 will add an
  `auto_teardown` policy if we decide we want one).
- Do not migrate to React 19.
- Do not reduce per-action money cap below $25 globally; per-test
  caps go via the system endpoint, not in code.
- Do not bake any new env vars into Phase 6.

### Acceptance for Phase 6 as a whole
After hermes deploys and runs the smoke checks:
- Code: pushed to origin; new commit hash recorded.
- Migrations: `approvals.execute_live` present;
  `ventures.kill_criteria_json/killed_at/kill_reason` present;
  `postmortems` table present.
- Approval flow: dashboard renders two buttons for side-effectful
  approvals; API accepts `execute_live` in request body; audit
  records the chosen mode.
- Kill loop: `kill_loop_tick` runs successfully (sync via
  `POST /api/kill_loop/run`); produces an Approval if any criteria
  hit, or a `no_kill_candidates` Event if none.
- Postmortem path: hermes seeds a fake `kill_venture` approval for the
  smoke venture, approves in simulate mode, postmortem written,
  status flips to `killed`. Hermes then resets the venture status to
  `chartered` (`UPDATE ventures SET status='chartered', killed_at=NULL,
  kill_reason=NULL WHERE id = N`) so further phases have clean state.
- npm audit: count reduced or documented in `docs/KNOWN_VULNS.md`.
- `infra/KEY_ROTATION.md` exists, covers all listed keys.
- System ends ACTIVE, dry-run, $20 LLM cap, $50 money cap.

---

## Final report from hermes

| Section | Content |
|---|---|
| Code | latest commit hash on origin; deliverables shipped (6A/B/C/D) |
| Migrations | confirm new columns/table present |
| 6A smoke | example: approval N approved with execute_live=false; audit shows simulated; another approval approved with execute_live=true and `system.dry_run=true`; audit shows live (only if operator authorised one live test) |
| 6B smoke | seed kill_venture approval id; postmortem id; venture status flips; reset confirmed |
| 6C result | npm audit before/after counts; KNOWN_VULNS.md present (or not, if zero) |
| 6D doc | KEY_ROTATION.md present; cross-references added |
| State | system active? caps? dry-run? today's spend (LLM and money) |
| Issues | non-fatal warnings, things deferred to Phase 7 |

End of plan.
