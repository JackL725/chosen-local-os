# Chosen Local Operating System

The AI systems that run Chosen Local, built with Claude Code (Opus) against the V1.1 business plan.

- `CLAUDE.md`: standing instructions Claude Code loads every session
- `.claude/settings.json`: blocks Claude Code from reading secrets and private prospect data
- `.claude/rules/`: extra rules that load only when Claude works in matching folders
- `docs/business-plan-v1.1.md`: the source of truth
- `docs/build-plan.md`: phases, specs, acceptance tests and a kickoff prompt for each phase
- `docs/phase-0-design.md`: the Phase 0 design as built (schema, decisions, trade-offs)
- `docs/phase-1-design.md`: the Phase 1 plan and what changed as it was built
- `deploy/PHASE1.md`: Phase 1 deploy steps and acceptance tests
- `docs/decisions.md`: business inputs only the founders can supply, and their status
- `docs/inputs/`: where those inputs go
- `docs/runbook.md`: start, stop, health, recovery and restore tests for every component
- `deploy/README.md`: numbered setup steps for Slack, Tailscale, the VPS and the workstation

## Layout

| Path | What |
| --- | --- |
| `db/migrations/` | Ledger schema (dbmate, forward-only SQL); `db/schema.sql` is the dump |
| `worker/` | job worker: queue, local model client, cloud boundary, logging, Slack alerts, bootstrap |
| `systems/` | one folder per AI system, each with a data-only manifest (`docs/systems.md`) |
| `review/` | review console: sign-in, core screens; systems add theirs under `/s/<key>/` |
| `prompts/<system>/<name>.vN.md` | versioned prompts; the version in use is set in `worker/config.py` |
| `evals/` | `python -m evals.run <system>` over `evals/synthetic/` |
| `n8n/workflows/` | exported workflows (no credentials) |
| `deploy/` | compose files, Caddy, systemd units, backups, Tailscale policy |
| `scripts/` | `deploy_local.sh`, `eval_private.sh`, backup and check scripts |
| `tests/` | unit, database and process tests (fake model in CI) |

## Develop

```bash
cp .env.example .env                                  # dev values only; add the #cl-dev webhook
docker compose -f deploy/compose.dev.yml up -d        # Postgres 16 + n8n on localhost
set -a; . ./.env; set +a
dbmate up                                             # apply migrations to the dev Ledger
uv run pytest                                         # unit + DB tests (TEST_DATABASE_URL from .env)
uv run ruff check . && uv run ruff format --check .
uv run python -m worker                               # dev worker
uv run python -m evals.run healthcheck                # needs llama-server on 127.0.0.1:8080
```

Real recordings, transcripts and exports belong to the separate `clworker` Linux user and live in
`/home/clworker/cl-private/`, never in this repo. Claude Code cannot read them.
