# Phase 5 build plan

For the hermes coding agent. Build spec — hermes implements, deploys,
writes its own short upgrade runbook (`infra/UPGRADE_PHASE5.md`), and
pushes to origin.

**Branch:** `claude/setup-project-architecture-t7WMs` (continue from HEAD).
**Repo:** `https://github.com/trackandkill/newsoft.git`

---

## Where we are

After Phase 4:
- Discovery, Board, Validator, CEO, CTO + Engineer pod, costs, run
  inspector, digest, Porkbun domain registration (dry-run gated through
  approval queue) all live.
- $20/day LLM cap, $50/day money cap, dry-run by default.
- One seed venture (`Phase 4 Smoke Venture`) with a plan + tasks,
  including an approved domain registration (simulated).

What's missing: the firm can buy a domain but can't yet *do anything
with it*. No DNS, no live site, no email capture. Phase 5 closes that
loop end to end so a chartered venture can ship a real landing page on
its own domain and start collecting leads — all approval-gated, dry-run
safe.

## Operator decisions baked into this plan

| Question | Default | Override |
|---|---|---|
| Where do venture sites live? | `/var/lib/newsoft/sites/<slug>/` on the VM, served by nginx | code default |
| TLS provider | Let's Encrypt via certbot --webroot | code default |
| DNS provider for ventures | Porkbun (already integrated for registrations) | new tool extends Phase 4's `tools/domains.py` |
| CDN / proxy | none (defer Cloudflare to later) | n/a |
| Public API path | `/api/public/*` exempt from nginx basicauth, CORS open | code default |
| Site delivery | single static `index.html` with inline CSS, no build step | code default |
| Lead storage | Postgres `leads` table; basic IP rate-limit | code default |

If the operator hasn't stated a preference, hermes uses these.

## New operator inputs (all optional; graceful unset everywhere)

| Var | What it's for |
|---|---|
| `VM_IPV4` | The VM's public IPv4 address. Required only for live deploys. |
| `CERTBOT_EMAIL` | Email for Let's Encrypt registration (terms + expiration alerts). |

`PORKBUN_API_KEY` and `PORKBUN_API_SECRET` from Phase 4 are reused.
Without them, all deployment paths stay in dry-run / staging mode.

---

## Scope — six deliverables, in priority order

### 5A. Porkbun DNS tool (small)

Extend `orchestrator/orchestrator/tools/domains.py` (or create
`tools/porkbun_dns.py` and consolidate later) with:

```python
def list_dns_records(domain: str) -> list[dict]: ...
def add_dns_record(domain: str, *, name: str, type: str, content: str,
                   ttl: int = 600) -> dict: ...
def delete_dns_record(domain: str, record_id: str) -> dict: ...
```

Endpoint: Porkbun `/dns/retrieve/{domain}`, `/dns/create/{domain}`,
`/dns/delete/{domain}/{id}`.

Acceptance:
- With keys configured: `add_dns_record("test.com", name="@", type="A",
  content="1.2.3.4")` succeeds and the record appears in
  `list_dns_records`.
- Without keys: raises `ToolUnavailable`. The deploy pipeline (5C)
  treats this as "dry-run / staging" and proceeds to a stage where
  operator can complete manually.
- No money cost; no approval gate (DNS changes are reversible and
  free).

### 5B. Copywriter agent (small)

New agent: `orchestrator/orchestrator/agents/copywriter.py`.

```python
COPYWRITER = AgentSpec(
    name="copywriter",
    role="Copywriter",
    model=settings.model_sonnet,
    max_tokens=3000,
    system_prompt=(
        "You are the Copywriter for an autonomous venture. Given a "
        "venture charter and target ICP, produce a single-file static "
        "landing page (HTML5 with inline CSS, no JS frameworks, no "
        "external fonts/CDN). Required sections: hero with headline + "
        "subhead, 3-5 value-prop bullets, a single primary CTA: an "
        "email signup form that POSTs to {SIGNUP_URL}. The form must "
        "include an `email` field, a hidden `source=landing` field, "
        "and a hidden `slug={SLUG}` field. After submit, show a thank-"
        "you message. The page must be under 30 KB. No tracking "
        "scripts. No analytics. No third-party requests. "
        "Return STRICT JSON: {\"html\":\"<!doctype...\",\"meta\":{\"title\":\"...\",\"description\":\"...\"}}. "
        "No prose outside the JSON."
    ),
)

def draft_landing_page(venture_id: int, signup_url: str) -> int:
    """Returns the SiteContent row id."""
```

Persist HTML in a new `site_contents` table:
```
id, venture_id (FK), kind ('landing_page'),
html (Text), meta_json (JSON),
agent_run_id (FK agent_runs nullable),
created_at
```

The HTML stays in the DB until 5C deploys it to disk. This makes
re-deploys easy (no re-generation needed).

Acceptance:
- For an existing venture, `draft_landing_page` produces a SiteContent
  row whose HTML parses (basic structure: contains `<form`, `email`
  input, `{SIGNUP_URL}` baked into the form action).
- HTML size <= 30 KB.
- Run cost <= $0.10 (Sonnet, no tools).

### 5C. Site deployment pipeline (medium — the meat)

Module: `orchestrator/orchestrator/tools/sites.py`.

#### Data model
New table `sites`:
```
id, venture_id (FK), slug (str unique, default = venture.slug),
domain (str nullable),  -- e.g. "example-venture.com"; null until a
                        -- domain is registered AND linked
deploy_dir (str),       -- "/var/lib/newsoft/sites/<slug>"
status (str),           -- staging | dns_pending | nginx_ready |
                        -- tls_pending | live | failed
last_error (Text nullable),
deployed_at (TIMESTAMPTZ nullable),
created_at, updated_at
```

#### Functions
```python
def stage_site(site_id: int) -> dict:
    """Write current SiteContent.html to deploy_dir/index.html. Always
       safe; no nginx, no DNS. Sets status='staging'."""

def request_dns(site_id: int) -> dict:
    """Add an A record (root + www) pointing the site's domain at
       VM_IPV4 via Porkbun. Sets status='dns_pending'. Idempotent: if
       records already exist with correct content, no-op."""

def install_nginx_vhost(site_id: int) -> dict:
    """Write /etc/nginx/newsoft-sites/<slug>.conf with the domain as
       server_name, root pointed at deploy_dir, /.well-known/acme-
       challenge/ served from /var/lib/newsoft/well-known. HTTP only
       at this stage (no listen 443 yet). Reload nginx via the
       wrapper script (see install.sh changes). Sets status=
       'nginx_ready'."""

def issue_cert(site_id: int) -> dict:
    """certbot certonly --webroot via wrapper script. Once cert
       written, rewrite vhost to also `listen 443 ssl http2` and
       redirect HTTP -> HTTPS. Reload nginx. Sets status='live'."""

def teardown_site(site_id: int) -> dict:
    """Remove vhost, reload nginx, optionally remove cert and DNS
       records. Sets status='failed' or deletes the row depending on
       caller."""
```

#### DNS readiness check (between request_dns and install_nginx_vhost)
A new ritual `site_tick` (every 5 min) walks `dns_pending` sites:
- Resolves the site's domain via stdlib `socket.gethostbyname` against
  multiple resolvers (8.8.8.8, 1.1.1.1).
- If it returns `VM_IPV4`, advance to `install_nginx_vhost` then
  `issue_cert`.
- If it doesn't resolve yet, leave alone (will retry next tick).
- After 6 hours of `dns_pending`, mark `failed` with last_error =
  "DNS did not propagate".

#### Approval flow integration
Three new approval actions, all dispatched from the existing
`POST /api/approvals/{id}` switch:
- `configure_dns` → `sites.request_dns`
- `deploy_landing_page` → `sites.stage_site` then schedule
  `install_nginx_vhost` + `issue_cert` once DNS is ready
- `teardown_site` → `sites.teardown_site` (rare; for kill flow later)

Each approval payload includes `{site_id, domain}` for traceability.

#### Dry-run behavior
When `system_state.dry_run = true`:
- `request_dns`: log a `simulated` event; do not call Porkbun.
- `install_nginx_vhost`: write the .conf file BUT skip nginx reload
  (so the change has no live effect); set status to `nginx_ready
  (simulated)`.
- `issue_cert`: skip certbot entirely; set status to `live
  (simulated)`.
- `site_tick`: skip the resolution check; advance immediately.

This means dry-run path runs the entire state machine end to end with
zero external side effects — perfect for smoke-testing on a venture
before flipping to live.

#### Install-time changes
Extend `infra/install.sh` to:
1. Create directories:
   - `/var/lib/newsoft/sites/` (newsoft:newsoft, 750)
   - `/var/lib/newsoft/well-known/` (newsoft:newsoft, 755)
   - `/etc/nginx/newsoft-sites/` (newsoft:newsoft, 755)
2. Add include directive to nginx (only if not already present):
   - `infra/nginx-newsoft.conf` already exists for the firm's
     dashboard. Add a snippet `infra/nginx-newsoft-sites-include.conf`
     containing `include /etc/nginx/newsoft-sites/*.conf;` and the
     install script appends it to `/etc/nginx/conf.d/newsoft.conf`.
3. Install certbot:
   - `apt-get install -y certbot`.
4. Install root-owned wrapper scripts:
   - `/usr/local/sbin/newsoft-nginx-reload`:
     ```bash
     #!/bin/bash
     set -e
     /usr/sbin/nginx -t
     /bin/systemctl reload nginx
     ```
   - `/usr/local/sbin/newsoft-issue-cert`:
     ```bash
     #!/bin/bash
     set -e
     domain="$1"
     case "$domain" in
       *.*) ;;
       *) echo "invalid domain"; exit 2;;
     esac
     /usr/bin/certbot certonly --webroot \
         -w /var/lib/newsoft/well-known \
         --non-interactive --agree-tos \
         -m "${CERTBOT_EMAIL:-founder@profithub.me}" \
         -d "$domain" -d "www.$domain"
     ```
   Both root-owned, mode 0755.
5. Install sudoers drop-in `/etc/sudoers.d/newsoft` (mode 440):
   ```
   newsoft ALL=(root) NOPASSWD: /usr/local/sbin/newsoft-nginx-reload
   newsoft ALL=(root) NOPASSWD: /usr/local/sbin/newsoft-issue-cert *
   ```
6. Install `infra/systemd/newsoft-site-tick.timer` (every 5 min)
   pointing at a `newsoft-site-tick.service` that runs
   `python -m orchestrator.tools.sites_tick`. (Or fold into the
   existing APScheduler — APScheduler is the right home; skip the
   systemd timer and add `site_tick` alongside the other ticks.)

The orchestrator runs `sudo /usr/local/sbin/newsoft-nginx-reload` and
`sudo /usr/local/sbin/newsoft-issue-cert <domain>` from Python via
`subprocess.run(..., check=True)`. No other privileged commands.

Acceptance (dry-run):
- For the existing seed venture or a newly-seeded one, calling
  `stage_site` writes `/var/lib/newsoft/sites/<slug>/index.html`.
- `request_dns` (dry-run) logs a `simulated` event; no Porkbun call.
- `install_nginx_vhost` (dry-run) writes the .conf file in
  `/etc/nginx/newsoft-sites/`; nginx is NOT reloaded; site_tick can
  see the file but doesn't reload either.
- Site row reaches status `live (simulated)`.

Acceptance (live, only if all keys + DNS prerequisites set):
- A test domain like `phase5-test.<your-domain>` (subdomain of an
  existing zone) goes through the full flow: DNS A record added →
  resolves to VM IP → vhost installed → cert issued → site live on
  HTTPS.
- This is gated behind operator approval at every step (DNS
  configuration is one approval; deploy_landing_page is another).

### 5D. Email capture (small)

New table `leads`:
```
id, site_id (FK sites), email (str, indexed), source (str),
ip_hash (str),    -- sha256(client_ip), not the IP itself
user_agent (str), referer (str nullable),
created_at
```

Public endpoint (no auth, CORS open, lightweight rate-limit):
```
POST /api/public/sites/<slug>/signup
  body: { "email": "...", "source": "landing" }
```
- Validate email format (basic regex).
- Look up site by slug.
- Hash client IP (sha256 with a fixed pepper from
  `LEAD_IP_HASH_PEPPER` env var, default to a static fallback if
  unset).
- Rate limit: at most 5 submissions per ip_hash per 10 min per site.
  Track in-memory (simple dict with TTL); if more, return 429.
- Insert Lead row, return `{ok: true}`.

Read endpoints (basic auth, like the rest of the dashboard):
- `GET /api/sites` — list with lead counts
- `GET /api/sites/<slug>/leads` — list of leads (most recent first)

Dashboard:
- `dashboard/app/sites/page.tsx` — list of sites with status, domain,
  deploy_at, lead count.
- `dashboard/app/sites/[slug]/page.tsx` — site detail with status
  pill, link to live URL, leads table.
- Link from `dashboard/app/ventures/[slug]/page.tsx` to the venture's
  site if any.

Nginx changes for `/api/public/*`:
- The existing nginx vhost at `/etc/nginx/sites-available/newsoft`
  has a single `auth_basic` block on `/`. Add a `location ^~ /api/public/`
  block before it that disables auth and proxies to the orchestrator:
  ```
  location ^~ /api/public/ {
      auth_basic off;
      proxy_pass http://127.0.0.1:8000;
      ...standard proxy headers...
  }
  ```
- Update `infra/nginx-newsoft.conf` to include this. Document in the
  Phase 5 runbook that the operator's live nginx config must be
  patched to match.

CORS: FastAPI CORSMiddleware is already permissive (allow_origins=*,
all methods, all headers). Confirm `/api/public/*` is reachable from
any origin.

Acceptance:
- `curl -X POST https://firm.profithub.me/api/public/sites/<slug>/signup
   -d '{"email":"x@y.com","source":"landing"}' -H 'content-type: application/json'`
  returns 200 with `{ok: true}` and creates a Lead row.
- 6th submission from the same IP hash within 10 min returns 429.
- The dashboard's `/sites/<slug>` page shows the new lead.
- Without auth, `GET /api/public/sites/...` returns 405 (only POST
  exists). Without auth, `GET /api/sites/...` returns 401.

### 5E. Venture pod integration (small)

Extend `orchestrator/orchestrator/agents/engineer.py` so the task list
includes (in this order, for any chartered venture):

1. `register_domain` — already supported (Phase 4)
2. `configure_dns` (new approval action) — payload `{domain, vm_ipv4}`
3. `draft_landing_page` (NOT approval-gated; runs immediately and
   creates a SiteContent row)
4. `deploy_landing_page` (new approval action) — payload
   `{site_id, domain}`

The Engineer agent's prompt is updated to know about these actions.
After registering a domain, the next task is configuring DNS; after
that, drafting the landing page (free, automatic); after that,
approving the deploy.

`venture_tick` (already exists) extends to:
- Run Copywriter for any chartered venture that has a domain but no
  SiteContent row yet (drafting the landing page).
- Run `sites.stage_site` once the deploy_landing_page approval is
  approved.
- Run `site_tick` step inline (or schedule as separate APScheduler
  job — preferred).

Acceptance:
- The seed venture's plan now includes (or can be re-planned to
  include) configure_dns + deploy_landing_page tasks.
- Approving them in dry-run advances the Site through its states to
  `live (simulated)` without external side effects.
- The dashboard shows the venture → site → leads chain.

### 5F. Public API exemption (tiny)

Already covered by 5D's nginx change. The piece to flag separately:

- `/api/public/*` must be exempt from nginx basicauth.
- Update `infra/nginx-newsoft.conf` to reflect this for any future
  reinstall.
- The runbook hermes writes must include the exact patch the operator
  needs to apply to their live `/etc/nginx/sites-available/newsoft`.

Acceptance:
- Browser visit to `https://firm.profithub.me/` still prompts for
  auth.
- `curl -X POST https://firm.profithub.me/api/public/sites/<slug>/signup`
  works without `-u founder:...`.
- Confirmed by visiting the live landing page (once deployed live)
  and submitting the form from a real browser → Lead row created.

---

## Cross-cutting requirements

### Migrations
- New tables: `sites`, `leads`, `site_contents`.
- No new columns on existing tables.
- Use `Base.metadata.create_all` (idempotent for new tables).

### Env vars added in Phase 5
| Var | Required | Notes |
|---|---|---|
| `VM_IPV4` | live deploys only | the VM's public IPv4 |
| `CERTBOT_EMAIL` | live deploys only | for Let's Encrypt registration |
| `LEAD_IP_HASH_PEPPER` | optional | random string; defaults to a fixed fallback if unset |

Add to `.env.example`.

### Module layout (additions only)
```
orchestrator/orchestrator/
  agents/
    copywriter.py           (new)
    engineer.py             (extend prompt + task generation)
  tools/
    sites.py                (new)
    domains.py              (extend with DNS functions OR move DNS to
                             tools/porkbun_dns.py)
  db/models.py              (extend with Site, SiteContent, Lead)
  rituals/scheduler.py      (extend with site_tick)
  api.py                    (extend with /api/sites/*, /api/public/*)
dashboard/
  app/sites/page.tsx               (new)
  app/sites/[slug]/page.tsx        (new)
  lib/api.ts                       (extend)
infra/
  systemd/                          (no new units; APScheduler does it)
  nginx-newsoft.conf                (extend with /api/public/ exemption)
  install.sh                        (extend: dirs, wrappers, sudoers,
                                     certbot install)
  UPGRADE_PHASE5.md                 (new — hermes writes this)
docs/
  PHASE5_PLAN.md                    (this file)
```

### Things hermes should NOT do
- Do not give the `newsoft` user broad sudo. Only the two wrapper
  scripts.
- Do not write nginx configs anywhere other than
  `/etc/nginx/newsoft-sites/`.
- Do not deploy any landing page to the firm's own domain
  (`firm.profithub.me`) — that's reserved for the dashboard.
- Do not include any third-party JS, fonts, analytics, or trackers
  in landing-page HTML. Privacy is the policy.
- Do not auto-approve `configure_dns` or `deploy_landing_page`.
  Both go through the approval queue without exception.
- Do not change Phase 4's domain registration approval flow.
- Do not add real outbound email (cold outreach, marketing) yet.
  Ph5 is read/serve only on the public side.
- Do not log Porkbun / Anthropic / SMTP / Resend secrets, or the
  raw client IP for leads.
- Do not introduce per-venture isolation (separate processes,
  separate DB schemas, etc.) — single process, single DB.

### Acceptance for Phase 5 as a whole
After hermes deploys and runs the smoke checks:
- Code: pushed to origin; new commit hash recorded.
- Migrations: `sites`, `site_contents`, `leads` tables present.
- Wrappers: `/usr/local/sbin/newsoft-nginx-reload` and
  `/usr/local/sbin/newsoft-issue-cert` exist; sudoers drop-in
  installed; newsoft user can run them.
- nginx config: `/api/public/*` exempt from basicauth in the live
  vhost; reload succeeds.
- Copywriter: ran against the seed venture; SiteContent row created;
  HTML under 30 KB and contains an `email` form posting to
  `/api/public/sites/<slug>/signup`.
- Site state machine (dry-run): seed venture progressed through
  staging → dns_pending → nginx_ready → tls_pending → live (simulated)
  with all approvals filed and approved.
- Email capture: `curl POST /api/public/sites/<slug>/signup` with a
  valid email returns 200 and creates a Lead row visible in
  `/sites/<slug>` on the dashboard.
- Rate limit: 6th request from same IP hash within 10 min returns 429.
- System ends ACTIVE, dry-run, $20 LLM cap, $50 money cap.

---

## Final report from hermes

| Section | Content |
|---|---|
| Code | latest commit hash on origin; deliverables shipped (5A/B/C/D/E/F) |
| Migrations | confirm new tables; no errors in init_db log |
| Env | which Phase-5 vars provided; which deferred |
| Wrappers | both wrapper scripts installed; sudoers verified |
| nginx | `/api/public/*` exempt; reload succeeded |
| Copywriter | SiteContent created for seed venture; HTML size; signup URL baked in correctly |
| Pipeline | site state machine reached `live (simulated)`; events logged at each step |
| Capture | a test POST landed a Lead row; rate limit confirmed |
| State | system active? caps? dry-run? today's spend (LLM and money) |
| Issues | non-fatal warnings, things deferred to Phase 6 |

End of plan.
