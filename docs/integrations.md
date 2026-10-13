# Integrations

Claude records here what it confirmed in each service's current API docs: endpoints, webhook
events and payload fields, auth method, rate limits, with links and the date checked.

| Service | What we use | Confirmed facts | Doc link | Checked |
| --- | --- | --- | --- | --- |
| Close | leads, calls, recordings, texts, webhooks | (Phase 2) | https://developer.close.com/ | |
| Cal.com | bookings, webhooks | (Phase 2a) | | |
| Slack | incoming webhook (alerts); bot token `chat:write` (n8n Slack node) | `POST <webhook URL>` with JSON `{"text": ...}`. Success is HTTP 200; errors are 400 `invalid_payload`, 403 `action_prohibited`, 404 `channel_not_found`, 410 `channel_is_archived`, 500 `rollup_error`. The worker checks the status code, not the body. | https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks ; https://docs.slack.dev/changelog/2016-05-17-changes-to-errors-for-incoming-webhooks | 2026-10-10 |
| QuickBooks | invoice drafts | (Phase 4) | | |
| Meta | Conversions API, ad insights | (Phase 2f/3) | | |
| TikTok | ad insights | (Phase 3) | | |

## Infrastructure facts confirmed for Phase 0

| Component | Confirmed | Source | Checked |
| --- | --- | --- | --- |
| llama.cpp `llama-server` | Flags: `--host` (default 127.0.0.1), `--port` (8080), `-m/--model`, `-c/--ctx-size`, `-np/--parallel`, `-ngl/--n-gpu-layers` (`auto`/N/`all`), `--alias`. `/slots` is **on** by default → `--no-slots`. `/metrics` is **off** by default (`--metrics` enables). `--no-webui`. `--log-verbosity N` (0–5, default 3). `--log-prompts-dir` is off by default. `--jinja` is on by default. `--chat-template-kwargs '{"enable_thinking":false}'` at server level, and per-request `chat_template_kwargs` on `/v1/chat/completions`. `/health` returns 503 while loading, 200 `{"status":"ok"}` when ready. | https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md | 2026-10-10 |
| llama.cpp structured output | For `response_format.type == "json_schema"` the server reads the OpenAI nested form `response_format.json_schema.schema` (server code), not a top-level `schema`. `worker/local_llm.py` sends the nested form. | https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-common.cpp | 2026-10-10 |
| n8n execution data | `EXECUTIONS_DATA_SAVE_ON_SUCCESS=none`, `EXECUTIONS_DATA_SAVE_ON_ERROR=all`, `EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS`, `EXECUTIONS_DATA_SAVE_ON_PROGRESS`, `EXECUTIONS_DATA_PRUNE=true` (default true), `EXECUTIONS_DATA_MAX_AGE` (hours, default 336), `EXECUTIONS_DATA_PRUNE_MAX_COUNT` (default 10000). | https://docs.n8n.io/hosting/configuration/environment-variables/executions/ | 2026-10-10 |
| n8n database | `DB_TYPE=postgresdb`, `DB_POSTGRESDB_HOST/PORT/DATABASE/USER/PASSWORD/SCHEMA`. | https://docs.n8n.io/hosting/configuration/environment-variables/database/ | 2026-10-10 |
| n8n behind a proxy | `N8N_HOST`, `N8N_PROTOCOL`, `N8N_PORT`, `N8N_PROXY_HOPS=1`, `N8N_EDITOR_BASE_URL`, `N8N_ENCRYPTION_KEY`. `WEBHOOK_URL` is deprecated in favour of `N8N_WEBHOOK_URL` (docs give 2.30 and 2.35 as the version; compose sets both). `N8N_RUNNERS_ENABLED` is no longer needed on 2.x. `N8N_DIAGNOSTICS_ENABLED=false`, `N8N_VERSION_NOTIFICATIONS_ENABLED=false`. Image `n8nio/n8n`, data at `/home/node/.n8n`. Paths: `/webhook/`, `/webhook-test/`, `/webhook-waiting/`, `/form/`, `/form-test/`, `/form-waiting/`, `/rest`, `/healthz`. | https://docs.n8n.io/hosting/configuration/environment-variables/deployment/ ; https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/endpoints.md ; https://github.com/n8n-io/n8n/blob/master/packages/@n8n/config/src/configs/endpoints.config.ts | 2026-10-10 |
| n8n task runners | Default `N8N_RUNNERS_MODE=internal` is deprecated and "not recommended for production"; `external` uses the `n8nio/runners` image. Not set up in Phase 0 because no workflow uses a Code node; revisit if one does. | https://docs.n8n.io/deploy/host-n8n/configure-n8n/set-up-task-runners.md | 2026-10-10 |
| Caddy | `handle` blocks are mutually exclusive; an unmatched `handle` is the fallback. Automatic HTTPS when the site address is a domain. Rate limiting needs the third-party `github.com/mholt/caddy-ratelimit` plugin, built with `xcaddy` from `caddy:2.11.7-builder`. | https://caddyserver.com/docs/caddyfile/directives/handle ; https://caddyserver.com/docs/automatic-https ; https://github.com/mholt/caddy-ratelimit ; https://hub.docker.com/_/caddy | 2026-10-10 |
| Tailscale serve | `tailscale serve --bg --tcp=<port> tcp://localhost:<port>` and `--https=<port>`; with `--bg` the config persists across reboots and `tailscale down/up`. This avoids binding Docker to the Tailscale IP, which fails at boot ("cannot assign requested address") because Docker can start before tailscaled. | https://tailscale.com/kb/1242/tailscale-serve ; https://forum.tailscale.com/t/docker-containers-with-ports-bound-to-tailscale-interface-fail-to-start-on-reboot/4616 | 2026-10-10 |
| Tailscale policy | Tags need `tagOwners`; `grants` are recommended for new policies (`src`, `dst`, `ip: ["tcp:5432"]`); a `tests` block validates on save. | https://tailscale.com/kb/1337/policy-syntax ; https://tailscale.com/kb/1324/grants | 2026-10-10 |
| Tailscale + WSL2 | Do not run Tailscale on Windows and inside WSL2 at the same time; Tailscale recommends the Windows host. | https://tailscale.com/kb/1295/install-windows-wsl2 | 2026-10-10 |
| WSL | `/etc/wsl.conf` `[boot] systemd=true`. `.wslconfig` `[general] instanceIdleTimeout=-1` disables auto shutdown. No official "start at boot" switch; we use a Task Scheduler task (not from an official source; proven by acceptance test A7). | https://learn.microsoft.com/en-us/windows/wsl/wsl-config | 2026-10-10 |
| dbmate | v2.36.0 (2026-09-19). Binary: `https://github.com/amacneil/dbmate/releases/download/v2.36.0/dbmate-linux-amd64`. Reads `DATABASE_URL`; files need `-- migrate:up` and `-- migrate:down`; `--no-dump-schema`; `--wait`; default dir `./db/migrations`. | https://github.com/amacneil/dbmate | 2026-10-10 |
| restic + B2 | Backblaze recommends its S3-compatible API for restic: `RESTIC_REPOSITORY=s3:https://<endpoint>/<bucket>/<path>` with `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`; `RESTIC_PASSWORD_FILE`. `forget --keep-daily/--keep-weekly/--keep-monthly --prune`; `restore latest --target`. | https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html ; https://restic.readthedocs.io/en/stable/060_forget.html ; https://restic.readthedocs.io/en/stable/050_restore.html | 2026-10-10 |

## Confirmed for Phase 1

| Component | Confirmed | Source | Checked |
| --- | --- | --- | --- |
| Tailscale Serve identity headers | Serve adds `Tailscale-User-Login`, `Tailscale-User-Name`, `Tailscale-User-Profile-Pic` to proxied requests and strips copies sent by the client. Not populated for traffic from tagged devices or through Funnel. The backend should listen on localhost only, since anything that reaches it directly can set the headers. | https://tailscale.com/kb/1312/serve | 2026-10-13 |
| Starlette 1.6.0 | `Jinja2Templates(env=...)`; `TemplateResponse(request, name, context, status_code=...)`; `Starlette(..., max_body_size=...)` adds a request body limit; `request.form(max_files, max_fields, max_part_size)` (part size applies to non-file fields; files spool to disk past 1 MB); `FileResponse(..., content_disposition_type=...)`. Read from the installed package source, not from memory. | https://github.com/encode/starlette (v1.6.0 source) | 2026-10-13 |
| Python `hashlib.scrypt` | Standard library; the console passes `maxmem` explicitly so n=2^17, r=8 (about 128 MB) is allowed. Parameters n=2^17, r=8, p=1 are, to my knowledge, OWASP's minimum for scrypt; the cheat sheet page could not be opened in that session, so **re-check** https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html. Hashes carry their own parameters, so raising them later needs no migration. | https://docs.python.org/3/library/hashlib.html | 2026-10-13 (partly unverified) |
| Postgres `pg_has_role` | Reports a superuser as having every role, so the login guard checks `rolsuper` first; it uses `USAGE` (inherited privileges) rather than `MEMBER`. Verified on Postgres 16.15. | local test, `tests/db/test_phase1_schema.py` | 2026-10-13 |

Relative redirects only: the console never builds absolute URLs, because Tailscale Serve may
rewrite the `Host` header. Not verified against a real Serve hop; acceptance step A3 exercises it.

## Model choices

| Job | Chosen | Score on eval | Date |
| --- | --- | --- | --- |
| Language model | Qwen3.6 27B Q4_K_M (baseline) | healthcheck eval: not yet run on the workstation | |
| Load check (Phase 1) | 10 healthcheck jobs, `WORKER_CONCURRENCY=4`, `LLAMA_PARALLEL=4` | not yet run: record `wall_s` and `job_ms_median` from `scripts/load_check.py` | |
| Transcription | | | |
| Diarization | pyannote speaker-diarization-community-1 | | |
