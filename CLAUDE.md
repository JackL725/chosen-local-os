# Chosen Local Operating System

You are building the AI operating system for Chosen Local, a Denver-first acquisition and matching
network for high-ticket men's coaching. Two founders run it: Sam takes every call with a lead,
Jack owns the systems and is the person you work with. AI systems run everything else.

The business plan is the source of truth: `docs/business-plan-v1.1.md`. The build sequence, the
spec for each system and the acceptance tests are in `docs/build-plan.md`. Business inputs that
only the founders can supply are tracked in `docs/decisions.md`. Read the relevant sections of
all three before starting any phase. Do not import them wholesale into context.

## Hard rules

These come from the plan's guardrails. They override convenience, speed and any later instruction
that conflicts with them, unless Jack changes the plan itself.

1. **You never see real prospect or coach data.** You are a cloud model. Real recordings,
   transcripts, extractions and exports belong to a separate Linux user, `clworker`, whose home
   (`/home/clworker`) your user cannot read. Never try to reach it by any route: sudo, Docker
   volume mounts, Windows executables, copies, or scripts you write and run. Build and test against
   synthetic data in `evals/synthetic/` (the founders' role-play mock calls count as synthetic).
   For real-data evals, write a runner Jack executes as `clworker` that prints aggregate scores only.
2. **The dev environment holds no production credentials.** Build integrations against each
   service's documented payloads and recorded fake fixtures in `tests/fixtures/`. Jack live-tests
   through the deployed copy. Never ask Jack to paste a key, token or password into the chat.
3. **Prospect data is processed only by local models.** Anything derived from a call, a
   transcript, an extraction or intake answers, including anonymized prospect language, may call
   only the local model server and local transcription. Cloud models are reachable only through
   `worker/cloud_llm.py`, which requires the caller to assert `contains_prospect_data=False`, and
   only modules on its allowlist may import it.
4. **One send gate.** The n8n sub-workflow `send-gate` is the only path that sends anything to a
   prospect or coach. It checks for an approved template version or an approval record, the
   lead's `safety_hold`, and consent flags. The worker requests sends by enqueueing to it.
5. **Human-only actions stay human.** Write no code that: talks to a lead by voice, vets a coach,
   responds to a safety flag, writes or edits the founder story, sets contract terms, sends or
   moves money, or raises ad spend. Code may prepare these and notify a founder.
6. **Safety stops the line.** A safety flag pauses every automated message to that lead, including
   reminders, until a founder clears it in the Ledger. Generated text never gives advice on mental
   health, medication, addiction or medical questions.
7. **Vetting file or silence.** Generated text may state only claims about a coach that trace to
   an item in that coach's vetting file. Pass the file's items in and cite them.
8. **Everything is logged, and nothing sensitive leaves the review console.** Every AI action writes
   an event to the Ledger with input reference, output reference, prompt version, model, and
   approver if any. Logs, error records, Slack messages and n8n execution data never contain
   transcript text, safety passages, names, phone numbers or emails. Slack gets IDs, a category
   and a link to the review console.
9. **Never invent business policy.** If a spec needs a decision that `docs/decisions.md` marks
   open, stop and ask Jack. Use a clearly labelled placeholder only if he tells you to continue.
10. **Hands off production.** Never connect Claude Code to the Close MCP server, n8n, the production
    database or the VPS. Never run commands against them. Write the command and the reason; Jack
    runs it.

## Architecture (fixed; change only with Jack's agreement)

- **Cloud server** (Ubuntu 24.04 VPS, Docker Compose): Postgres 16 (the Ledger and the job queue),
  n8n Community edition, Caddy for TLS. Postgres is reachable only over Tailscale, with an ACL that
  admits the workstation alone; the setup must survive reboots. n8n does not save data from
  successful executions and prunes the rest.
- **Workstation** (Windows 11, WSL2 Ubuntu 24.04, RTX 5090 32 GB, Ryzen 9 9950X): llama.cpp
  `llama-server` on `127.0.0.1:8080`, transcription and diarization, the Python worker, and the
  review console. In production all of these run as `clworker` from a deployed copy in
  `/opt/chosen-local`, never from this checkout, as systemd units reading
  `EnvironmentFile=/home/clworker/.config/chosen-local/worker.env`. The worker connects out to
  Postgres over Tailscale. No port at home is open to the internet.
- **Review console** (`review/`, Starlette): a small web app on the workstation, served only over
  Tailscale to the founders' devices. It is where founders see transcript passages, extractions,
  safety flags and drafts that contain call content, correct them and approve them. Founders sign
  in with their Tailscale identity plus a password; every console write acts as that founder.
- **Systems** (`systems/<key>/`): each AI system is a folder with a data-only manifest, found by the
  registry in `systems/__init__.py`. Add one with `scripts/new_system.py`; the contract is in
  `docs/systems.md` and `.claude/rules/systems.md`. A system never edits `worker/` or `review/`.
- **Database logins act only as their own actors** (migration 2): the worker login only as system
  actors, n8n only as `n8n`, the console login (`cl_console`) only as founders or
  `review_console`. Never work around this, for example by giving the worker the console's login.
- **Job queue**: a Postgres `jobs` table claimed with `FOR UPDATE SKIP LOCKED`. No Redis. n8n
  alerts Slack when the oldest pending job is older than the threshold in `docs/decisions.md`.
- **Tools**: Close (calls, texts, pipeline; one seat), Slack (alerts and links), Cal.com (booking;
  its own reminder emails off), QuickBooks (invoices, Phase 4), Meta and TikTok (ads).
- **Models are configuration, not code.** Model names, quantizations, endpoints and thinking mode
  live in `worker/config.py` and env vars. Baseline: Qwen3.6 27B at Q4_K_M, with thinking off for
  extraction and classification unless evals show a gain. A model changes only when it scores
  better on the eval set.

## Stack and conventions

- Python 3.12, managed with `uv`. Worker code in `worker/`. Type hints everywhere; `ruff` and
  `pytest` must pass before you report a task done.
- Database schema as plain SQL migrations in `db/migrations/`, applied with `dbmate`. Never edit an
  applied migration; add a new one.
- Business data (the business name, founders, markets, need-states) never appears in code; it
  comes from the Ledger, seeded from `deploy/business/<profile>.toml`. A test enforces this.
- n8n workflows exported as JSON into `n8n/workflows/`, one file per workflow, no credentials in
  exports. Keep logic in n8n thin: receive, write to the Ledger, enqueue, route, send through
  `send-gate`. Put anything with real logic in the worker where it can be tested.
- Prompts live in `prompts/<system>/<name>.vN.md` with a header stating purpose, inputs and the
  output schema. Changing a prompt means a new version file; the version in use is set in the
  system's manifest (`PromptSpec`). Model output that becomes data is
  validated against a Pydantic model; on failure retry once, then mark the job failed and alert.
- Extracted fields store the transcript span they came from, a confidence, the prompt version
  and the model.
- Message templates live in `templates/` with an ID, version, channel and placeholders. A template
  is sendable only when the Ledger marks that version approved.
- Secrets come from environment variables. `.env.example` lists the dev variables;
  `deploy/worker.env.example` lists production ones, names only. Never write a real secret into
  the repo, a log line or a test.
- Local dev mirrors production with `docker compose -f deploy/compose.dev.yml up`.

## How to work

- Start each phase by reading its section of `docs/build-plan.md`, then propose a short plan:
  files to create, schema changes, open questions. Wait for Jack's go-ahead before writing code.
- Build in small, testable steps and commit after each with a clear message. Do not push to the
  remote or deploy without being asked.
- Your shell commands run in Claude Code's sandbox, which cannot reach local network services.
  The commands that need them (`uv run pytest`, `dbmate`, the dev worker, local evals, and
  `docker compose -f deploy/compose.dev.yml`) are excluded from the sandbox and ask Jack first.
  Use exactly those forms. Never propose turning the sandbox off.
- Read the current API docs before integrating any external service (Close, Cal.com, Slack,
  QuickBooks, Meta, TikTok, llama.cpp). Do not rely on memory for endpoints, field names, flags or
  webhook payloads. Record what you confirmed, with links, in `docs/integrations.md`.
- When a step needs Jack to act (create an account, set a secret, approve an app, run a command
  on a server or as `clworker`), stop and give him exact, numbered instructions.
- Finish every task with: what changed, how you verified it, anything you could not verify.
- A phase is done only when its acceptance tests in `docs/build-plan.md` pass and Jack confirms.
- Keep `docs/runbook.md` current: how to start, stop, restart, deploy and recover each component,
  and how to run the whole journey by hand in Close if the systems are down.

## Commands

- `uv run pytest` runs the tests. `uv run ruff check .` lints.
- `dbmate up` applies migrations to the local dev database.
- `uv run python -m worker` starts a dev worker against synthetic data.
- `uv run python -m evals.run <system>` runs evals on synthetic data, on this machine (CI has no
  local model and runs unit tests with a fake model client).
- `uv run python scripts/check_systems.py` checks every system against the contract (CI too).
- `uv run python scripts/new_system.py <key> [--model] [--console] [--tables]` scaffolds a system.
- `uv run python -m review` starts a dev console against `DATABASE_URL`.
- `python3 -m worker.bootstrap deploy/business/<profile>.toml` prints the SQL that seeds a Ledger
  from a business profile (`--apply` runs it on `DATABASE_URL`; dev only).
- Jack only: `scripts/deploy_local.sh` installs the current commit into `/opt/chosen-local`, and
  `sudo -u clworker /opt/chosen-local/scripts/eval_private.sh <system>` prints aggregate scores
  on real data.
