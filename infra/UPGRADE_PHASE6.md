# NewSoft Phase 6 Upgrade Runbook

## Scope

Phase 6 ships:

1. Per-approval `execute_live` override so global `dry_run=true` can stay sticky while one approved action is forced live or simulated.
2. Conservative kill loop: `kill_evaluator` files `kill_venture` approvals only; approval writes a postmortem and marks the venture killed without deleting data or tearing down the site.
3. Dashboard support for dual approval buttons, venture postmortem display, and a postmortems index.
4. npm audit cleanup/documentation.
5. `infra/KEY_ROTATION.md` for external secrets.

See `infra/KEY_ROTATION.md` before rotating any API key or dashboard credential.

## Pre-flight

```bash
cd /tmp/newsoft
git fetch origin claude/setup-project-architecture-t7WMs
git checkout claude/setup-project-architecture-t7WMs
git pull --ff-only origin claude/setup-project-architecture-t7WMs
```

Do **not** overwrite `/opt/newsoft/.env`. Do **not** flip global `dry_run` to false. Phase 6 has no new env vars.

## Deploy code

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

cd /opt/newsoft/orchestrator
/opt/newsoft/orchestrator/.venv/bin/pip install -e .
/opt/newsoft/orchestrator/.venv/bin/python -m orchestrator.db.init_db

cd /opt/newsoft/dashboard
npm install
npm run build
```

## Restart and preserve caps

Re-engage with caps unchanged except for the required final state: active, dry-run, $20 LLM daily cap, $50 money daily cap.

```bash
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
curl -fsS -X POST http://127.0.0.1:8000/api/system \
  -H 'content-type: application/json' \
  -d '{"active":true,"dry_run":true,"daily_spend_cap_usd":20.0,"money_daily_cap_usd":50.0}'
```

## Smoke checks

### Schema

```bash
sudo -u postgres psql -d newsoft -P pager=off -c "\d approvals" | grep execute_live
sudo -u postgres psql -d newsoft -P pager=off -c "\d ventures" | grep -E 'kill_criteria_json|killed_at|kill_reason'
sudo -u postgres psql -d newsoft -P pager=off -c "\d postmortems"
```

### 6A approval override

Create/choose a side-effectful approval and approve with simulation override:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/approvals/<approval_id> \
  -H 'content-type: application/json' \
  -d '{"approve":true,"execute_live":false,"decided_by":"founder"}'
```

Verify `approvals.execute_live=false` and the resulting event/transaction records `execute_mode=simulated`.

Only test `execute_live=true` for a real-money action after explicit operator approval. For that one test temporarily set `money_daily_cap_usd=15`, restore `50` immediately after, and stop on any Porkbun/account-funding error.

### 6B kill loop/postmortem

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/kill_loop/run
```

For smoke, seed a fake `kill_venture` approval for the seed venture, approve in simulate mode, verify a postmortem row, then reset the seed venture:

```sql
UPDATE ventures SET status='chartered', killed_at=NULL, kill_reason=NULL WHERE slug='phase-4-smoke-venture';
```

### Dashboard

```bash
curl -fsS http://127.0.0.1:3000/approvals | grep 'Approve (simulate)'
curl -fsS http://127.0.0.1:3000/postmortems | grep 'Postmortems'
curl -fsS http://127.0.0.1:3000/ventures | grep 'Ventures'
```

## Rollback

Rollback is code-level only; schema additions are additive and safe to leave in place.

```bash
cd /opt/newsoft
sudo -u newsoft git checkout <previous-good-commit>
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
```

Do not delete `postmortems` or kill metadata. If a smoke killed the seed venture, reset it to `chartered` as shown above.
