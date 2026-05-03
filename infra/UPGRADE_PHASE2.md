# NewSoft — Phase 2 upgrade runbook

In-place upgrade of an existing Phase 1 deployment. Adds:

1. Caddy basicauth on `firm.profithub.me` (single account: `founder`).
2. Board pod (Growth / Operator / Skeptic partners) that reviews each memo
   and votes fund / explore / pass.
3. CEO charter generation on a FUND'd memo, producing a Venture record.
4. Two new dashboard surfaces: `/board`, `/ventures` (and `/ventures/<slug>`).
5. Idempotent column migration for `memos.decision` / `memos.decision_at`.

**Branch:** `claude/setup-project-architecture-t7WMs`
**Reference commit:** the latest commit on that branch — fetch and rebase.

## Inputs the agent needs from the operator (do not log)

| Variable | Required | What it is |
|---|---|---|
| `DASHBOARD_PASSWORD` | yes | The password the operator will type into the browser when visiting `firm.profithub.me`. The username is fixed as `founder`. Operator picks the value; agent generates the bcrypt hash. |

The agent does not need any other new inputs.

## Preconditions to verify

1. `systemctl is-active newsoft-orchestrator` returns `active`.
2. `systemctl is-active newsoft-dashboard` returns `active`.
3. `systemctl is-active caddy` returns `active`.
4. `curl -fsS http://127.0.0.1:8000/api/status` returns JSON with `"active"`.
5. `test -d /opt/newsoft && test -f /opt/newsoft/.env`.
6. `command -v caddy` resolves (we need `caddy hash-password`).

## Step-by-step plan

### 1. Pull the latest code into the staging clone
```bash
cd /tmp/newsoft
sudo -u $(stat -c %U .) git fetch origin claude/setup-project-architecture-t7WMs
sudo -u $(stat -c %U .) git checkout claude/setup-project-architecture-t7WMs
sudo -u $(stat -c %U .) git pull --ff-only origin claude/setup-project-architecture-t7WMs
git rev-parse HEAD   # record this in the final report
```
If `/tmp/newsoft` no longer exists, re-clone:
```bash
sudo rm -rf /tmp/newsoft
git clone https://github.com/trackandkill/newsoft.git /tmp/newsoft
cd /tmp/newsoft && git checkout claude/setup-project-architecture-t7WMs
```

### 2. Sync code into /opt/newsoft (excluding live state)
```bash
sudo rsync -a --delete \
    --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
    --exclude '.next' --exclude '__pycache__' --exclude '.env' \
    /tmp/newsoft/ /opt/newsoft/
sudo chown -R newsoft:newsoft /opt/newsoft
```
Note the `--exclude '.env'` — never overwrite the live secrets file.

### 3. Generate the basic-auth bcrypt hash
```bash
NEWSOFT_BASIC_HASH="$(caddy hash-password --plaintext "${DASHBOARD_PASSWORD}")"
```
The hash starts with `$2a$` or `$2b$`. Do not log `DASHBOARD_PASSWORD` after this point.

### 4. Append `NEWSOFT_BASIC_HASH` to `/opt/newsoft/.env`
```bash
# Remove any prior NEWSOFT_BASIC_HASH line, then append the new one.
sudo sed -i '/^NEWSOFT_BASIC_HASH=/d' /opt/newsoft/.env
echo "NEWSOFT_BASIC_HASH=${NEWSOFT_BASIC_HASH}" | sudo tee -a /opt/newsoft/.env >/dev/null
sudo chmod 600 /opt/newsoft/.env
sudo chown newsoft:newsoft /opt/newsoft/.env
```

### 5. Re-install the orchestrator package (no new system deps)
```bash
sudo -u newsoft /opt/newsoft/orchestrator/.venv/bin/pip install -e /opt/newsoft/orchestrator
```
No new Python dependencies were added in Phase 2 — this is a fast no-op,
but it ensures any new modules are picked up.

### 6. Rebuild the Next.js dashboard
```bash
cd /opt/newsoft/dashboard
sudo -u newsoft npm ci || sudo -u newsoft npm install
sudo -u newsoft npm run build
```

### 7. Install the Caddy systemd drop-in so caddy reads NEWSOFT_BASIC_HASH
```bash
sudo install -d /etc/systemd/system/caddy.service.d
sudo install -m 644 /opt/newsoft/infra/systemd/caddy.service.d/newsoft-env.conf \
    /etc/systemd/system/caddy.service.d/newsoft-env.conf
sudo systemctl daemon-reload
```

### 8. Replace the Caddyfile (now with basicauth)
```bash
sudo install -m 644 /opt/newsoft/infra/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy   # restart, not reload — needed to pick up EnvironmentFile
sleep 5
systemctl is-active caddy
```
A `restart` (not `reload`) is required because EnvironmentFile is only read on
process start, not on SIGHUP.

### 9. Restart the orchestrator and dashboard
```bash
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
sleep 5
systemctl is-active newsoft-orchestrator
systemctl is-active newsoft-dashboard
```
On orchestrator boot, `init_db()` runs the idempotent column migration that
adds `memos.decision` and `memos.decision_at`. Verify via the orchestrator log:
```bash
journalctl -u newsoft-orchestrator -n 50 --no-pager
```
Should not contain `ProgrammingError` or `relation "memos" does not exist`.

### 10. Verify auth + endpoints
```bash
# Without auth: 401
test "$(curl -fsS -o /dev/null -w '%{http_code}' https://firm.profithub.me/api/status)" = "401"

# With auth: 200
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/status >/dev/null

# New endpoints
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/ventures
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/board/reviews
```

### 11. Smoke-test the Board pod end to end
The Board picks up any `decision = pending` memo. If the Phase 1 smoke test
already produced a memo, run the board now:
```bash
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" -X POST https://firm.profithub.me/api/board/run
sleep 90
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/board/reviews \
    | python3 -c "import sys,json; \
        rs=json.load(sys.stdin); print(len(rs), 'reviews'); \
        [print(r['persona'], '-', r['vote']) for r in rs[:6]]"
```
Expected: at least 3 reviews (one per persona) for the most recent memo.

If the Board votes FUND, the CEO charters automatically. Verify:
```bash
curl -fsS -u "founder:${DASHBOARD_PASSWORD}" https://firm.profithub.me/api/ventures \
    | python3 -c "import sys,json; \
        vs=json.load(sys.stdin); \
        print(len(vs), 'ventures'); \
        [print(v['slug'], '-', v['status']) for v in vs[:3]]"
```

### 12. Browser check
- Open `https://firm.profithub.me` — browser should prompt for credentials.
- After login, top nav shows: Overview / Goals / Ideas / Memos / Board / Ventures / Approvals / Activity.
- `/board` lists all reviews with vote pills.
- `/ventures` lists ventures (may be empty if Board didn't FUND anything yet).
- `/ventures/<slug>` shows charter + reviews + memo + idea.

## Final report from the agent

- ✅/❌ Each precondition
- Latest commit hash applied
- ✅/❌ rsync to `/opt/newsoft` succeeded
- ✅/❌ orchestrator `pip install -e` exit 0
- ✅/❌ dashboard `npm run build` exit 0
- ✅/❌ caddy restarted with EnvironmentFile drop-in
- ✅/❌ HTTP 401 without auth, 200 with auth
- ✅/❌ `init_db` log clean (no ProgrammingError)
- Number of board reviews observed for latest memo
- Number of ventures (if any) and their slugs
- `spend_today_usd` from `/api/status`

## Failure / rollback

To revert to the Phase 1 state (basic auth disabled, no board/venture features):
```bash
# Revert Caddyfile to no-auth version
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
firm.profithub.me {
    encode gzip
    handle /api/* { reverse_proxy 127.0.0.1:8000 }
    handle { reverse_proxy 127.0.0.1:3000 }
}
EOF
sudo systemctl restart caddy

# Roll back the code
cd /tmp/newsoft && git checkout c5ce58e -- .   # the Phase 1 commit
sudo rsync -a --delete \
    --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
    --exclude '.next' --exclude '__pycache__' --exclude '.env' \
    /tmp/newsoft/ /opt/newsoft/
cd /opt/newsoft/dashboard && sudo -u newsoft npm run build
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
```
The new tables (`board_reviews`, `ventures`) and columns (`memos.decision`,
`memos.decision_at`) are harmless to leave in place; old code does not read them.

## Constraints

- Never overwrite `/opt/newsoft/.env`. Always preserve it across rsync.
- Never log `DASHBOARD_PASSWORD` or `NEWSOFT_BASIC_HASH`.
- If `caddy hash-password` is missing, run `apt-get install -y caddy` first;
  do not install caddy from any non-Cloudsmith source.
- If the orchestrator fails to start after migration, the operator must be
  notified before any data-touching rollback. The migration is additive; the
  failure is much more likely to be config than data.
