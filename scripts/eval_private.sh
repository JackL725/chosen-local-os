#!/bin/bash
# Evals on REAL data. Jack runs it as clworker from the deployed copy:
#
#   sudo -u clworker /opt/chosen-local/scripts/eval_private.sh <system>
#
# Reads cases from $PRIVATE_DATA_DIR/evals/<system>/cases.jsonl and prints aggregate
# metrics only. Two layers keep examples off the screen: evals.run --aggregate-only
# prints nothing but `name=value` metrics, and this script drops every other line and
# all of stderr.
set -uo pipefail

system="${1:?usage: eval_private.sh <system>}"
[[ "$system" =~ ^[a-z][a-z0-9_]*$ ]] || { echo "eval_error=bad_system_name"; exit 2; }
[ "$(id -un)" = "clworker" ] || { echo "eval_error=run_as_clworker"; exit 2; }

set -a
. /home/clworker/.config/chosen-local/worker.env
set +a
cd /opt/chosen-local || exit 2

/opt/chosen-local/.venv/bin/python -m evals.run "$system" \
    --data-dir "$PRIVATE_DATA_DIR/evals" --aggregate-only 2>/dev/null \
  | grep -E '^[a-z][a-z0-9_]*=[A-Za-z0-9_.-]{1,40}$'
status=${PIPESTATUS[0]}
[ "$status" -eq 0 ] || echo "eval_exit=$status"
exit "$status"
