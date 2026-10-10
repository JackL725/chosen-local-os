#!/bin/bash
# Runs once, when the Postgres volume is first created (official image init hook).
# Creates the Ledger and n8n databases and their owner logins. Passwords come from
# vps.env through the container environment and are never written to disk here.
# The Ledger's application logins (cl_worker, cl_n8n, cl_reporting) are created after
# the first migration by deploy/postgres/create_logins.sh, because their group roles
# come from the migration.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username postgres \
     -v ledger_pw="$LEDGER_OWNER_PASSWORD" -v n8n_pw="$N8N_DB_PASSWORD" <<'SQL'
CREATE ROLE cl_migrate LOGIN CREATEROLE PASSWORD :'ledger_pw';
CREATE DATABASE ledger OWNER cl_migrate;
CREATE ROLE n8n LOGIN PASSWORD :'n8n_pw';
CREATE DATABASE n8n OWNER n8n;
REVOKE CONNECT ON DATABASE ledger FROM PUBLIC;
REVOKE CONNECT ON DATABASE n8n FROM PUBLIC;
GRANT CONNECT ON DATABASE n8n TO n8n;
SQL
