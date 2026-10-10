# Deploying Phase 0

Numbered steps Jack runs. Claude Code never runs these (CLAUDE.md hard rule 10).
Nothing here asks you to paste a secret anywhere but your password manager and the env
files named. Generate secrets with `openssl rand -base64 32` (or `-hex 32` where noted).

Order: **S** (Slack) → **T** (Tailscale policy) → **V** (VPS) → **W** (workstation) → **A** (acceptance).

---

## S. Slack (10 min)

1. Create two channels: `#cl-alerts` (production) and `#cl-dev` (development).
2. At api.slack.com/apps, create an app "Chosen Local" in your workspace.
3. **Incoming Webhooks** → on → add one for `#cl-alerts` and one for `#cl-dev`.
   Put the `#cl-alerts` URL in your password manager; it goes into `worker.env` and
   `backup.env`. The `#cl-dev` URL goes into your local `.env`.
4. **OAuth & Permissions** → Bot Token Scopes: `chat:write`. Install to the workspace,
   copy the Bot User OAuth Token to your password manager (n8n uses it), and run
   `/invite @Chosen Local` in `#cl-alerts`.

## T. Tailscale policy (5 min)

1. Open `deploy/tailscale-policy.hujson`. Replace `JACK_TAILSCALE_LOGIN` and
   `SAM_TAILSCALE_LOGIN` with the logins you and Sam use for Tailscale (e.g. your emails).
2. Paste it into https://login.tailscale.com/admin/acls (merge with what is there) and Save.
   The `tests` block must pass; if Save refuses, the error says which line.

## V. VPS (Ubuntu 24.04, about 2 vCPU / 4 GB is plenty)

1. **Base.** SSH in as your sudo user. Then:
   ```bash
   sudo apt update && sudo apt -y upgrade
   sudo apt -y install git restic python3 curl ufw
   sudo timedatectl set-timezone UTC
   ```
2. **Docker Engine + Compose plugin.** Follow docs.docker.com/engine/install/ubuntu
   ("Install using the apt repository"). Check: `docker compose version`.
3. **Tailscale, tagged as the Ledger.**
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --advertise-tags=tag:ledger
   tailscale ip -4          # note this 100.x address: the workstation's DATABASE_URL uses it
   tailscale status --self  # note the MagicDNS name (cl-vps.tailXXXX.ts.net) for VPS_TAILNET_NAME
   ```
4. **Firewall.** Nothing but SSH-over-Tailscale (and later 80/443) comes in:
   ```bash
   sudo ufw default deny incoming && sudo ufw default allow outgoing
   sudo ufw allow in on tailscale0
   sudo ufw allow OpenSSH      # remove this line's rule once you SSH over Tailscale reliably
   sudo ufw enable
   ```
   Postgres and n8n are published on 127.0.0.1 only, so Docker never exposes them.
5. **Code and secrets.**
   ```bash
   sudo git clone https://github.com/JackL725/chosen-local-os /opt/chosen-local-vps
   sudo install -d -m 700 /etc/chosen-local
   sudo cp /opt/chosen-local-vps/deploy/vps.env.example /etc/chosen-local/vps.env
   sudo chmod 600 /etc/chosen-local/vps.env
   sudo nano /etc/chosen-local/vps.env
   ```
   Fill every value (generate passwords with `openssl rand -base64 32`, the encryption key
   with `openssl rand -hex 32`). Leave `N8N_PUBLIC_HOST` empty until the domain exists.
   Copy `N8N_ENCRYPTION_KEY` into your password manager: n8n credentials cannot be
   recovered without it.
6. **Start Postgres and n8n, then migrate.**
   ```bash
   cd /opt/chosen-local-vps/deploy
   sudo docker compose -f compose.prod.yml --env-file /etc/chosen-local/vps.env up -d
   sudo docker compose -f compose.prod.yml --env-file /etc/chosen-local/vps.env ps   # both healthy/running
   sudo curl -fsSL -o /usr/local/bin/dbmate \
     https://github.com/amacneil/dbmate/releases/download/v2.36.0/dbmate-linux-amd64
   sudo chmod +x /usr/local/bin/dbmate
   sudo /opt/chosen-local-vps/deploy/migrate.sh          # "Applied: 20261010000001_ledger_init.sql"
   ```
7. **Application logins.** `sudo /opt/chosen-local-vps/deploy/postgres/create_logins.sh`
   prints three generated passwords once. Save all three in your password manager now.
8. **Expose Postgres and the n8n editor on the tailnet only.** These settings persist
   across reboots (Tailscale docs, `--bg`):
   ```bash
   sudo tailscale serve --bg --tcp=5432 tcp://127.0.0.1:5432
   sudo tailscale serve --bg --https=8443 http://127.0.0.1:5678
   tailscale serve status
   ```
9. **Reboot test** (the rules require proof Postgres comes back by itself):
   `sudo reboot`, wait two minutes, SSH back in, then:
   ```bash
   sudo docker compose -f /opt/chosen-local-vps/deploy/compose.prod.yml --env-file /etc/chosen-local/vps.env ps
   tailscale serve status
   ```
   Both containers up and both serve entries present. Record the date in `docs/runbook.md`.
10. **Backups to Backblaze B2.** In B2: create a private bucket (e.g. `chosen-local-backups`)
    and an application key restricted to it. Note the bucket's S3 endpoint. Then:
    ```bash
    sudo sh -c 'openssl rand -base64 48 > /etc/chosen-local/restic.password && chmod 600 /etc/chosen-local/restic.password'
    sudo nano /etc/chosen-local/backup.env        # names in deploy/vps.env.example; chmod 600 after
    sudo chmod 600 /etc/chosen-local/backup.env
    sudo bash -c 'set -a; . /etc/chosen-local/backup.env; restic init'
    sudo cp /opt/chosen-local-vps/deploy/backup/cl-pg-backup.{service,timer} /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now cl-pg-backup.timer
    sudo systemctl start cl-pg-backup.service && journalctl -u cl-pg-backup -n 20
    ```
    A ✅ message lands in `#cl-alerts`. Put the restic password in your password manager:
    without it the backups cannot be read. Then do the restore test in `docs/runbook.md`.
11. **n8n.** On a founder device, open `https://<VPS_TAILNET_NAME>:8443`, create the owner
    account (password manager), then follow `n8n/README.md`: create the two credentials,
    import `error-handler.json` and `stale-job-alert.json`, set `error-handler` as the error
    workflow of `stale-job-alert`, activate both.
12. **Later, when the domain exists** (decisions item 6): point an A record for
    `hooks.<domain>` at the VPS public IP, set `N8N_PUBLIC_HOST` in `vps.env`, then
    ```bash
    sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp
    cd /opt/chosen-local-vps/deploy
    sudo docker compose -f compose.prod.yml --env-file /etc/chosen-local/vps.env --profile public up -d --build
    curl -sI https://hooks.<domain>/            # 404 (only webhook paths are served)
    ```

**Updating the VPS later:** `cd /opt/chosen-local-vps && sudo git pull && sudo deploy/migrate.sh`,
then `sudo docker compose ... up -d` if compose files changed.

---

## W. Workstation (Windows 11 + WSL2 Ubuntu 24.04)

1. **Tailscale on Windows only.** Install Tailscale for Windows and sign in. In its
   settings turn on **Run unattended** (keeps it connected with nobody logged in). In the
   admin console, give this machine the tag `tag:workstation` (Machines → ⋯ → Edit ACL
   tags). Do **not** install Tailscale inside WSL: Tailscale's docs warn that running it on
   both breaks traffic. WSL reaches the VPS through Windows.
2. **WSL boot settings.** Follow `deploy/windows/start-wsl.md` sections 1 and 2 now
   (systemd on, idle shutdown off). Section 3 (start at boot) comes in step W9.
3. **GPU and llama.cpp.** With the current NVIDIA Windows driver installed (WSL uses it),
   in WSL install the CUDA toolkit for WSL-Ubuntu (developer.nvidia.com/cuda-downloads →
   Linux → x86_64 → WSL-Ubuntu), then build llama.cpp with CUDA into `/opt/llama.cpp`
   following its docs/build.md ("CUDA" section):
   ```bash
   sudo git clone https://github.com/ggml-org/llama.cpp /opt/llama.cpp
   cd /opt/llama.cpp && sudo cmake -B build -DGGML_CUDA=ON && sudo cmake --build build --config Release -j
   ls /opt/llama.cpp/build/bin/llama-server
   ```
   Download the Qwen3.6 27B Q4_K_M GGUF into `/opt/models/` and make sure the file name
   matches `LLAMA_MODEL_PATH` in step W6 (`sudo chmod -R a+rX /opt/models`).
4. **Tools for deploying.** In WSL as yourself: `sudo apt -y install python3.12 python3.12-venv restic postgresql-client`
   and install uv (docs.astral.sh/uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
   In your checkout (`~/code/chosen-local-os`): `uv lock && git add uv.lock && git commit -m "Add uv.lock" && git push`
   (the cloud session could not reach PyPI to create it), then `uv run pre-commit install`.
5. **First deploy** (creates `clworker` and a names-only `worker.env`, then stops):
   `sudo scripts/deploy_local.sh`
6. **Fill worker.env.** `sudo nano /home/clworker/.config/chosen-local/worker.env`:
   - `DATABASE_URL=postgres://cl_worker:<cl_worker password>@<VPS 100.x IP>:5432/ledger?sslmode=disable`
     (Tailscale encrypts the link. If the password has `/`, `@` or `:`, URL-encode it.)
   - `SLACK_ALERT_WEBHOOK_URL=` the `#cl-alerts` webhook
   - `REVIEW_CONSOLE_BASE_URL=https://<workstation tailnet name>:8443`
   - check `LLAMA_MODEL_PATH`, `LLAMA_CTX_SIZE`, `LLAMA_PARALLEL`
   Quick connectivity check from WSL: `psql "postgres://cl_worker@<VPS 100.x IP>:5432/ledger" -c "select 1"`
   (it prompts for the password).
7. **Second deploy** starts everything: `sudo scripts/deploy_local.sh`. Expect
   `llama-server`, `cl-worker`, `cl-review` all `active`, user `clworker`.
8. **Publish the review console to the founders' devices.** In an **administrator
   PowerShell** on Windows:
   ```powershell
   tailscale serve --bg --https=8443 http://127.0.0.1:8090
   tailscale serve status
   ```
   From your phone (on Tailscale): `https://<workstation tailnet name>:8443/healthz`.
9. **Start at boot.** Follow `deploy/windows/start-wsl.md` section 3.
10. **Private-data backups.**
    ```bash
    sudo sh -c 'openssl rand -base64 48 > /home/clworker/.config/chosen-local/restic.password'
    sudo nano /home/clworker/.config/chosen-local/restic.env    # names in deploy/worker.env.example
    sudo chown clworker:clworker /home/clworker/.config/chosen-local/restic.*
    sudo chmod 600 /home/clworker/.config/chosen-local/restic.*
    sudo -u clworker bash -c 'set -a; . ~/.config/chosen-local/restic.env; restic init'
    sudo systemctl start cl-restic.service && systemctl status cl-restic.service --no-pager
    ```
    Save the restic password in your password manager. Restore test: `docs/runbook.md`.

---

## A. Phase 0 acceptance tests

Run each, and tell Claude the result (pass/fail and any error class, never data).

| # | Test | Command | Pass when |
| --- | --- | --- | --- |
| A1 | Dev stack | `docker compose -f deploy/compose.dev.yml up -d` then `docker compose -f deploy/compose.dev.yml ps` and open http://localhost:5678 | Postgres healthy, n8n page loads |
| A2 | Migration on empty DB | `cp .env.example .env` (set the `#cl-dev` webhook), `set -a; . ./.env; set +a; dbmate up` | `Applied: 20261010000001_ledger_init.sql` |
| A3 | Test suite | `uv run pytest` (with `.env` loaded so `TEST_DATABASE_URL` is set) | all pass; 1 skipped (A4) |
| A4 | Real model round trip < 30 s | `RUN_LOCAL_MODEL_TESTS=1 uv run pytest tests/integration -m local_model` with llama-server running | 1 passed |
| A5 | Kill mid-job, 3 failures → 1 Slack alert | covered by `tests/db/test_worker_process.py` in A3 | passed in A3 |
| A6 | Import boundary, log scrubbing, CI | A3, plus the green check on GitHub Actions | green |
| A7 | Services survive a WSL restart | PowerShell: `wsl --shutdown`; wait 1 min; run the Task Scheduler task (or reboot Windows); then in WSL `systemctl is-active llama-server cl-worker cl-review` and `systemctl show -p User --value cl-worker` | `active` ×3, `clworker` |
| A8 | Private eval prints only metrics | `sudo install -d -o clworker -g clworker -m 700 /home/clworker/cl-private/evals/healthcheck && sudo install -o clworker -g clworker -m 600 evals/synthetic/healthcheck/cases.jsonl /home/clworker/cl-private/evals/healthcheck/`, then `sudo -u clworker /opt/chosen-local/scripts/eval_private.sh healthcheck` | only `name=value` lines |
| A9 | Stale-job alert | In n8n, run `stale-job-alert` once by hand with the worker stopped and a job older than 4 h (`sudo systemctl stop cl-worker`, then in psql as `cl_migrate`: `select set_config('app.actor','jack',false); insert into jobs (job_type, run_after) values ('healthcheck', now() - interval '4 hours 5 minutes');`). Afterwards: `update jobs set status='cancelled' where status='pending';` and start the worker | one message in `#cl-alerts` with a job ID |
