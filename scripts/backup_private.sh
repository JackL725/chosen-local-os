#!/bin/bash
# Encrypted backup of /home/clworker/cl-private (recordings, transcripts, private evals).
# Runs as clworker from the cl-restic systemd timer. restic encrypts on this machine
# before upload. Settings: /home/clworker/.config/chosen-local/restic.env (600).
set -euo pipefail
set -a; . /home/clworker/.config/chosen-local/restic.env; set +a
: "${PRIVATE_DATA_DIR:?}"
restic backup --quiet --tag cl-private --host chosen-local-workstation "$PRIVATE_DATA_DIR"
restic forget --quiet --host chosen-local-workstation --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
restic check --quiet
