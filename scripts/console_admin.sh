#!/bin/bash
# Manage founder logins for the review console. Run with sudo on the workstation:
#   sudo /opt/chosen-local/scripts/console_admin.sh create-user jack --allow-local
#   sudo /opt/chosen-local/scripts/console_admin.sh list
# It runs python -m review.admin as clconsole with the console's own environment, the same
# way the cl-review unit does, so the console's database login is read by systemd as root
# and is never passed on a command line or visible to clworker.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo $0 $*" >&2
  exit 1
fi
exec systemd-run --quiet --pty --wait --collect \
  --uid=clconsole --gid=clconsole -p SupplementaryGroups=clprivate \
  -p EnvironmentFile=/home/clworker/.config/chosen-local/worker.env \
  -p EnvironmentFile=/etc/chosen-local/console.env \
  -p WorkingDirectory=/opt/chosen-local \
  -- /opt/chosen-local/.venv/bin/python -m review.admin "$@"
