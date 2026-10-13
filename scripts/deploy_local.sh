#!/bin/bash
# Install the current commit as the production copy on the workstation (WSL).
#
#   cd ~/code/chosen-local-os && sudo scripts/deploy_local.sh
#
# What it does, in order:
#   1. refuses to run with uncommitted changes (production is always a commit);
#   2. creates the `clworker` system user and its private folders if missing;
#   3. exports HEAD into /opt/chosen-local-releases/<sha>, builds its virtualenv with
#      the system Python 3.12, and points the /opt/chosen-local symlink at it;
#   4. installs the systemd units and enables them at boot;
#   5. creates worker.env from the example (names only) if it does not exist yet, and
#      stops there so you can fill it in;
#   6. restarts the services and shows their state.
# It never reads worker.env's values and never touches /home/clworker/cl-private.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo scripts/deploy_local.sh" >&2
  exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUN_AS="${SUDO_USER:-root}"
git_() { sudo -u "$RUN_AS" git -C "$REPO" "$@"; }

if [ -n "$(git_ status --porcelain --untracked-files=no)" ]; then
  echo "Uncommitted changes in $REPO. Commit first; production runs a commit." >&2
  exit 1
fi
SHA="$(git_ rev-parse HEAD)"
SHORT="${SHA:0:12}"

UV="$(command -v uv || true)"
[ -n "$UV" ] || UV="/home/$RUN_AS/.local/bin/uv"
[ -x "$UV" ] || { echo "uv not found (looked in PATH and $UV)" >&2; exit 1; }
PY=/usr/bin/python3.12
[ -x "$PY" ] || { echo "$PY not found: sudo apt install python3.12 python3.12-venv" >&2; exit 1; }

# --- 2. clworker and its folders ------------------------------------------------
if ! id clworker >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/clworker --shell /usr/sbin/nologin clworker
  echo "Created system user clworker."
fi
install -d -o clworker -g clworker -m 700 /home/clworker
install -d -o clworker -g clworker -m 700 /home/clworker/cl-private /home/clworker/cl-private/evals
install -d -o clworker -g clworker -m 700 /home/clworker/.config /home/clworker/.config/chosen-local

# --- 3. release ------------------------------------------------------------------
RELEASES=/opt/chosen-local-releases
DEST="$RELEASES/$SHORT"
install -d -m 755 "$RELEASES"
if [ ! -d "$DEST" ]; then
  TMP="$(mktemp -d "$RELEASES/.incoming.XXXX")"
  git_ archive --format=tar HEAD | tar -x -C "$TMP"
  echo "GIT_SHA=$SHA" > "$TMP/REVISION.env"
  (cd "$TMP" && UV_CACHE_DIR=/var/cache/chosen-local-uv UV_PYTHON_DOWNLOADS=never \
     "$UV" sync --no-dev --python "$PY" --quiet)
  "$TMP/.venv/bin/python" -m compileall -q "$TMP/worker" "$TMP/review" "$TMP/evals" "$TMP/systems" >/dev/null
  chown -R root:root "$TMP"
  chmod -R go-w "$TMP"
  chmod -R a+rX "$TMP"
  mv "$TMP" "$DEST"
fi
ln -sfn "$DEST" /opt/chosen-local.new && mv -T /opt/chosen-local.new /opt/chosen-local
# Keep the five newest releases.
   { ls -1dt "$RELEASES"/*/ 2>/dev/null | grep -v "/$SHORT/$" || true; } | tail -n +5 | xargs -r rm -rf

# --- 4. units --------------------------------------------------------------------
install -m 644 "$DEST"/deploy/systemd/*.service "$DEST"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable llama-server.service cl-worker.service cl-review.service cl-restic.timer >/dev/null

# --- 5. worker.env ----------------------------------------------------------------
ENVFILE=/home/clworker/.config/chosen-local/worker.env
if [ ! -f "$ENVFILE" ]; then
  install -o clworker -g clworker -m 600 "$DEST/deploy/worker.env.example" "$ENVFILE"
  echo
  echo "Created $ENVFILE from the example (names only)."
  echo "Fill in the values (deploy/README.md, workstation step W5), then run this script again."
  exit 0
fi
chown clworker:clworker "$ENVFILE"; chmod 600 "$ENVFILE"
CONSOLE_ENV=/home/clworker/.config/chosen-local/console.env
if [ ! -f "$CONSOLE_ENV" ]; then
  install -o clworker -g clworker -m 600 "$DEST/deploy/console.env.example" "$CONSOLE_ENV"
  echo
  echo "Created $CONSOLE_ENV from the example (names only)."
  echo "Fill in the values (deploy/PHASE1.md, workstation step W2), then run this script again."
  exit 0
fi
chown clworker:clworker "$CONSOLE_ENV"; chmod 600 "$CONSOLE_ENV"

# --- 6. restart -------------------------------------------------------------------
systemctl restart llama-server.service cl-review.service
systemctl restart cl-worker.service
systemctl start cl-restic.timer
sleep 3
echo
echo "Deployed $SHORT to /opt/chosen-local"
for unit in llama-server cl-worker cl-review cl-restic.timer; do
  printf '  %-16s %s (user: %s)\n' "$unit" "$(systemctl is-active "$unit" || true)" \
    "$(systemctl show -p User --value "$unit" 2>/dev/null || echo -)"
done
