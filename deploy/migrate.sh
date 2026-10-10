#!/bin/bash
# Apply Ledger migrations on the VPS. Run from anywhere:  sudo /opt/chosen-local-vps/deploy/migrate.sh
# Connects to Postgres on 127.0.0.1:5432 as cl_migrate (password from vps.env).
set -euo pipefail
set -a; . /etc/chosen-local/vps.env; set +a
DBMATE=/usr/local/bin/dbmate
[ -x "$DBMATE" ] || { echo "Install dbmate first (deploy/README.md step V6)" >&2; exit 1; }
pw="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$LEDGER_OWNER_PASSWORD")"
DATABASE_URL="postgres://cl_migrate:${pw}@127.0.0.1:5432/ledger?sslmode=disable" \
  "$DBMATE" --migrations-dir /opt/chosen-local-vps/db/migrations --no-dump-schema --wait "${1:-up}"
