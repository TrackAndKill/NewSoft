# Outreach Go-Live Runbook

This is the operator checklist for the first ever live outreach send. Do not use it during Phase 8 smoke; Phase 8 smoke stays dry-run only.

## 0. Preconditions

- The system has completed Phase 8 smoke checks in dry-run.
- The global dashboard shows `ACTIVE` and `dry-run` until the operator intentionally flips live mode.
- A `send_outreach_batch` approval exists and has been reviewed recipient-by-recipient.
- The batch has no more than `OUTREACH_PER_BATCH_MAX` recipients.
- The operator has reviewed exact subject, copy, landing link, success metric, budget, and kill criteria.

## 1. DNS records to verify

In Resend, open the sending domain used by `DIGEST_FROM_EMAIL`. Verify these records exactly as Resend displays them:

- DKIM: CNAME/TXT records for Resend signing.
- SPF: TXT record authorizing Resend to send for the domain.
- DMARC: TXT record for `_dmarc.<domain>`.
- Optional but recommended: custom return-path/bounce domain if configured in Resend.

Do not guess values from this runbook; copy them from the Resend domain page because each domain gets provider-specific tokens.

## 2. Configure secrets

Set only the values needed for live outreach:

```bash
sudoedit /opt/newsoft/.env
```

Required for live send:

```bash
RESEND_API_KEY=<from Resend>
DIGEST_FROM_EMAIL=<verified sender on the verified domain>
RESEND_WEBHOOK_SECRET=<Resend webhook signing secret>
PUBLIC_DASHBOARD_URL=https://firm.profithub.me
OUTREACH_DOMAIN_CAP=3
OUTREACH_THROTTLE_SECONDS=60
OUTREACH_PER_BATCH_MAX=10
```

Optional:

```bash
UNSUBSCRIBE_SECRET=<random high-entropy string>
```

If `UNSUBSCRIBE_SECRET` is unset, NewSoft reuses `LEAD_IP_HASH_PEPPER` for signed unsubscribe links.

Never paste these secrets into chat, logs, screenshots, commits, or tickets.

## 3. Restart and verify from-domain

```bash
sudo systemctl restart newsoft-orchestrator
journalctl -u newsoft-orchestrator -n 80 --no-pager | grep 'from_domain_verified'
```

Expected with a verified Resend domain:

```text
from_domain_verified=true domain=<your-domain>
```

If it says false, stop. Do not send live.

## 4. Self-test in dry-run first

Keep global dry-run enabled and approve one batch with `execute_live=false`.

Verify:

```bash
curl -sS http://127.0.0.1:8000/api/outreach | python3 -m json.tool
```

Expected:

- rows show `status: dry_run`
- rows include `audit_payload.headers.List-Unsubscribe`
- rows include `audit_payload.headers.List-Unsubscribe-Post`
- no live email is received because this was dry-run

## 5. Webhook self-test

After configuring Resend's webhook URL:

```text
https://firm.profithub.me/api/public/webhooks/resend
```

Send Resend's test webhook from the Resend dashboard if available, or wait for the first real delivery event after live mode. Bad signatures should receive `401`.

## 6. Flip live mode intentionally

Only after all checks pass:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/system \
  -H 'Content-Type: application/json' \
  -d '{"dry_run":false}'
```

Then approve exactly one reviewed `send_outreach_batch` with live execution. Do not bulk-approve multiple batches on the first live run.

## 7. Watch the first send

```bash
journalctl -u newsoft-orchestrator -f
```

In a second shell:

```bash
curl -sS http://127.0.0.1:8000/api/outreach | python3 -m json.tool
curl -sS http://127.0.0.1:8000/api/suppressions | python3 -m json.tool
```

Stop if you see unexpected bounces, complaints, wrong copy, wrong recipients, or a cap behaving unexpectedly.

## 8. Literal kill switch

If anything looks wrong, run this immediately:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/system/kill
```

This halts rituals by setting the system inactive. To be extra conservative, also restore dry-run:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/system \
  -H 'Content-Type: application/json' \
  -d '{"dry_run":true}'
```

If needed, stop the orchestrator service:

```bash
sudo systemctl stop newsoft-orchestrator
```

## 9. Porkbun FRAUD_BLOCK during domain shakedowns

Porkbun can return a fraud block on first registrations from a new account, new IP, or new API key. The symptom during a domain registration approval is HTTP 400 with:

```json
{"status":"ERROR","code":"FRAUD_BLOCK","message":"Unable to process order at this time. (002)"}
```

Resolution path:

- Complete account verification in the Porkbun web UI.
- If the account remains blocked, contact `support@porkbun.com` and reference the blocked order and code `002`.
- Rotate Porkbun API keys after support unblocks the account, because the original keys are now associated with a flagged order.
- Before the next agent-driven registration, pre-warm Porkbun by buying one domain manually in the web UI so the account's first programmatic order is not also its first order ever.
