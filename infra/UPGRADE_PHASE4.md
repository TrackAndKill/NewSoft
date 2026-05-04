# NewSoft Phase 4 Upgrade Runbook

## Scope

Phase 4 ships:

1. Next.js patched 15.x dashboard dependency bump while staying on React 18.3.1.
2. Daily 08:00 founder digest via Resend/SMTP, with `digest_skipped` event fallback when email is not configured.
3. Porkbun domain registration as the first approval-gated real-money tool with money spend caps and transaction audit rows.
4. Venture pod: CTO 30/60/90 plans plus Engineer first-30-day tasks for chartered ventures.

## Preconditions

- Work from branch `claude/setup-project-architecture-t7WMs`.
- GitHub SSH deploy key for `newsoft` can push to origin.
- Preserve `/opt/newsoft/.env`; never overwrite or print secrets.
- Nginx remains the public reverse proxy for `https://firm.profithub.me`.
- Existing live services should stay up except during short NewSoft restarts:
  - `newsoft-orchestrator`
  - `newsoft-dashboard`
  - `nginx`
  - `postgresql`
  - `redis-server`

## New env vars

Optional; all have graceful unset behavior:

```text
RESEND_API_KEY=
DIGEST_TO_EMAIL=
DIGEST_FROM_EMAIL=
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_USE_TLS=true
PORKBUN_API_KEY=
PORKBUN_API_SECRET=
MONEY_DAILY_CAP_USD=50.00
PUBLIC_DASHBOARD_URL=https://firm.profithub.me
```

Unset email transport: `POST /api/digest/run` logs `digest_skipped` with the digest content.

Unset Porkbun credentials: `domain_check` is unavailable, but `domain_register` still files approval rows and approved registrations simulate in dry-run without external calls.

## Deploy

From a clean synced clone:

```bash
cd /tmp/newsoft
git fetch origin claude/setup-project-architecture-t7WMs
git checkout claude/setup-project-architecture-t7WMs
git pull --ff-only origin claude/setup-project-architecture-t7WMs

python3 -m compileall orchestrator/orchestrator
cd dashboard && npm install && npm run build && cd ..

git diff --check
```

Sync to live, preserving secrets and build/runtime dirs:

```bash
rsync -a --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude '.ssh' \
  --exclude 'node_modules' \
  --exclude '.venv' \
  --exclude '.next' \
  --exclude '__pycache__' \
  /tmp/newsoft/ /opt/newsoft/
```

Install/build live:

```bash
set -o pipefail
/opt/newsoft/orchestrator/.venv/bin/pip install -e /opt/newsoft/orchestrator 2>&1 | tee /tmp/newsoft-phase4-pip.log
cd /opt/newsoft/dashboard
npm install 2>&1 | tee /tmp/newsoft-phase4-npm-install.log
npm run build 2>&1 | tee /tmp/newsoft-phase4-npm-build.log
```

Run migrations by starting/restarting orchestrator; `init_db()` performs idempotent `ADD COLUMN IF NOT EXISTS` and `Base.metadata.create_all()`.

```bash
systemctl restart newsoft-orchestrator
systemctl restart newsoft-dashboard
systemctl is-active newsoft-orchestrator newsoft-dashboard nginx postgresql redis-server
```

Set final Phase 4 runtime state:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/system \
  -H 'content-type: application/json' \
  -d '{"active":true,"dry_run":true,"daily_spend_cap_usd":20.0,"money_daily_cap_usd":50.0}'
```

## Smoke checks

```bash
# API basics
curl -fsS http://127.0.0.1:8000/api/status
curl -fsS http://127.0.0.1:8000/api/money/status
curl -fsS http://127.0.0.1:8000/api/money/transactions

# Digest: should send or log digest_skipped
SINCE=$(date -u '+%Y-%m-%d %H:%M:%S')
curl -fsS -X POST http://127.0.0.1:8000/api/digest/run
sleep 5
journalctl -u newsoft-orchestrator --since "$SINCE" --no-pager | grep -Ei 'digest|error' || true
curl -fsS http://127.0.0.1:8000/api/events | python3 -m json.tool | grep -E 'digest_sent|digest_skipped|Daily digest' || true

# Venture pod manual trigger
curl -fsS -X POST http://127.0.0.1:8000/api/venture/run
```

For a full end-to-end dry-run test when there are no real chartered ventures, create one temporary venture, run `venture_tick`, approve the generated `register_domain` approval, verify a `simulated` `MoneyTransaction`, then delete the seed rows in reverse dependency order. Keep `DRY_RUN=true` for this smoke.

## Rollback

1. Leave `/opt/newsoft/.env` untouched.
2. Revert code to previous origin commit and rsync with the same excludes.
3. Rebuild dashboard and restart `newsoft-orchestrator` + `newsoft-dashboard`.
4. The new DB columns/tables are additive and can remain in place.

## Final report template

- Code: origin commit hash and delivered 4A/4B/4C/4D.
- Migrations: confirm `money_transactions`, `plans`, `tasks`, and `SystemState.money_*` columns.
- Env: report configured vs deferred without printing secrets.
- Build: exact Next.js version.
- Digest: sent or skipped with event id/reason.
- Money: status endpoint and dry-run register-domain transaction proof.
- Pod: venture processed, plan id, task count, approval-gated task count.
- State: active, dry-run, LLM cap/spend, money cap/spend.
- Issues/deferred: missing optional keys, warnings, Phase 5 items.
