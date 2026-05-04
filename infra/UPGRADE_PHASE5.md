# NewSoft Phase 5 Upgrade Runbook

## Scope

Phase 5 ships the venture public-launch loop:

1. Porkbun DNS tools: list/create/delete DNS records.
2. Copywriter pod: DB-backed static landing page drafts, no trackers, no third-party JS.
3. Site deployment pipeline: `staging -> dns_pending -> nginx_ready -> tls_pending -> live`, dry-run safe.
4. Lead capture: unauthenticated `/api/public/sites/<slug>/signup`, rate limited, IP stored only as SHA256+pepper.
5. Engineer pod launch tasks: `configure_dns`, `draft_landing_page`, `deploy_landing_page`.
6. nginx exemption for `/api/public/*` while preserving Basic Auth everywhere else.

## Pre-flight

```bash
cd /tmp/newsoft
git fetch origin claude/setup-project-architecture-t7WMs
git checkout claude/setup-project-architecture-t7WMs
git pull --ff-only origin claude/setup-project-architecture-t7WMs
```

Do **not** overwrite `/opt/newsoft/.env`. Do **not** deploy venture sites to `firm.profithub.me`.

## Environment

Optional Phase 5 env vars:

```env
VM_IPV4=89.167.106.132
CERTBOT_EMAIL=operator@example.com
LEAD_IP_HASH_PEPPER=<random-long-string>
```

`PORKBUN_API_KEY` and `PORKBUN_API_SECRET` are reused from Phase 4. If unset, DNS/deployment stays dry-run/manual and safe.

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

## Install Phase 5 directories/wrappers

```bash
sudo install -d -m 750 -o newsoft -g newsoft /var/lib/newsoft/sites
sudo install -d -m 755 -o newsoft -g newsoft /var/lib/newsoft/well-known
sudo install -d -m 755 -o newsoft -g newsoft /etc/nginx/newsoft-sites
sudo apt-get update -y
sudo apt-get install -y certbot

sudo tee /usr/local/sbin/newsoft-nginx-reload >/dev/null <<'SH'
#!/bin/bash
set -e
/usr/sbin/nginx -t
/bin/systemctl reload nginx
SH

sudo tee /usr/local/sbin/newsoft-issue-cert >/dev/null <<'SH'
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
SH

sudo chmod 0755 /usr/local/sbin/newsoft-nginx-reload /usr/local/sbin/newsoft-issue-cert
sudo chown root:root /usr/local/sbin/newsoft-nginx-reload /usr/local/sbin/newsoft-issue-cert

sudo tee /etc/sudoers.d/newsoft >/dev/null <<'SUDO'
newsoft ALL=(root) NOPASSWD: /usr/local/sbin/newsoft-nginx-reload
newsoft ALL=(root) NOPASSWD: /usr/local/sbin/newsoft-issue-cert *
SUDO
sudo chmod 0440 /etc/sudoers.d/newsoft
sudo visudo -cf /etc/sudoers.d/newsoft
```

Only these two privileged wrapper scripts are allowed for `newsoft`.

## Exact nginx patch for live `/etc/nginx/sites-available/newsoft`

Place this block **inside the existing HTTPS `server { ... }` for `firm.profithub.me` and before `location /api/`**:

```nginx
    # Public venture signup API. Must stay before /api/ so basicauth is off.
    location ^~ /api/public/ {
        auth_basic off;
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
```

Also add this top-level include once if missing, so future venture vhosts are loaded:

```nginx
include /etc/nginx/newsoft-sites/*.conf;
```

Then verify and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Expected behavior:
- `https://firm.profithub.me/` still prompts for Basic Auth.
- `POST https://firm.profithub.me/api/public/sites/<slug>/signup` does not require Basic Auth.
- `GET https://firm.profithub.me/api/sites` still requires Basic Auth.

## Restart and activate

```bash
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
curl -fsS -X POST http://127.0.0.1:8000/api/system \
  -H 'content-type: application/json' \
  -d '{"active":true,"dry_run":true,"daily_spend_cap_usd":20.0,"money_daily_cap_usd":50.0}'
```

## Smoke checks

```bash
curl -fsS http://127.0.0.1:8000/api/status
curl -fsS http://127.0.0.1:8000/api/sites
curl -fsS http://127.0.0.1:3000/sites | grep Sites
```

For a site slug:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/public/sites/<slug>/signup \
  -H 'content-type: application/json' \
  -d '{"email":"phase5@example.com","source":"landing"}'
```

The 6th signup from the same client within 10 minutes should return HTTP `429`.
