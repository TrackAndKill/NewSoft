# Key rotation runbook

Rotate secrets from the provider first, then update `/opt/newsoft/.env`, restart the affected services, and run the listed verification. Never print secrets in shell history or logs.

## General pre-flight

```bash
sudo test -f /opt/newsoft/.env
sudo cp -a /opt/newsoft/.env /opt/newsoft/.env.backup.$(date +%Y%m%d%H%M%S)
sudo systemctl is-active newsoft-orchestrator newsoft-dashboard postgresql nginx
```

Edit secrets with:

```bash
sudoedit /opt/newsoft/.env
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
```

## Anthropic — `ANTHROPIC_API_KEY`

1. Create a new API key at https://console.anthropic.com/.
2. Replace `ANTHROPIC_API_KEY` in `/opt/newsoft/.env`.
3. Restart orchestrator.
4. Verify the API can still run a lightweight model-backed endpoint/workflow:

```bash
sudo systemctl restart newsoft-orchestrator
curl -fsS http://127.0.0.1:8000/api/status >/dev/null
sudo journalctl -u newsoft-orchestrator --since '5 minutes ago' --no-pager | tail -80
```

Rollback: restore the backup `.env` if the new key fails, restart orchestrator, then revoke the failed key.

## Brave Search — `BRAVE_API_KEY` / `BRAVE_SEARCH_API_KEY`

1. Rotate the key in the Brave Search API dashboard.
2. Replace both configured Brave key env names if present.
3. Restart orchestrator.
4. Run a discovery/search smoke that uses Brave, or inspect the next discovery run for `ToolCall` rows and no Brave auth errors.

```bash
sudo systemctl restart newsoft-orchestrator
sudo journalctl -u newsoft-orchestrator --since '10 minutes ago' --no-pager | grep -i brave || true
```

Rollback: restore old key if still valid, restart, then reissue a clean Brave key.

## Resend — `RESEND_API_KEY`

1. Create a replacement key at https://resend.com/.
2. Replace `RESEND_API_KEY` in `/opt/newsoft/.env`.
3. Restart orchestrator.
4. Trigger/send a digest only when the operator expects email; otherwise verify service startup and wait for scheduled digest.

```bash
sudo systemctl restart newsoft-orchestrator
curl -fsS -X POST http://127.0.0.1:8000/api/digest/run
sudo journalctl -u newsoft-orchestrator --since '5 minutes ago' --no-pager | tail -80
```

Rollback: restore previous key if not revoked; otherwise pause digest until Resend is fixed.

## Porkbun — `PORKBUN_API_KEY` / `PORKBUN_API_SECRET`

1. Rotate API credentials in Porkbun account settings.
2. Replace `PORKBUN_API_KEY` and `PORKBUN_API_SECRET` in `/opt/newsoft/.env`.
3. Restart orchestrator.
4. Verify with a read/check call, not a purchase:

```bash
sudo systemctl restart newsoft-orchestrator
cd /opt/newsoft/orchestrator
sudo -u newsoft /opt/newsoft/orchestrator/.venv/bin/python - <<'PY'
from orchestrator.tools.domains import domain_check
print(domain_check('example.com'))
PY
```

Rollback: restore old key if still valid. If Porkbun shows invalid credentials, disable live domain approvals and keep `dry_run=true` until fixed.

## Dashboard Basic Auth — `NEWSOFT_BASIC_HASH`

Regenerate using Caddy's hash function without logging the plaintext password:

```bash
read -rsp 'New dashboard password: ' PASS; printf '\n'
HASH=$(printf '%s\n' "$PASS" | caddy hash-password)
unset PASS
sudoedit /opt/newsoft/.env   # replace NEWSOFT_BASIC_HASH with $HASH manually
sudo systemctl restart newsoft-dashboard
curl -ks -o /dev/null -w '%{http_code}\n' https://firm.profithub.me/          # expect 401 without auth
```

Rollback: restore `.env` backup and restart dashboard.

## Lead IP pepper — `LEAD_IP_HASH_PEPPER`

Rotating this changes future IP hashes. Historical lead hashes cannot be compared to future submissions unless you run a one-time backfill from raw IPs, which NewSoft intentionally does not store. Normal rotation accepts that old and new hashes will not match.

```bash
openssl rand -hex 32
sudoedit /opt/newsoft/.env   # replace LEAD_IP_HASH_PEPPER
sudo systemctl restart newsoft-orchestrator
```

Post-flight: submit one test lead to a staging/site signup endpoint and confirm a new `Lead` row appears. Rollback only if rate limiting or lead capture breaks.

## GitHub deploy key

1. Generate a replacement key as `newsoft`:

```bash
sudo -u newsoft ssh-keygen -t ed25519 -f /opt/newsoft/.ssh/github_deploy_new -C 'newsoft-deploy' -N ''
sudo -u newsoft cat /opt/newsoft/.ssh/github_deploy_new.pub
```

2. Add the public key to GitHub repo deploy keys with write access if pushes are required.
3. Update `/opt/newsoft/.ssh/config` or the repo remote to use the new key.
4. Verify:

```bash
sudo -u newsoft git -C /opt/newsoft ls-remote origin >/dev/null
```

Rollback: keep the old deploy key until the new one verifies; then remove the old key from GitHub and delete old private key files.
