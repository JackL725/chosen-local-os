#!/bin/bash
# Called by cl-unit-failed@.service. Sends the unit name and host only.
set -euo pipefail
unit="${1//[^A-Za-z0-9@._-]/}"
[ -n "${SLACK_ALERT_WEBHOOK_URL:-}" ] || exit 0
curl -fsS -m 10 -H 'Content-Type: application/json' \
  -d "{\"text\": \":warning: systemd unit ${unit} failed on $(hostname -s). Check: journalctl -u ${unit}\"}" \
  "$SLACK_ALERT_WEBHOOK_URL" >/dev/null
