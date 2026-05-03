# NewSoft — Deploy Runbook (Phase 1)

Self-contained instructions for an automation agent to deploy NewSoft onto a
fresh-or-existing Ubuntu 24.04 Hetzner VM that already runs Postgres on
localhost:5432.

**Target host:** `firm.profithub.me`
**Branch:** `claude/setup-project-architecture-t7WMs`
**Repo:** `https://github.com/trackandkill/newsoft.git`

## Inputs the agent needs from the operator

| Variable | Example | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Required. From console.anthropic.com. |
| `NEWSOFT_DB_PASSWORD` | random 24+ chars | Postgres password for the `newsoft` role. Pick anything strong. |
| `VM_IPV4` | e.g. `5.6.7.8` | The VM's public IPv4. Used to verify DNS. |

The agent should NOT print these to logs.

## Preconditions to verify before starting

1. **OS**: `cat /etc/os-release` shows Ubuntu 24.04.
2. **Postgres listening**: `ss -tln | grep :5432` returns a row.
3. **Disk**: `df -h /` shows >= 5 GB free.
4. **Memory**: `free -m` shows >= 2.5 GB total. (4 GB recommended.)
5. **DNS**: `dig +short firm.profithub.me` returns `${VM_IPV4}`. If not, **stop**
   and ask the operator to add the A record. Caddy cannot get a TLS cert until
   DNS resolves to this VM.
6. **Ports 80/443 free**: `ss -tln | grep -E ':(80|443) '` returns nothing.
   If something else binds them, abort and ask the operator.

## Step-by-step plan

### 1. Clone the repo
```bash
sudo rm -rf /tmp/newsoft
git clone https://github.com/trackandkill/newsoft.git /tmp/newsoft
cd /tmp/newsoft
git checkout claude/setup-project-architecture-t7WMs
```
**Verify**: `git rev-parse HEAD` matches the latest commit on that branch.

### 2. Run the installer
```bash
sudo bash /tmp/newsoft/infra/install.sh /tmp/newsoft 2>&1 | tee /tmp/newsoft-install.log
```
Expected duration: 3–6 minutes. The script:
- Installs `python3.12`, `nodejs 20`, `caddy`
- Creates system user `newsoft`
- Rsyncs the repo to `/opt/newsoft`
- Bootstraps Postgres role + DB inside the existing Postgres
- Builds `/opt/newsoft/orchestrator/.venv` and runs `pip install -e .`
- Runs `npm ci && npm run build` in the dashboard
- Installs systemd units (does NOT start them)

**Verify** at the end:
- `/opt/newsoft/orchestrator/.venv/bin/python -c "import orchestrator"` exits 0.
- `test -d /opt/newsoft/dashboard/.next` is true.
- `systemctl list-unit-files | grep newsoft` shows both units `disabled` or `enabled` (not `not-found`).

### 3. Write `.env`
The installer copied `.env.example` to `/opt/newsoft/.env` (mode 600, owned by
`newsoft`). Replace it with the real values:
```bash
sudo install -m 600 -o newsoft -g newsoft /dev/stdin /opt/newsoft/.env <<EOF
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
POSTGRES_USER=newsoft
POSTGRES_PASSWORD=${NEWSOFT_DB_PASSWORD}
POSTGRES_DB=newsoft
DATABASE_URL=postgresql+psycopg://newsoft:${NEWSOFT_DB_PASSWORD}@localhost:5432/newsoft
ORCHESTRATOR_PORT=8000
DRY_RUN=true
DAILY_SPEND_CAP_USD=5.00
SYSTEM_ACTIVE=true
NEXT_PUBLIC_API_URL=https://firm.profithub.me
EOF
```

### 4. Sync the Postgres password
```bash
sudo -u postgres psql -c "ALTER ROLE newsoft PASSWORD '${NEWSOFT_DB_PASSWORD}';"
```
**Verify**:
```bash
PGPASSWORD="${NEWSOFT_DB_PASSWORD}" psql -h 127.0.0.1 -U newsoft -d newsoft -c '\dt'
```
Should return `Did not find any relations.` (tables get created on first
orchestrator boot).

### 5. Start the services
```bash
sudo systemctl enable --now newsoft-orchestrator newsoft-dashboard
```
**Verify** within 15s:
```bash
systemctl is-active newsoft-orchestrator   # -> active
systemctl is-active newsoft-dashboard      # -> active
curl -fsS http://127.0.0.1:8000/api/status | head -c 200
```
The status endpoint should return JSON containing `"active": true` and
`"dry_run": true`.

If a service fails:
```bash
journalctl -u newsoft-orchestrator -n 100 --no-pager
journalctl -u newsoft-dashboard -n 100 --no-pager
```
Common causes:
- Wrong DB password (psql auth fails) → re-run step 4.
- Missing `ANTHROPIC_API_KEY` (fails on first scheduler tick, not on boot;
  service should still come up).
- Port 8000 or 3000 already used → identify the conflict, change the port in
  `.env` and the systemd unit, or stop the conflicting process.

### 6. Configure Caddy
```bash
sudo install -m 644 /opt/newsoft/infra/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```
**Verify** that Caddy got a cert (within ~30s):
```bash
journalctl -u caddy -n 50 --no-pager | grep -i "certificate obtained"
curl -fsS -o /dev/null -w "%{http_code}\n" https://firm.profithub.me/api/status
# expect: 200
```

### 7. End-to-end smoke test
```bash
curl -fsS -X POST https://firm.profithub.me/api/goals \
    -H "content-type: application/json" \
    -d '{"title":"Smoke test","description":"verify discovery loop"}'
curl -fsS -X POST https://firm.profithub.me/api/discovery/run
sleep 90
curl -fsS https://firm.profithub.me/api/events | python3 -c "import sys,json; \
    [print(e['kind'], '-', e['actor'], '-', e['message']) for e in json.load(sys.stdin)[:10]]"
```
Expected events (most recent first):
- `discovery_complete`
- `memo_written`
- `discovery_scored`
- one or more `agent_run` entries
- `goal_created`

Then:
```bash
curl -fsS https://firm.profithub.me/api/memos | python3 -c \
    "import sys,json; m=json.load(sys.stdin); print(m[0]['recommendation'] if m else 'NO MEMO')"
```
Should print `fund`, `explore`, `pass`, or `hold`. If `NO MEMO`, dump
orchestrator logs and report.

### 8. Final report to operator

Report:
- All seven `systemctl is-active` checks: pass/fail
- TLS cert acquired: yes/no
- Smoke test events observed: list of `kind` values
- First memo recommendation
- `spend_today_usd` from `/api/status` (should be ~$0.05–$0.30)

## Failure / rollback

If anything in steps 2–6 fails irrecoverably:
```bash
sudo systemctl disable --now newsoft-orchestrator newsoft-dashboard caddy 2>/dev/null
sudo rm -f /etc/systemd/system/newsoft-*.service /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo rm -rf /opt/newsoft
sudo userdel newsoft 2>/dev/null
sudo -u postgres psql -c "DROP DATABASE IF EXISTS newsoft;"
sudo -u postgres psql -c "DROP ROLE IF EXISTS newsoft;"
```
Then report the failure with the relevant logs and stop.

## Out of scope for Phase 1

- No real-money tools wired up (everything is dry-run).
- Hourly discovery tick is enabled by default; first manual run is the smoke
  test in step 7. To pause until the operator is ready, the agent can
  immediately engage the kill switch:
  ```bash
  curl -fsS -X POST https://firm.profithub.me/api/system/kill
  ```
  Re-enable later with:
  ```bash
  curl -fsS -X POST https://firm.profithub.me/api/system \
      -H "content-type: application/json" -d '{"active":true}'
  ```
