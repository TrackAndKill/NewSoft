#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/newsoft}"
DATABASE_URL_RAW="${DATABASE_URL:-}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-newsoft}"
POSTGRES_DB="${POSTGRES_DB:-newsoft}"
MIN_BACKUP_BYTES="${MIN_BACKUP_BYTES:-10240}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
OUTPUT="$BACKUP_DIR/newsoft-$(date +%Y%m%d-%H%M%S).pgc"
TMP_OUTPUT="${OUTPUT}.tmp"
cleanup() {
  rm -f "$TMP_OUTPUT"
}
trap cleanup EXIT

if [[ -n "$DATABASE_URL_RAW" ]]; then
  # pg_dump accepts libpq URLs, not SQLAlchemy driver URLs like postgresql+psycopg://...
  DATABASE_URL_RAW=$(printf '%s' "$DATABASE_URL_RAW" | sed -E 's,^postgresql\+[^:]+://,postgresql://,')
  pg_dump "$DATABASE_URL_RAW" -Fc -f "$TMP_OUTPUT"
else
  pg_dump -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -Fc -f "$TMP_OUTPUT" "$POSTGRES_DB"
fi

if [[ ! -s "$TMP_OUTPUT" ]]; then
  rm -f "$TMP_OUTPUT"
  echo "Backup failed: empty output file" >&2
  exit 1
fi

actual_bytes=$(stat -c '%s' "$TMP_OUTPUT")
if (( actual_bytes < MIN_BACKUP_BYTES )); then
  rm -f "$TMP_OUTPUT"
  echo "Backup failed: suspiciously small output (${actual_bytes} bytes, minimum ${MIN_BACKUP_BYTES})" >&2
  exit 1
fi

mv "$TMP_OUTPUT" "$OUTPUT"
find "$BACKUP_DIR" -name 'newsoft-*.pgc' -mtime +"$RETENTION_DAYS" -delete
printf 'backup=%s bytes=%s\n' "$OUTPUT" "$actual_bytes"
