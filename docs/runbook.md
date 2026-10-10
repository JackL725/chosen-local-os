# Runbook

Claude keeps this current as components are built. For each component: how to start, stop,
restart, check health, and recover. Commands that touch production are run by Jack.

Shorthand used below:

```bash
# VPS
DC="sudo docker compose -f /opt/chosen-local-vps/deploy/compose.prod.yml --env-file /etc/chosen-local/vps.env"
```

## Where things run

| Component | Where | Runs as | Reached at |
| --- | --- | --- | --- |
| Postgres 16 (Ledger + n8n DBs) | VPS, Docker | container | 127.0.0.1:5432; tailnet `<vps>:5432` via `tailscale serve` |
| n8n | VPS, Docker | container | editor `https://<vps>:8443` (tailnet); webhooks `https://hooks.<domain>/webhook/...` once Caddy is up |
| Caddy | VPS, Docker, profile `public` | container | public 80/443, webhook paths only |
| Ledger backup | VPS, `cl-pg-backup.timer` 03:30 Denver | root | B2 bucket, prefix `vps` |
| llama-server | workstation WSL, `llama-server.service` | clworker | 127.0.0.1:8080 |
| worker | workstation WSL, `cl-worker.service` | clworker | outbound to Ledger over Tailscale |
| review console | workstation WSL, `cl-review.service` | clworker | 127.0.0.1:8090; tailnet `https://<workstation>:8443` via Windows `tailscale serve` |
| private-data backup | workstation WSL, `cl-restic.timer` 02:30 Denver | clworker | B2 bucket, prefix `workstation` |
| unit failure alerts | workstation, `cl-unit-failed@.service` | clworker | Slack `#cl-alerts` |

## Health at a glance

```bash
# Workstation (WSL)
systemctl is-active llama-server cl-worker cl-review
curl -s 127.0.0.1:8080/health          # {"status":"ok"} once the model is loaded (503 while loading)
curl -s 127.0.0.1:8090/healthz
journalctl -u cl-worker -n 50 --no-pager   # JSON lines, IDs only

# VPS
$DC ps
tailscale serve status
systemctl list-timers cl-pg-backup.timer
```

Ledger queue (psql as `cl_reporting`, or `cl_migrate` on the VPS):

```sql
SELECT * FROM api.queue_health(4);                 -- as cl_n8n, or any owner
SELECT status, count(*) FROM jobs GROUP BY 1;
SELECT worker_id, last_seen_at, current_job_id FROM worker_heartbeats;
SELECT id, job_type, attempts, error_class, finished_at FROM jobs WHERE status = 'failed' ORDER BY finished_at DESC LIMIT 20;
```

## Worker (`cl-worker`)

- **Start / stop / restart:** `sudo systemctl start|stop|restart cl-worker`. Stop sends SIGTERM:
  the current job finishes, then the process exits (up to 120 s).
- **Deploy a new commit:** commit, then `sudo scripts/deploy_local.sh` from the checkout. It
  installs `/opt/chosen-local-releases/<sha>`, repoints `/opt/chosen-local`, and restarts.
- **Roll back:** `sudo ln -sfn /opt/chosen-local-releases/<older sha> /opt/chosen-local && sudo systemctl restart cl-worker cl-review`.
- **A job is stuck `running`:** its lease expires (5 min default) and the next reaper pass
  (every minute) returns it to `pending`, or marks it `failed` if it has used all attempts.
  Nothing to do by hand.
- **A job `failed`:** Slack has the job ID and error class. After fixing the cause, retry it:
  ```sql
  SELECT set_config('app.actor', 'jack', false);
  UPDATE jobs SET status = 'pending', attempts = 0, run_after = now(), finished_at = NULL WHERE id = '<job id>';
  ```
- **Worker cannot reach the Ledger:** check Tailscale on Windows (tray icon connected),
  `tailscale serve status` on the VPS, then `psql` from WSL as in deploy/README.md W6.

## Model server (`llama-server`)

- **Restart:** `sudo systemctl restart llama-server`; model load takes a minute; `/health` returns 503 until then.
- **Change model or context:** edit `LLAMA_MODEL_PATH` / `LLAMA_CTX_SIZE` / `LLAMA_PARALLEL` / `LOCAL_LLM_MODEL`
  in `worker.env`, restart `llama-server` and `cl-worker`. A model changes only after it scores
  better on the eval set (`uv run python -m evals.run <system>`).
- **Logs:** `journalctl -u llama-server` (warnings and errors only; prompts are never logged).

## Review console (`cl-review`)

- Phase 0 placeholder: `/healthz` only. Restart with `sudo systemctl restart cl-review`.
- **Not reachable from a phone:** Windows tray → Tailscale connected and "Run unattended" on;
  in admin PowerShell `tailscale serve status` shows `:8443 → http://127.0.0.1:8090`; in WSL
  `curl 127.0.0.1:8090/healthz` works.

## WSL itself

- **After a Windows reboot, nothing runs:** check Task Scheduler → "Start WSL (Chosen Local)"
  last run result; `%UserProfile%\.wslconfig` has `instanceIdleTimeout=-1`; `/etc/wsl.conf`
  has `systemd=true` (deploy/windows/start-wsl.md).

## n8n

- **Restart:** `$DC restart n8n`. **Logs:** `$DC logs --tail 100 n8n`.
- **Upgrade:** change `N8N_VERSION` in `/etc/chosen-local/vps.env`, read the release notes,
  `$DC pull n8n && $DC up -d n8n`. Back up first (`sudo systemctl start cl-pg-backup`).
- Execution data: successful runs are not saved; errors are kept 7 days (`EXECUTIONS_DATA_MAX_AGE=168`).

## Postgres

- **Restart:** `$DC restart postgres` (n8n reconnects; the worker retries with backoff).
- **Migrate:** `sudo /opt/chosen-local-vps/deploy/migrate.sh` after `git pull`.
- **Reboot behaviour:** containers `restart: unless-stopped`; `tailscale serve` config
  persists. Reboot test result: _not yet run_ (deploy/README.md V9 — record the date here).

## Backups and restore tests

Backups are restic repositories in the B2 bucket, encrypted with passwords kept only in the
password manager and on each machine (`/etc/chosen-local/restic.password`,
`/home/clworker/.config/chosen-local/restic.password`).

**Ledger restore test (VPS, do once after setup and then quarterly):**

```bash
sudo bash -c 'set -a; . /etc/chosen-local/backup.env; restic snapshots --tag ledger --latest 1'
sudo bash -c 'set -a; . /etc/chosen-local/backup.env; restic dump latest ledger.dump --tag ledger > /root/ledger-restore.dump'
$DC exec -T postgres psql -U postgres -c "CREATE DATABASE ledger_restore_test"
$DC exec -T postgres pg_restore -U postgres -d ledger_restore_test --no-owner < /root/ledger-restore.dump
$DC exec -T postgres psql -U postgres -d ledger_restore_test -c "select count(*) from events"
$DC exec -T postgres psql -U postgres -c "DROP DATABASE ledger_restore_test"
sudo rm /root/ledger-restore.dump
```

Pass: the count matches the live Ledger's (or is a little lower: anything since 03:30).
Last run: _not yet run_.

**Private-data restore test (workstation, as clworker):**

```bash
sudo -u clworker bash -c 'set -a; . ~/.config/chosen-local/restic.env; restic snapshots --tag cl-private --latest 1'
sudo -u clworker bash -c 'set -a; . ~/.config/chosen-local/restic.env; restic restore latest --tag cl-private --target /home/clworker/restore-test'
sudo -u clworker bash -c 'diff -rq /home/clworker/cl-private /home/clworker/restore-test/home/clworker/cl-private && echo RESTORE_OK'
sudo rm -rf /home/clworker/restore-test
```

Last run: _not yet run_.

**Full Ledger loss:** stand up a new VPS with deploy/README.md V1–V5 (do not run `migrate.sh`),
restore the latest dump into the new `ledger` database with `pg_restore`, run
`create_logins.sh`, update the workstation's `DATABASE_URL`, and re-import n8n workflows.

## Manual fallback

If the systems are down, Sam can run the whole journey inside Close: leads arrive by email from the
form, Sam texts the booking link from a saved Close template, takes the call, picks the coach from
the roster sheet, and introduces by email. Log what happened in the Ledger once systems return.
