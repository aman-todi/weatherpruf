#!/usr/bin/env bash
# Create (or recreate) a local database and apply the local auth shim plus every
# migration, in order.
#
#   ./scripts/db_apply.sh [dbname] [readonly-password]
#
# Against a real Supabase project you do not use this script — run the files in
# db/migrations/ in filename order through the SQL editor or the Supabase CLI,
# and skip db/local/00_auth_shim.sql entirely.
set -euo pipefail

DB="${1:-wardrobe_dev}"
RO_PASSWORD="${2:-${RO_PASSWORD:-readonly-local-dev}}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

psql_as_postgres() { su postgres -c "psql -v ON_ERROR_STOP=1 -q $(printf '%q ' "$@")"; }

echo "==> recreating database $DB"
psql_as_postgres -d postgres -c "drop database if exists $DB" >/dev/null
psql_as_postgres -d postgres -c "create database $DB" >/dev/null

export PGOPTIONS="-c client_min_messages=warning"
echo "==> applying db/local/00_auth_shim.sql (local only)"
psql_as_postgres -d "$DB" -f "$ROOT/db/local/00_auth_shim.sql" >/dev/null

for f in "$ROOT"/db/migrations/*.sql; do
    echo "==> applying $(basename "$f")"
    psql_as_postgres -d "$DB" -f "$f" >/dev/null
done

psql_as_postgres -d postgres -c "alter role wardrobe_readonly with password '$RO_PASSWORD'" >/dev/null
unset PGOPTIONS

echo
echo "Ready. Connection strings for backend/.env:"
echo "  DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/$DB"
echo "  READONLY_DATABASE_URL=postgresql://wardrobe_readonly:$RO_PASSWORD@127.0.0.1:5432/$DB"
