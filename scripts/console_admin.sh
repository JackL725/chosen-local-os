#!/bin/bash
# Manage founder logins for the review console. Run as clworker on the workstation:
#   sudo -u clworker /opt/chosen-local/scripts/console_admin.sh create-user jack --allow-local
# Reads the same environment files as the console's systemd unit.
set -euo pipefail
set -a
. /home/clworker/.config/chosen-local/worker.env
. /home/clworker/.config/chosen-local/console.env
set +a
cd /opt/chosen-local
exec /opt/chosen-local/.venv/bin/python -m review.admin "$@"
