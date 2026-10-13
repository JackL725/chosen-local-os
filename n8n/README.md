# n8n workflows

One file per workflow, exported with credentials and instance IDs stripped. After importing
a workflow, open each node that needs a credential and select it, then activate.
`scripts/check_n8n_workflows.py` (CI and pre-commit) fails if a workflow other than
`send-gate` can send to a person, or if an export still carries credentials or instance IDs.

## Credentials (create once in n8n: Settings → Credentials)

| Credential name | Type | Used by | What it holds |
| --- | --- | --- | --- |
| `Ledger (n8n role)` | Postgres | stale-job-alert, error-handler | host `postgres`, port 5432, database `ledger`, user `cl_n8n`, its password. This login can only run `api.*` functions. |
| `Slack bot (Chosen Local)` | Slack API (access token) | stale-job-alert, error-handler | the bot token for the Chosen Local Slack app; the bot must be invited to `#cl-alerts` |

## Workflows

| Workflow | Trigger | What it does | Notes |
| --- | --- | --- | --- |
| `error-handler` | any workflow fails | writes a `workflow.failed` event (IDs only) and posts the workflow name, execution ID and node to Slack | Set it as the **Error workflow** in every other workflow's settings. |
| `stale-job-alert` | every 15 min | reads `api.queue_health(15)`; posts to Slack when the oldest pending job is older than the threshold (setting `core.stale_job_alert_hours`, 4 h, decisions item 24), then every 4 h while it stays stale. Jobs of systems that are turned off are counted as paused and never alert. | Re-import after migration 2: the old `queue_health(4, 15, 240)` call no longer exists. |

## After every change

Export the workflow (⋯ → Download), remove the `id`, `versionId`, `meta.instanceId` and every
node's `credentials` block, save it here as `<name>.json`, and run
`uv run python scripts/check_n8n_workflows.py`.
