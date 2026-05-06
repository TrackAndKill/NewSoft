# Phase 8 build plan

For the hermes coding agent. Build spec — hermes implements, deploys,
writes its own short upgrade runbook (`infra/UPGRADE_PHASE8.md`), and
pushes to origin.

**Branch:** `claude/setup-project-architecture-t7WMs` (continue from HEAD).
**Repo:** `https://github.com/trackandkill/newsoft.git`

---

## Where we are

After Phase 7:
- Stage-gated experiments live (research → outreach_draft → validation_run).
- Vector memory wired into Board, CEO, Validator, CTO, Postmortem.
- `tools/outreach.py` exists with skeleton send_email — caps, idempotency,
  dry-run path. Smoke deliberately did not enable live Stage 3 sends.

What's missing for outreach to be safe in production:
1. Suppression list (no recheck before send).
2. No bounce / complaint / unsubscribe webhook from Resend.
3. RFC 8058 List-Unsubscribe headers absent — current code only does
   a substring check for "unsubscribe" in body.
4. No per-recipient-domain cap. An agent could send to 50 people at
   the same company and toast our domain reputation.
5. No throttle. Agent could fire 100 emails in 30 seconds.
6. No verification that the From-address domain is actually verified
   with Resend at boot time.
7. No operator UI for suppressions or stop-this-experiment.

Phase 8 closes all seven gaps. Theme: **outreach done right; no surprises**.

## Operator decisions baked into this plan

| Question | Default | Override |
|---|---|---|
| Per-recipient-domain cap | 3 sends per experiment to any single domain | env `OUTREACH_DOMAIN_CAP` |
| Throttle | min 60s between sends per experiment | env `OUTREACH_THROTTLE_SECONDS` |
| Default per-batch size | 10 recipients per `send_outreach_batch` approval | code default |
| Suppression sources | bounces, complaints, unsubscribes, manual | code default |
| Webhook signature verification | Resend's HMAC SHA256 (Svix-compatible header) | required; reject if invalid |
| List-Unsubscribe-Post | One-click via GET on a signed URL (RFC 8058) | required |
| Live outreach in Phase 8 smoke | NO — smoke only exercises dry-run paths | hard rule |

## New operator inputs

| Var | What it's for |
|---|---|
| `RESEND_WEBHOOK_SECRET` | shared secret for verifying Resend webhook signatures |
| `OUTREACH_DOMAIN_CAP` | optional override (default 3) |
| `OUTREACH_THROTTLE_SECONDS` | optional override (default 60) |

`RESEND_API_KEY` and `DIGEST_FROM_EMAIL` from earlier phases are reused.

---

## Scope — eight deliverables, in priority order

### 8A. Suppression list + pre-send check (small, must-ship)

Goal: an immutable list of recipients we will never email, checked
before every send.

#### Data model
New table `email_suppressions`:
```
id BIGSERIAL PRIMARY KEY,
recipient_hash VARCHAR(64) NOT NULL,    -- sha256(lower(email))
recipient_domain VARCHAR(255) NOT NULL, -- the domain part, indexed
reason VARCHAR(40) NOT NULL,            -- 'bounce', 'complaint',
                                        -- 'unsubscribe', 'manual',
                                        -- 'webhook_invalid',
                                        -- 'domain_block'
source VARCHAR(120),                    -- 'resend_webhook',
                                        -- 'operator', 'unsub_link'
provider_id VARCHAR(120),               -- Resend webhook event id
metadata JSONB,
created_at TIMESTAMPTZ DEFAULT now(),
UNIQUE (recipient_hash)
```

Plus an additional table for whole-domain blocks:
```
email_domain_blocks (id, domain, reason, source, created_at)
```

#### Pre-send check
In `send_email`, before any other validation:
```python
def is_suppressed(email: str) -> tuple[bool, str | None]:
    rh = _recipient_hash(email)
    domain = email.split("@")[-1].lower()
    with session_scope() as s:
        if s.scalar(select(EmailSuppression).where(EmailSuppression.recipient_hash == rh)):
            return True, "recipient_suppressed"
        if s.scalar(select(EmailDomainBlock).where(EmailDomainBlock.domain == domain)):
            return True, "domain_blocked"
    return False, None
```
If suppressed: log to `outreach_sends` with `status='suppressed'` and a
reason, return immediately. Do not increment caps. Do not call Resend.

#### Manual suppression endpoint
- `POST /api/suppressions` — body `{email, reason='manual', note?}`,
  basicauth required (operator only). Hashes the email, inserts.
- `POST /api/domain_blocks` — body `{domain, reason='manual', note?}`,
  same auth.
- `GET /api/suppressions` — list (most recent first), with reason
  breakdown.

Acceptance:
- Send to a suppressed email returns `{status:'suppressed'}` with
  no Resend call and no cap consumption.
- Manual `POST /api/suppressions` with a known email blocks
  subsequent sends.

### 8B. Resend webhook handler (small, must-ship)

Goal: bounces, complaints, unsubs from Resend land in the suppression
list automatically.

#### Endpoint
`POST /api/public/webhooks/resend` — exempt from basicauth (already
handled by nginx exemption from Phase 5). Body is JSON; signature is
in headers.

#### Signature verification
Resend uses Svix; HMAC-SHA256 over the raw body with
`RESEND_WEBHOOK_SECRET`. Headers:
- `svix-id`
- `svix-timestamp`
- `svix-signature` (format: `v1,<base64>`)

```python
def verify_resend_signature(body_bytes: bytes, headers: dict) -> bool:
    secret = settings.resend_webhook_secret
    if not secret:
        return False
    # Svix scheme: signed_content = f"{svix_id}.{svix_timestamp}.{body}"
    # Compare HMAC SHA256 against any of the comma-separated v1 sigs
```
Reject (401) any unsigned or wrong-signed request. Log
`Event(kind='webhook_rejected', actor='resend')` for visibility but
don't accept.

Reject events older than 5 minutes (replay protection).

#### Event handling
Map Resend event types to suppression reasons:
| Resend event | reason |
|---|---|
| `email.bounced` (hard) | `bounce` |
| `email.bounced` (soft) | log only, no suppression |
| `email.complained` | `complaint` |
| `email.delivery_delayed` | log only |
| `email.delivered` | log only; update `outreach_sends.status='delivered'` |
| `email.opened` | log only; update `outreach_sends.opened_at` |
| `email.clicked` | log only; update `outreach_sends.clicked_at` |

For suppression-triggering events:
- Insert/upsert `email_suppressions` row.
- Mark any pending `outreach_sends` for the same `recipient_hash` as
  `cancelled`.

#### Data model
Extend `outreach_sends` (idempotent ALTER):
- `delivered_at TIMESTAMPTZ`
- `opened_at TIMESTAMPTZ`
- `clicked_at TIMESTAMPTZ`
- `bounced_at TIMESTAMPTZ`
- `complained_at TIMESTAMPTZ`
- `cancelled BOOLEAN DEFAULT false`

Acceptance:
- Hand-crafted curl with valid signature against
  `/api/public/webhooks/resend` containing a `email.bounced` event
  for a known send: writes a suppression row and marks the
  outreach_send.bounced_at.
- Same payload with bad signature: 401, no suppression.
- Same payload with timestamp 10 min old: 401.

### 8C. List-Unsubscribe + one-click unsub (small, must-ship)

Goal: real RFC 8058 compliance. Email clients (Gmail bulk-sender
requirements explicitly mandate this) will honor the headers and
some auto-unsubscribe on bulk-mark-as-spam.

#### Headers added to every send
```
List-Unsubscribe: <https://firm.profithub.me/api/public/unsubscribe?t=<TOKEN>>, <mailto:unsub@profithub.me?subject=unsub-<TOKEN>>
List-Unsubscribe-Post: List-Unsubscribe=One-Click
```

`<TOKEN>` is a signed URL-safe string containing `recipient_hash` and
`experiment_id`, signed with `LEAD_IP_HASH_PEPPER` (or a new
`UNSUBSCRIBE_SECRET` env var if hermes prefers separation; document
the choice).

#### Endpoint
`POST /api/public/unsubscribe?t=<TOKEN>` — exempt from basicauth.
- Verifies signature.
- Inserts `email_suppressions` row with `reason='unsubscribe'`,
  `source='unsub_link'`.
- Returns 200 with a tiny "You're unsubscribed" HTML page.

`GET /api/public/unsubscribe?t=<TOKEN>` accepted for List-Unsubscribe
clients that issue GET (older clients); same behavior.

#### Footer auto-injection
Modify `send_email` to:
- Reject the operator-supplied "unsubscribe" substring check (the
  header-based one is enough, but keep the substring check as a
  belt-and-suspenders for plain-text clients that don't honor
  headers).
- Auto-append a plain-text and HTML footer with the URL form of the
  same unsubscribe link.
- Build the `List-Unsubscribe` and `List-Unsubscribe-Post` headers.
  Resend's API supports the `headers` field on email send.

Acceptance:
- A dry-run send produces an `outreach_sends` row whose stored
  payload (or whatever the audit captures) contains both headers.
- POST to `/api/public/unsubscribe?t=<valid-token>` returns 200 and
  inserts a suppression.
- POST with a tampered token: 400.

### 8D. Per-recipient-domain cap (tiny, must-ship)

Goal: refuse to send to >N recipients at the same domain within one
experiment. Default N=3.

In `send_email`, after suppression check and before existing caps:
```python
domain = to.split("@")[-1].lower()
with session_scope() as s:
    domain_count = s.scalar(
        select(func.count(OutreachSend.id))
        .where(OutreachSend.experiment_id == experiment_id)
        .where(OutreachSend.recipient_domain == domain)
        .where(OutreachSend.cancelled == False)
    ) or 0
if domain_count >= settings.outreach_domain_cap:
    raise RuntimeError(f"per-domain cap reached for {domain}")
```

Add `recipient_domain VARCHAR(255)` column to `outreach_sends`
(idempotent ALTER); backfill existing rows from `recipient_hash`
isn't possible (one-way hash), so leave existing rows with
`recipient_domain=NULL`; only new rows enforce.

Acceptance:
- 3 sends to `a@x.com`, `b@x.com`, `c@x.com` all succeed.
- 4th send to `d@x.com` raises with the per-domain cap message.

### 8E. Throttle (small, must-ship)

Goal: at least N seconds between consecutive sends within an
experiment. Default 60s. Prevents agent from firing batches at
machine speed.

In `send_email`:
```python
with session_scope() as s:
    last = s.scalar(
        select(OutreachSend.created_at)
        .where(OutreachSend.experiment_id == experiment_id)
        .order_by(desc(OutreachSend.created_at))
    )
if last is not None:
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    if elapsed < settings.outreach_throttle_seconds:
        wait = settings.outreach_throttle_seconds - elapsed
        raise RuntimeError(f"throttled; retry in {wait:.0f}s")
```
Note: this raises rather than blocks. Validator's batch runner
catches the error and reschedules via APScheduler — Phase 8 does NOT
add new schedulers; the existing `validator_tick` retry loop handles
it. Document this in the runbook.

Acceptance:
- Two sends within 60s on the same experiment: second raises
  `throttled` and is not consumed against per-day cap.

### 8F. From-domain verification at boot (small)

Goal: refuse to start outreach if the From-address domain isn't
actually verified with Resend.

#### Implementation
At startup, after `init_db()`:
```python
def verify_from_domain() -> None:
    if not settings.resend_api_key:
        log.warning("RESEND_API_KEY unset; outreach will dry-run only")
        return
    if not settings.digest_from_email:
        log.warning("DIGEST_FROM_EMAIL unset; outreach will refuse")
        return
    domain = settings.digest_from_email.split("@")[-1]
    # Resend domains.list endpoint
    resp = httpx.get(
        "https://api.resend.com/domains",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        timeout=10,
    )
    resp.raise_for_status()
    verified = [
        d for d in resp.json().get("data", [])
        if d.get("name") == domain and d.get("status") == "verified"
    ]
    if not verified:
        log.error("From domain %s NOT verified; outreach disabled", domain)
        # Set a process-level flag; send_email checks it.
```
Run once at boot. Cache the result. Re-check on a daily cron tick.

If unverified: every `send_email` call (even dry-run is fine; live is
not) raises `RuntimeError("from-domain not verified with Resend")`.

Acceptance:
- With `DIGEST_FROM_EMAIL=founder@profithub.me` and Resend domain
  `profithub.me` actually verified (operator must do this once),
  startup logs `from_domain_verified=true`.
- Without verification, live sends raise; dry-run continues to work.

### 8G. send_outreach_batch approval action (small, must-ship)

Goal: outreach is gated per batch, not per individual send. The
Validator's Stage 3 plan files multiple batch approvals (e.g. 5
batches of 10 = 50 sends), each independently approvable.

#### New approval action
- `send_outreach_batch` payload:
  ```json
  {
    "experiment_id": 7,
    "stage_id": 21,
    "recipients": ["a@x.com", "b@y.com", ...],
    "subject": "...",
    "body_text": "...",
    "body_html": "...",
    "copy_variant": "A",
    "estimated_count": 10
  }
  ```
- Hard limit: `len(recipients) <= settings.outreach_per_batch_max`
  (default 10). Validator must split larger lists across multiple
  approvals.

#### Dispatcher
`POST /api/approvals/{id}` with action `send_outreach_batch` →
`outreach.execute_batch(approval_id)`:
- Honors `execute_live` (Phase 6); if not live, all sends use
  `dry_run=True`.
- Iterates recipients, calling `send_email` for each.
- Catches throttle errors and yields back to APScheduler — partial
  completion is allowed; the same approval is re-runnable.
- When all recipients are processed (or suppressed/throttled), marks
  approval `status='completed'` and writes a result event with
  per-recipient outcomes.

#### Validator updates
`validator.py` Stage 3 plan generator now emits a list of
`send_outreach_batch` approvals (one per ≤10-recipient slice) instead
of a single mega-approval. Validator's Stage 3 prompt includes the
caps as constraints.

Acceptance:
- A simulated Stage 3 run on memo #14 produces 1+ pending
  `send_outreach_batch` approvals each with ≤10 recipients.
- Approving one in execute_live=false sends dry-run sends; rows
  appear in `outreach_sends` with `status='dry_run'`.

### 8H. Operator UI: suppressions + outreach (small)

Goal: operator can see suppressions and outreach activity without SQL.

Pages:
- `dashboard/app/suppressions/page.tsx` — list, breakdown by reason,
  manual add form (email or domain).
- `dashboard/app/outreach/page.tsx` — list of `outreach_sends` with
  recent activity, per-experiment counts, status pills.
- Extend `experiments/[id]` page to show outreach sends in context
  of stages.

Acceptance:
- `/suppressions` shows current suppression list with reason filter.
- Manual add form posts to `/api/suppressions` and the new entry
  shows up.
- `/outreach` lists recent sends.

---

## Cross-cutting requirements

### Migrations
- New tables: `email_suppressions`, `email_domain_blocks`.
- New columns on `outreach_sends`: `recipient_domain`, `delivered_at`,
  `opened_at`, `clicked_at`, `bounced_at`, `complained_at`,
  `cancelled` — all idempotent ALTERs.
- No destructive changes.

### Env vars added in Phase 8
| Var | Required | Notes |
|---|---|---|
| `RESEND_WEBHOOK_SECRET` | for live webhook | If unset, the webhook endpoint always returns 401. |
| `OUTREACH_DOMAIN_CAP` | optional | Default 3. |
| `OUTREACH_THROTTLE_SECONDS` | optional | Default 60. |
| `OUTREACH_PER_BATCH_MAX` | optional | Default 10. |
| `UNSUBSCRIBE_SECRET` | optional | If unset, reuses `LEAD_IP_HASH_PEPPER`. |

### Module layout (additions only)
```
orchestrator/orchestrator/
  tools/
    outreach.py        (extend: suppression check, throttle, domain
                        cap, headers, batch runner, from-verification)
  webhooks.py          (new — resend signature verification)
  db/models.py         (extend with EmailSuppression, EmailDomainBlock;
                        extend OutreachSend columns)
  db/init_db.py        (extend with new ALTERs)
  api.py               (extend endpoints: webhooks, suppressions,
                        unsubscribe, outreach list)
  agents/validator.py  (Stage 3 emits batch approvals)
dashboard/
  app/suppressions/page.tsx   (new)
  app/outreach/page.tsx       (new)
  app/experiments/[id]/page.tsx  (extend)
  lib/api.ts                  (extend)
infra/
  UPGRADE_PHASE8.md           (new — hermes writes this)
docs/
  PHASE8_PLAN.md              (this file)
  OUTREACH_GO_LIVE.md         (new — operator runbook for first
                               live send: domain verification,
                               DKIM/SPF/DMARC checklist, test send
                               procedure, kill switch)
```

### Things hermes should NOT do
- Do NOT enable live cold-email sending in the Phase 8 smoke. All
  testing stays in dry-run / simulated. The infra is built; the
  operator decides when to flip the first live `execute_live=true`
  on a `send_outreach_batch` approval.
- Do NOT bypass the from-domain verification check. Even dry-run
  should respect it once a `RESEND_API_KEY` is configured (so that
  flipping to live mode doesn't reveal new failures).
- Do NOT log `RESEND_WEBHOOK_SECRET`, `UNSUBSCRIBE_SECRET`, raw
  recipient emails, or unsubscribe tokens.
- Do NOT reduce any existing cap. Throttle, per-domain, per-day,
  per-experiment caps are all additive.
- Do NOT change Stage 1 / Stage 2 behavior from Phase 7. Phase 8
  affects Stage 3 only.
- Do NOT migrate to React 19.
- Do NOT add a second money tool.
- Do NOT remove the operator-supplied "unsubscribe" substring check
  in send_email — keep both belt and suspenders.

### Acceptance for Phase 8 as a whole
After hermes deploys and runs the smoke checks:
- Migrations: new tables + columns present.
- Suppression: manual add via API works; pre-send check blocks
  suppressed addresses.
- Webhook: hand-crafted signed POST writes a suppression; bad
  signature 401; replay (>5 min) 401.
- Headers: dry-run send records `List-Unsubscribe` and
  `List-Unsubscribe-Post` headers in audit.
- Per-domain cap: 4th send to same domain rejected.
- Throttle: 2nd send within 60s rejected.
- Batch approval: Stage 3 generates one or more
  `send_outreach_batch` approvals each with ≤10 recipients.
- From-domain verification: with verified domain, startup logs
  success; without, send_email raises live-mode error.
- Operator UI: `/suppressions` and `/outreach` pages render.
- `infra/OUTREACH_GO_LIVE.md` exists and is referenced from README.
- System ends ACTIVE, dry-run, $20 LLM cap, $50 money cap.

---

## Final report from hermes

| Section | Content |
|---|---|
| Code | latest commit hash; deliverables shipped (8A–8H) |
| Migrations | confirm new tables + columns |
| Suppression | manual-add test result; suppressed-recipient blocked-send proof |
| Webhook | valid signature accepted; bad signature 401; replay 401 |
| Headers | dry-run send shows both List-Unsubscribe headers |
| Throttle / domain cap | both proven by test sends |
| From-domain | verified at boot? logged result |
| Batch approval | Stage 3 produces N batches of ≤10 each |
| UI | suppressions + outreach pages render |
| Docs | OUTREACH_GO_LIVE.md present and linked |
| State | system active? caps? dry-run? today's spend |
| Issues | non-fatal warnings, things deferred to Phase 9 |

End of plan.
