#!/usr/bin/env bash
# Apply the migrations to a throwaway local database and run the Ticket 1
# definition-of-done checks against it: RLS blocks cross-user reads and writes,
# the column constraints hold, and the read-only role really is read-only.
#
#   ./scripts/db_verify.sh [dbname]
#
# Expects a local Postgres 16 and a superuser connection. Everything it creates
# is dropped and recreated on each run, so it is safe to re-run.
set -euo pipefail

DB="${1:-wardrobe_verify}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RO_PASSWORD="${RO_PASSWORD:-verify-only-not-a-secret}"

# psql as a superuser. Locally that means sudo-ing to the postgres OS user
# unless the caller points SUPERUSER_PSQL somewhere else.
if [[ -n "${SUPERUSER_DATABASE_URL:-}" ]]; then
    su_psql() { psql -v ON_ERROR_STOP=1 -q "${SUPERUSER_DATABASE_URL%/*}/$DB" "$@"; }
    su_admin() { psql -v ON_ERROR_STOP=1 -q "$SUPERUSER_DATABASE_URL" "$@"; }
else
    su_psql() { su postgres -c "psql -v ON_ERROR_STOP=1 -q -d $DB $(printf '%q ' "$@")"; }
    su_admin() { su postgres -c "psql -v ON_ERROR_STOP=1 -q -d postgres $(printf '%q ' "$@")"; }
fi

echo "==> recreating $DB"
su_admin -c "drop database if exists $DB" >/dev/null
su_admin -c "create database $DB" >/dev/null

echo "==> applying local auth shim + migrations"
# Migrations are idempotent, so DROP ... IF EXISTS chatter is expected noise.
export PGOPTIONS="-c client_min_messages=warning"
su_psql -f "$ROOT/db/local/00_auth_shim.sql" >/dev/null
for f in "$ROOT"/db/migrations/*.sql; do
    echo "    $(basename "$f")"
    su_psql -f "$f" >/dev/null
done

echo "==> giving wardrobe_readonly a password for this run"
su_admin -c "alter role wardrobe_readonly with password '$RO_PASSWORD'" >/dev/null

unset PGOPTIONS
echo "==> RLS and constraint checks"
su_psql -f "$ROOT/db/tests/verify_rls.sql"

echo "==> read-only role checks"
PGPASSWORD="$RO_PASSWORD" psql -v ON_ERROR_STOP=1 -q \
    -h "${PGHOST:-127.0.0.1}" -p "${PGPORT:-5432}" \
    -U wardrobe_readonly -d "$DB" \
    -f "$ROOT/db/tests/verify_readonly_role.sql"

echo
echo "All database checks passed."
