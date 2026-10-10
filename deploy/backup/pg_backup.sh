#!/bin/bash
# Nightly encrypted backup of the Ledger and n8n databases (VPS host, run by systemd).
# pg_dump streams straight into restic, which encrypts before anything leaves the VPS.
# Settings: /etc/chosen-local/backup.env (root:root, 600). Names in deploy/vps.env.example.
set -euo pipefail
set -a; . /etc/chosen-local/backup.env; set +a
cd /opt/chosen-local-vps/deploy
compose=(docker compose -f compose.prod.yml --env-file /etc/chosen-local/vps.env)

notify() {  # IDs and status only
  [ -n "${SLACK_ALERT_WEBHOOK_URL:-}" ] || return 0
  curl -fsS -m 10 -H 'Content-Type: application/json' \
       -d "{\"text\": \"$1\"}" "$SLACK_ALERT_WEBHOOK_URL" >/dev/null || true
}
trap 'notify ":x: VPS backup FAILED on $(hostname) (exit $?). See: journalctl -u cl-pg-backup"' ERR

for db in ledger n8n; do
  "${compose[@]}" exec -T postgres pg_dump -U postgres --format=custom "$db" \
    | restic backup --quiet --stdin --stdin-filename "$db.dump" --tag "$db" --host chosen-local-vps
done
restic forget --quiet --host chosen-local-vps --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
restic check --quiet
notify ":white_check_mark: VPS backup ok ($(date -u +%F))"
