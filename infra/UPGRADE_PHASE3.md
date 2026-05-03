# NewSoft — Phase 3 upgrade runbook

In-place upgrade of an existing Phase 2 deployment. Adds daily backups, cost dashboard, agent-run inspector, Brave-backed web search tools, Validator experiments for EXPLORE memos, and cadence tuning.

## Inputs

| Variable | Required | What it is |
|---|---|---|
| `DASHBOARD_PASSWORD` | yes | Existing Basic Auth password for `founder`. Do not log. |
| `BRAVE_API_KEY` | optional | Brave Search API key. If unset, Scout/Validator fall back gracefully. |

## Preconditions

1. `systemctl is-active newsoft-orchestrator` returns `active`.
2. `systemctl is-active newsoft-dashboard` returns `active`.
3. `systemctl is-active nginx` returns `active`; nginx remains the reverse proxy.
4. `curl -fsS http://127.0.0.1:8000/api/status` returns JSON.
5. `/opt/newsoft/.env` exists, mode `600`, owner `newsoft:newsoft`.
6. `/tmp/newsoft` is on branch `claude/setup-project-architecture-t7WMs`.

## Deploy steps

### 1. Pull/latest code

```bash
cd /tmp/newsoft
git fetch origin claude/setup-project-architecture-t7WMs
git checkout claude/setup-project-architecture-t7WMs
git pull --ff-only origin claude/setup-project-architecture-t7WMs
git rev-parse HEAD
```

### 2. Sync code preserving secrets

```bash
sudo rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
  --exclude '.next' --exclude '__pycache__' --exclude '.env' \
  /tmp/newsoft/ /opt/newsoft/
sudo chown -R newsoft:newsoft /opt/newsoft
sudo chmod 600 /opt/newsoft/.env
```

### 3. Env updates

Add only missing keys; never print secrets:

```bash
sudo sed -i '/^DAILY_SPEND_CAP_USD=/d' /opt/newsoft/.env
printf 'DAILY_SPEND_CAP_USD=20.00\n' | sudo tee -a /opt/newsoft/.env >/dev/null
# If Brave key was provided:
# printf 'BRAVE_API_KEY=%s\nWEB_SEARCH_PROVIDER=brave\n' "$BRAVE_API_KEY" | sudo tee -a /opt/newsoft/.env >/dev/null
sudo chmod 600 /opt/newsoft/.env && sudo chown newsoft:newsoft /opt/newsoft/.env
```

### 4. Install/rebuild

```bash
sudo -u newsoft /opt/newsoft/orchestrator/.venv/bin/pip install -e /opt/newsoft/orchestrator
cd /opt/newsoft/dashboard
set -a; . /opt/newsoft/.env; set +a
sudo -E -u newsoft npm install --no-audit --no-fund
sudo -E -u newsoft npm run build
```

### 5. Install backup timer

```bash
sudo useradd --system --create-home --home-dir /home/newsoft-backup --shell /usr/sbin/nologin newsoft-backup 2>/dev/null || true
sudo install -d -m 700 -o newsoft-backup -g newsoft-backup /var/backups/newsoft
# Create /home/newsoft-backup/.pgpass from POSTGRES_PASSWORD in /opt/newsoft/.env without printing it.
sudo install -m 644 /opt/newsoft/infra/systemd/newsoft-backup.service /etc/systemd/system/
sudo install -m 644 /opt/newsoft/infra/systemd/newsoft-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now newsoft-backup.timer
sudo systemctl start newsoft-backup.service
systemctl list-timers | grep newsoft-backup
sudo ls -lh /var/backups/newsoft/newsoft-*.pgc | tail -1
```

### 6. Restart services

```bash
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
sleep 5
systemctl is-active newsoft-orchestrator newsoft-dashboard nginx
curl -fsS http://127.0.0.1:8000/api/status
curl -fsS http://127.0.0.1:8000/api/costs
```

### 7. Smoke checks

```bash
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/costs
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/events | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))'
# Reactivate with cap:
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" -X POST https://firm.profithub.me/api/system \
  -H 'content-type: application/json' -d '{"active":true,"daily_spend_cap_usd":20.0}'
# Trigger board + validator:
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" -X POST https://firm.profithub.me/api/board/run
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" -X POST https://firm.profithub.me/api/validator/run
sleep 90
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/experiments
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/approvals?status=pending
```

If a `run_experiment` approval is present and the operator permits smoke approval, approve it and wait for the background run:

```bash
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" -X POST https://firm.profithub.me/api/approvals/<id> \
  -H 'content-type: application/json' -d '{"approve":true,"decided_by":"founder"}'
sleep 90
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/experiments
```

## Rollback

Rollback is code-only plus disabling the backup timer if desired:

```bash
sudo systemctl disable --now newsoft-backup.timer || true
cd /tmp/newsoft && git checkout 4e4758f -- .
sudo rsync -a --delete --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude '.next' --exclude '__pycache__' --exclude '.env' /tmp/newsoft/ /opt/newsoft/
cd /opt/newsoft/dashboard && sudo -E -u newsoft npm run build
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
```

The `experiments` table is additive and safe to leave.

## Final report template

- Code: commit hash; shipped 3A/3B/3C/3D/3E/3F.
- Migrations: experiments table exists; logs clean of `ProgrammingError`.
- Env: vars added; Brave key present yes/no.
- Backups: timer next fire; first backup size.
- Costs: today/yesterday/7d from `/api/costs`.
- Smoke: agent runs last hour; experiments designed; approvals waiting.
- State: active/cap/dry_run.
- Issues: warnings/non-fatal notes.
