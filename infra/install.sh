#!/usr/bin/env bash
# NewSoft installer for Ubuntu 24.04 with an existing Postgres on localhost.
# Idempotent: safe to re-run.
# Usage: sudo bash infra/install.sh [REPO_DIR]

set -euo pipefail

REPO_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
INSTALL_DIR="/opt/newsoft"
SERVICE_USER="newsoft"
BACKUP_USER="newsoft-backup"
BACKUP_DIR="/var/backups/newsoft"

echo "==> Installing NewSoft from ${REPO_DIR} into ${INSTALL_DIR}"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root (sudo)." >&2
    exit 1
fi

echo "==> Installing system packages"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip postgresql-client \
    curl ca-certificates gnupg certbot nginx
if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi
if ! command -v caddy >/dev/null 2>&1; then
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
fi

echo "==> Creating service users"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
if ! id -u "$BACKUP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/home/${BACKUP_USER}" --shell /usr/sbin/nologin "$BACKUP_USER"
fi
install -d -m 700 -o "$BACKUP_USER" -g "$BACKUP_USER" "$BACKUP_DIR"
install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" /var/lib/newsoft/sites
install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_USER" /var/lib/newsoft/well-known
install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_USER" /etc/nginx/newsoft-sites
cat >/usr/local/sbin/newsoft-nginx-reload <<'SH'
#!/bin/bash
set -e
/usr/sbin/nginx -t
/bin/systemctl reload nginx
SH
cat >/usr/local/sbin/newsoft-issue-cert <<'SH'
#!/bin/bash
set -e
domain="$1"
case "$domain" in
  *.*) ;;
  *) echo "invalid domain"; exit 2;;
esac
/usr/bin/certbot certonly --webroot   -w /var/lib/newsoft/well-known   --non-interactive --agree-tos   -m "${CERTBOT_EMAIL:-founder@profithub.me}"   -d "$domain" -d "www.$domain"
SH
chmod 0755 /usr/local/sbin/newsoft-nginx-reload /usr/local/sbin/newsoft-issue-cert
chown root:root /usr/local/sbin/newsoft-nginx-reload /usr/local/sbin/newsoft-issue-cert
cat >/etc/sudoers.d/newsoft <<'SUDO'
newsoft ALL=(root) NOPASSWD: /usr/local/sbin/newsoft-nginx-reload
newsoft ALL=(root) NOPASSWD: /usr/local/sbin/newsoft-issue-cert *
SUDO
chmod 0440 /etc/sudoers.d/newsoft

echo "==> Syncing code to ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
    --exclude '.next' --exclude '__pycache__' --exclude '.env' --exclude '.ssh' \
    "${REPO_DIR}/" "${INSTALL_DIR}/"

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
    echo "==> No .env found; copying from .env.example. EDIT IT BEFORE STARTING."
    cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
    chmod 600 "${INSTALL_DIR}/.env"
fi

echo "==> Bootstrapping Postgres role + database"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'newsoft') THEN
        CREATE ROLE newsoft LOGIN PASSWORD 'changeme';
    END IF;
END$$;
SELECT 'CREATE DATABASE newsoft OWNER newsoft'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'newsoft')\gexec
SQL

echo "==> Installing backup timer"
DB_PASS=""
if grep -q '^POSTGRES_PASSWORD=' "${INSTALL_DIR}/.env"; then
    DB_PASS="$(grep '^POSTGRES_PASSWORD=' "${INSTALL_DIR}/.env" | tail -1 | cut -d= -f2-)"
fi
if [[ -n "$DB_PASS" ]]; then
    printf 'localhost:5432:newsoft:newsoft:%s\n' "$DB_PASS" > "/home/${BACKUP_USER}/.pgpass"
    chown "$BACKUP_USER:$BACKUP_USER" "/home/${BACKUP_USER}/.pgpass"
    chmod 600 "/home/${BACKUP_USER}/.pgpass"
else
    echo "    WARNING: POSTGRES_PASSWORD not found; create /home/${BACKUP_USER}/.pgpass manually."
fi
install -m 644 "${INSTALL_DIR}/infra/systemd/newsoft-backup.service" /etc/systemd/system/
install -m 644 "${INSTALL_DIR}/infra/systemd/newsoft-backup.timer" /etc/systemd/system/

echo "==> Building orchestrator venv"
python3.12 -m venv "${INSTALL_DIR}/orchestrator/.venv"
"${INSTALL_DIR}/orchestrator/.venv/bin/pip" install --upgrade pip wheel
"${INSTALL_DIR}/orchestrator/.venv/bin/pip" install -e "${INSTALL_DIR}/orchestrator"

echo "==> Building dashboard"
cd "${INSTALL_DIR}/dashboard"
npm ci || npm install
npm run build

echo "==> Setting ownership"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
chmod 600 "${INSTALL_DIR}/.env"
chown "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}/.env"

echo "==> Installing systemd units"
install -m 644 "${INSTALL_DIR}/infra/systemd/newsoft-orchestrator.service" /etc/systemd/system/
install -m 644 "${INSTALL_DIR}/infra/systemd/newsoft-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable newsoft-orchestrator newsoft-dashboard newsoft-backup.timer
systemctl start newsoft-backup.timer

cat <<'NEXT'

==> Install complete.
Verify:
  systemctl status newsoft-orchestrator newsoft-dashboard
  systemctl list-timers | grep newsoft-backup
NEXT
