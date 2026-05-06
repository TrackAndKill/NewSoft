# Phase 8 Upgrade Runbook

Phase 8 adds the live-outreach safety layer while keeping the system dry-run by default.

## What ships

- Suppression storage: `email_suppressions`, `email_domain_blocks`.
- `outreach_sends` audit columns for recipient domain, captured Resend payload, webhook timestamps, cancellation, and errors.
- Resend webhook endpoint with Svix/HMAC verification and a 5-minute replay window.
- RFC 8058 `List-Unsubscribe` and `List-Unsubscribe-Post` headers on every outreach payload.
- Additive caps: per-recipient-domain, per-experiment throttle, existing per-experiment/day limits.
- From-domain verification at boot via Resend `domains.list` when `RESEND_API_KEY` is set.
- `send_outreach_batch` approvals and Stage 3 batch splitting.
- Operator pages: `/suppressions` and `/outreach`.

## Environment variables

Optional unless noted by your provider setup:

```bash
RESEND_WEBHOOK_SECRET=        # if unset, webhook endpoint returns 401 by design
OUTREACH_DOMAIN_CAP=3
OUTREACH_THROTTLE_SECONDS=60
OUTREACH_PER_BATCH_MAX=10
UNSUBSCRIBE_SECRET=           # optional; falls back to LEAD_IP_HASH_PEPPER
```

Existing vars still matter:

```bash
RESEND_API_KEY=               # required only for live sending/from-domain verification
DIGEST_FROM_EMAIL=            # domain must be verified in Resend before live sends
PUBLIC_DASHBOARD_URL=https://firm.profithub.me
```

## Upgrade steps

```bash
cd /opt/newsoft
sudo -u newsoft git fetch origin claude/setup-project-architecture-t7WMs
sudo -u newsoft git checkout claude/setup-project-architecture-t7WMs
sudo -u newsoft git pull --ff-only origin claude/setup-project-architecture-t7WMs
sudo -u newsoft /opt/newsoft/orchestrator/.venv/bin/python -m orchestrator.db.init_db
sudo -u newsoft bash -lc 'cd /opt/newsoft/dashboard && npm install && npm run build'
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
```

## Post-upgrade checks

```bash
systemctl is-active newsoft-orchestrator newsoft-dashboard nginx postgresql redis-server
curl -sk -o /dev/null -w 'firm_https=%{http_code}\n' https://firm.profithub.me/
curl -sS http://127.0.0.1:8000/api/status
curl -sS http://127.0.0.1:8000/api/suppressions
curl -sS http://127.0.0.1:8000/api/outreach
```

Expected final posture after Phase 8 smoke:

- `active=true`
- `dry_run=true`
- LLM cap unchanged (`$20` on current deployment)
- money cap unchanged (`$50` on current deployment)
- no live sends performed

## Rollback

Phase 8 migrations are additive. To rollback code only:

```bash
cd /opt/newsoft
sudo -u newsoft git checkout <previous-known-good-commit>
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
```

Do not drop suppression/outreach columns during rollback; they are harmless and preserve audit history.
