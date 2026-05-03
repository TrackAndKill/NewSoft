#!/usr/bin/env bash
# NewSoft installer for Ubuntu 24.04 with an existing Postgres on localhost.
# Idempotent: safe to re-run.
#
# Usage: sudo bash infra/install.sh [REPO_DIR]
# REPO_DIR defaults to the parent of this script.

set -euo pipefail

REPO_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
INSTALL_DIR="/opt/newsoft"
SERVICE_USER="newsoft"

echo "==> Installing NewSoft from ${REPO_DIR} into ${INSTALL_DIR}"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root (sudo)." >&2
    exit 1
fi

echo "==> Installing system packages (python, node, caddy)"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip \
    curl ca-certificates gnupg
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

echo "==> Creating service user ${SERVICE_USER}"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> Syncing code to ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
    --exclude '.next' --exclude '__pycache__' \
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
echo "    NOTE: change the 'changeme' password in Postgres AND in /opt/newsoft/.env."

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

echo "==> Installing systemd units"
install -m 644 "${INSTALL_DIR}/infra/systemd/newsoft-orchestrator.service" /etc/systemd/system/
install -m 644 "${INSTALL_DIR}/infra/systemd/newsoft-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable newsoft-orchestrator newsoft-dashboard

cat <<'NEXT'

==> Install complete.

Next steps:
  1. Edit /opt/newsoft/.env (set ANTHROPIC_API_KEY and the Postgres password).
  2. Sync the Postgres password:
        sudo -u postgres psql -c "ALTER ROLE newsoft PASSWORD 'YOUR_PASSWORD';"
  3. Start services:
        systemctl start newsoft-orchestrator newsoft-dashboard
        systemctl status newsoft-orchestrator newsoft-dashboard
  4. (Optional) Configure Caddy:
        cp /opt/newsoft/infra/Caddyfile /etc/caddy/Caddyfile
        # edit YOUR_SUBDOMAIN
        systemctl reload caddy
NEXT
