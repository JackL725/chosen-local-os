---
paths: n8n/**, db/**, deploy/**
---

# n8n, database and deployment rules

- n8n workflows: receive, validate, write to the Ledger, enqueue, route. No business logic in Code
  nodes beyond reshaping data. Each workflow has an error branch that writes a failed event and
  alerts Slack with IDs only.
- Every outbound message goes through the `send-gate` sub-workflow. No other workflow calls Close,
  email or SMS send endpoints directly. A check script in `scripts/` scans the exported workflow
  JSON and fails if one does.
- Export workflows to `n8n/workflows/<name>.json` after every change. Strip credentials and
  instance-specific IDs. Document the credentials each workflow needs in `n8n/README.md`.
- n8n runs with execution data for successful runs not saved and pruning on. Confirm the current
  environment variable names in n8n's docs.
- Webhooks that accept outside traffic (landing form, Cal.com, Close, Slack interactions) verify a
  signature or shared secret and are rate limited, either in n8n or with a Caddy build that
  includes a rate-limit plugin (core Caddy has none).
- Migrations are forward-only SQL. Every table gets `created_at`, `updated_at`. Money is stored as
  integer cents. Timestamps are `timestamptz` in UTC.
- Postgres never listens on a public interface. The method that exposes it on Tailscale must come
  back by itself after a reboot of either machine; document how you tested that.
