# Deploying Phase 1

Numbered steps Jack runs, after Phase 0 is deployed (`deploy/README.md`). Claude Code never
runs these (CLAUDE.md hard rule 10). The design is `docs/phase-1-design.md`.

Order: **L** (lock and test locally) → **V** (VPS) → **W** (workstation) → **C** (console
set-up) → **A** (acceptance).

Shorthand on the VPS, as in the runbook:

```bash
DC="sudo docker compose -f /opt/chosen-local-vps/deploy/compose.prod.yml --env-file /etc/chosen-local/vps.env"
```

---

## L. Lock and test on your checkout (WSL, as yourself)

1. `git fetch && git checkout claude/phase-1` (or `main` once the PR is merged).
2. `uv lock`: Phase 1 adds starlette, jinja2, python-multipart and uvicorn, and the lock
   file could not be regenerated where the code was written. Commit `uv.lock`.
3. `docker compose -f deploy/compose.dev.yml up -d`, `set -a; . ./.env; set +a`, `dbmate up`,
   then `uv run pytest -q`. Expect everything to pass with one skip (the local-model test).
   Add `CONSOLE_ALLOW_LOCAL_SIGNIN=true` to your `.env` if you want to try the dev console.

## V. VPS

1. **Back up first:** `sudo systemctl start cl-pg-backup` and check it finished
   (`systemctl status cl-pg-backup`).
2. **Code:** `cd /opt/chosen-local-vps && sudo git pull` (same branch as step L1).
3. **Migrate:** `sudo /opt/chosen-local-vps/deploy/migrate.sh`. Migration 2 adds the systems,
   settings, markets, vetting-standard and console tables and the login guard. It refuses to
   run if `coaches`, `leads` or `ads` already hold rows (they are empty after Phase 0).
4. **Seed the business profile** (founders, Denver, the two launch need-states, the draft
   vetting standard, the business name). It only adds what is missing, so it is safe to rerun:
   ```bash
   cd /opt/chosen-local-vps
   python3 -m worker.bootstrap deploy/business/chosen-local.toml > /tmp/bootstrap.sql
   less /tmp/bootstrap.sql          # read it: inserts and one founder check, nothing else
   $DC exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d ledger < /tmp/bootstrap.sql
   rm /tmp/bootstrap.sql
   ```
5. **Console login:** `sudo /opt/chosen-local-vps/deploy/postgres/create_console_login.sh`.
   Copy the password into your password manager. (Do not rerun `create_logins.sh`; it rotates
   every login.)
6. **n8n:** the stale-job alert now reads its threshold from the Ledger and calls
   `api.queue_health(15)`. In the n8n editor, re-import `n8n/workflows/stale-job-alert.json`
   over the old one, select its two credentials, set `error-handler` as its error workflow,
   and activate it. Until you do, the old workflow fails (its function signature is gone)
   and `error-handler` reports it to Slack.

## W. Workstation (WSL)

1. **Deploy:** from your checkout, `sudo scripts/deploy_local.sh`. On its first Phase 1 run it
   creates the `clconsole` user and the `clprivate` group, gives the group access to
   `/home/clworker/cl-private`, creates `/etc/chosen-local/console.env` (root only) from the
   example, and stops.
2. **console.env:** `sudo nano /etc/chosen-local/console.env`
   ```
   CONSOLE_DATABASE_URL=postgres://cl_console:<password from V5>@<VPS Tailscale IP>:5432/ledger?sslmode=disable
   CONSOLE_ALLOW_LOCAL_SIGNIN=true
   CONSOLE_MAX_UPLOAD_MB=500
   ```
   Only the console reads this file: systemd reads it as root and starts the console as
   `clconsole`. **Do not** put `CONSOLE_DATABASE_URL` in `worker.env`. That login may act as a
   founder, and the worker must never hold it. In `worker.env`, add `WORKER_CONCURRENCY=4` (equal
   to `LLAMA_PARALLEL`). You can delete `REVIEW_CONSOLE_SECRET`; sessions now live in the
   Ledger. Then run `sudo scripts/deploy_local.sh` again.
3. `sudo systemctl restart cl-worker cl-review` and check `systemctl is-active cl-worker cl-review`
   and `curl -s 127.0.0.1:8090/healthz`.

## C. Console set-up

1. **Your login** (you may also sign in on the workstation itself, decision 11):
   ```bash
   sudo /opt/chosen-local/scripts/console_admin.sh create-user jack \
     --tailscale-login <your Tailscale login> --allow-local
   ```
   Type a password of 12+ characters at the prompt.
2. **Sam's login** (Tailscale only), once his Tailscale login is in the policy (decisions
   item 23): `sudo /opt/chosen-local/scripts/console_admin.sh create-user sam --tailscale-login <Sam's login>`. Give Sam
   the password in person or through your password manager; he changes it under Account.
3. Open the console: from your phone or laptop `https://<workstation tailnet name>:8443`; on the
   workstation `http://localhost:8090`.
4. **Systems:** turn on **Health check** and **Coaches**. New systems always arrive off.
5. **Settings:** check *Stale-job alert (hours)* is 4 (decisions item 24).
6. **Need-states:** write what *Fitness* and *Life direction* cover and do not cover (B2).
7. **Coaches → Vetting standards:** read draft v1 (the business plan's six items). Change it if
   you and Sam want, then **Activate**. That is decisions item 2; until then no coach can be
   marked active.

## A. Phase 1 acceptance tests

| # | Test | How | Pass |
| --- | --- | --- | --- |
| A1 | CI and the local suite | PR checks green; step L3 | all green, one skip |
| A2 | Ten parallel jobs | With llama-server up, in your checkout: `set -a; . ./.env; set +a`; turn on Health check in the dev Ledger (dev console, or `psql "$DATABASE_URL" -c "SELECT set_config('app.actor','jack',false); UPDATE systems SET enabled = true WHERE key = 'healthcheck'"` after starting the dev worker once); run `WORKER_CONCURRENCY=4 uv run python -m worker` in one terminal and `uv run python scripts/load_check.py --jobs 10` in another | `result=PASS`, `succeeded=10`; record `wall_s` and `job_ms_median` in `docs/integrations.md` |
| A3 | A coach moves prospect → vetting → active only by founders | In the production console, add "Test Coach", start vetting, add a note to each item, verify them, try **Mark active** (refused while the standard is a draft), activate the standard, mark the coach active; open **history** | every row in history shows you or Sam; nothing shows `worker` or `n8n` |
| A4 | A missing item blocks activation | Pause Test Coach, add a new draft standard with one extra item, activate it, move Test Coach to it, try **Mark active** | refused, naming the missing item. Then **Remove** Test Coach |
| A5 | The worker cannot act as a founder | On the workstation: (1) `psql "postgres://cl_worker:<pw>@<VPS IP>:5432/ledger" -c "SELECT set_config('app.actor','sam',false); INSERT INTO coaches (display_name) VALUES ('x')"`; (2) `sudo -u clworker cat /etc/chosen-local/console.env`; (3) `sudo -u clworker cat /proc/$(systemctl show -p MainPID --value cl-review)/environ` | (1) `this database login may not act as a founder actor`; (2) and (3) `Permission denied` |
| A6 | Console unreachable off the tailnet, and from tagged devices without a password | Phone with Tailscale off: the :8443 address does not load. Workstation browser at `http://localhost:8090` with no password: refused | both refused |
| A7 | Evidence is backed up | Upload a small PDF to Test Coach before removing it; `sudo systemctl start cl-restic`; `sudo -u clworker bash -c 'set -a; . ~/.config/chosen-local/restic.env; restic ls latest' \| grep coaches/` | the file appears in the snapshot |
| A9 | The worker cannot change evidence | After A7's upload, before removing Test Coach: `sudo -u clworker sh -c 'echo x > /home/clworker/cl-private/coaches/probe'` and `sudo -u clworker mv /home/clworker/cl-private/coaches /home/clworker/cl-private/moved`; then open the PDF in the console | both refused (`Permission denied` / `Operation not permitted`); the PDF still opens. A file that was changed on disk shows "changed on the workstation" instead, and Slack gets `evidence_changed_on_disk` |
| A8 | Services survive a restart | `wsl --shutdown` from Windows, wait for the Task Scheduler task, then `systemctl is-active llama-server cl-worker cl-review` | all three `active` |

When all pass, tell Claude, and update `docs/decisions.md` (section 12 of the design lists the edits).
