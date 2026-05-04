# NewSoft

An autonomous, multi-agent firm. A board of AI agents discovers, validates, and
operates small businesses; a human founder sets goals and holds veto power.

## Status

Phase 1 — skeleton. Discovery pod runs end-to-end on a fake goal with full
audit logging. No real-money tools wired up yet. Everything is dry-run.

## Layout

```
orchestrator/      Python service: scheduler, agent runtime, DB, HTTP API
  orchestrator/
    agents/        Role definitions and system prompts
    db/            SQLAlchemy models + session
    rituals/       Scheduled jobs (discovery loop)
    runtime.py     Anthropic SDK wrapper with budget + audit
    budget.py      Spend cap + kill-switch enforcement
    api.py         FastAPI app
dashboard/         Next.js UI: goals, ideas, memos, approvals, activity
ventures/          One subdirectory per funded venture (empty in Phase 1)
infra/             systemd units, Caddyfile, install script
```

## Architecture

- **Orchestrator** is a single Python process running FastAPI for the dashboard
  to talk to, plus an APScheduler that fires rituals (currently: hourly
  discovery tick).
- **Discovery pod** runs three agents in sequence: Market Scout (Sonnet) →
  Opportunity Analyst (Sonnet) → Memo Writer (Opus). Output is a scored list
  of business ideas and an investment memo for the top candidate.
- **Audit log**: every agent run, tool call, and event lands in Postgres.
  Dashboard reads from the same tables.
- **Safety**: kill switch + per-day spend cap + dry-run flag, all enforced in
  `runtime.py` before any model call.

## Local development

Requires Python 3.12, Node 20+, Postgres 14+.

```
createdb newsoft   # or use an existing DB
cp .env.example .env
# Fill in ANTHROPIC_API_KEY and DATABASE_URL

cd orchestrator
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m orchestrator      # http://localhost:8000

# In another shell:
cd dashboard
npm install && npm run dev  # http://localhost:3000
```

## Deploy to Hetzner VM

The install script targets Ubuntu 24.04 and reuses an existing local Postgres.

```
git clone <this repo> /tmp/newsoft
sudo bash /tmp/newsoft/infra/install.sh /tmp/newsoft
# then: edit /opt/newsoft/.env and start the services
```

The script:
- Installs Python, Node, and Caddy
- Creates a `newsoft` system user
- Creates a `newsoft` Postgres role + DB inside the existing Postgres
- Builds the orchestrator venv and the Next.js dashboard
- Installs and enables two systemd units (`newsoft-orchestrator`,
  `newsoft-dashboard`) with memory caps suited to a 4 GB VM
- Drops a Caddyfile template at `/opt/newsoft/infra/Caddyfile` for HTTPS
  termination on a subdomain

## Runbooks

- `infra/UPGRADE_PHASE6.md` — Phase 6 execute-live/postmortem/audit upgrade checklist.
- `infra/KEY_ROTATION.md` — Rotation procedures for Anthropic, Brave, Resend, Porkbun, dashboard auth, lead pepper, and GitHub deploy keys.

## Safety rails

- All money-moving / side-effectful tool calls are dry-run by default
- Per-day spend cap enforced in `orchestrator/budget.py` before each model call
- Approval queue table for actions above caps or flagged risky
- Kill switch flips `system_state.active = false` and halts all rituals
- Full audit log of every prompt, response, tool call, and decision in Postgres
