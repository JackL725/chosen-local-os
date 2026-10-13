#!/bin/bash
# Create (or rotate) only the review console's login, cl_console. Run on the VPS:
#   sudo /opt/chosen-local-vps/deploy/postgres/create_console_login.sh
# Prints the password ONCE. It goes into CONSOLE_DATABASE_URL in
# /home/clworker/.config/chosen-local/worker.env on the workstation. This is the only
# Ledger login that may act as a founder (migration 2), so keep it off every other machine.
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -f compose.prod.yml --env-file /etc/chosen-local/vps.env)
PW=$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)
"${compose[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d ledger -v console_pw="$PW" <<'SQL'
SELECT 'CREATE ROLE cl_console LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cl_console') \gexec
ALTER ROLE cl_console PASSWORD :'console_pw';
GRANT ledger_console TO cl_console;
GRANT CONNECT ON DATABASE ledger TO cl_console;
SQL
cat <<OUT

cl_console is ready. Copy the password now; it will not be shown again.

  cl_console    $PW
      -> workstation: CONSOLE_DATABASE_URL in /home/clworker/.config/chosen-local/worker.env
OUT
