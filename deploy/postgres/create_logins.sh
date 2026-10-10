#!/bin/bash
# Creates the Ledger's application logins after the first migration.
# Run on the VPS, from /opt/chosen-local-vps:  sudo deploy/postgres/create_logins.sh
#
# It generates a random password for each login, sets it, and prints each one ONCE to
# this terminal. Put them straight into your password manager and the files named
# below. They are not stored anywhere else. Re-running rotates all three.
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -f compose.prod.yml --env-file /etc/chosen-local/vps.env)

gen() { openssl rand -base64 32 | tr -d '/+=' | cut -c1-32; }
WORKER_PW=$(gen); N8N_PW=$(gen); REPORTING_PW=$(gen)

"${compose[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d ledger \
  -v worker_pw="$WORKER_PW" -v n8n_pw="$N8N_PW" -v reporting_pw="$REPORTING_PW" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', r) FROM unnest(ARRAY['cl_worker', 'cl_n8n', 'cl_reporting']) AS r
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) \gexec
ALTER ROLE cl_worker    PASSWORD :'worker_pw';
ALTER ROLE cl_n8n       PASSWORD :'n8n_pw';
ALTER ROLE cl_reporting PASSWORD :'reporting_pw';
GRANT ledger_worker    TO cl_worker;
GRANT ledger_n8n       TO cl_n8n;
GRANT ledger_reporting TO cl_reporting;
GRANT CONNECT ON DATABASE ledger TO cl_worker, cl_n8n, cl_reporting;
SQL

cat <<OUT

Logins created. Copy each password now; it will not be shown again.

  cl_worker     $WORKER_PW
      -> workstation: DATABASE_URL in /home/clworker/.config/chosen-local/worker.env
  cl_n8n        $N8N_PW
      -> n8n: the "Ledger (n8n role)" Postgres credential
  cl_reporting  $REPORTING_PW
      -> read-only access for reports (store it; nothing uses it yet)

OUT
