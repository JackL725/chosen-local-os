# Phase 0 design: Foundation

Oct 10, 2026 · approved by Jack and built the same day · see "As built" below for what changed

This turns the Phase 0 section of `docs/build-plan.md` and section 8 of `docs/business-plan-v1.1.md` into a concrete design: the topology, the file list, the full Ledger schema, the decisions behind them, and the questions that need an answer before or during the build.

`CLAUDE.md` and `.claude/rules/worker.md` are not in the project, so where the build plan points to them (the import-boundary test) this document states what it assumes. Correct it if the rules file says otherwise.

---

## As built (changes from the proposal)

The repo's `CLAUDE.md` and `.claude/rules/` were not visible when this was written. Where they
differ from the proposal, the rules won:

- **Cloud boundary.** `cloud_llm.complete()` requires `contains_prospect_data=False` explicitly
  (no provenance wrapper). The import test walks every module's imports transitively, and also
  fails on any cloud SDK, cloud model host, or HTTP client outside `worker.local_llm`,
  `worker.slack` and `worker.cloud_llm`.
- **Terminal job status is `failed`** (not `dead`), as the worker rules say. Model validation
  failures retry once inside the model client, then fail the job. Network errors back off
  (30 s, 2 min, 8 min) up to three attempts.
- **`updated_at` on every table**, including the append-only `events` and `consent_records`,
  as the db rules require. They never change, because updates and deletes are refused by trigger.
- **Review console lives in `review/`** and binds 127.0.0.1:8090 in WSL.
- **Tailscale runs on Windows only**, not inside WSL. Tailscale's docs warn against running
  both. Windows `tailscale serve` publishes the console on 8443.
- **Postgres on the VPS** is published on 127.0.0.1 and exposed with
  `tailscale serve --tcp`, which persists across reboots. This avoids the known
  Docker-before-tailscaled boot race when binding to the 100.x address.
- **Caddy** is built with the `caddy-ratelimit` plugin, serves only n8n's webhook and form
  paths, and runs under a compose profile so the VPS can run before the domain exists.
- **`api.queue_health`** computes `should_alert` (first crossing, then every 4 h) so an
  overnight workstation shutdown does not post every 15 minutes.
- **Job payloads, results and free-text notes** are recorded in events as "changed", never by
  value. The synthetic leak test caught the original version copying payloads into events.
- **Every n8n workflow** sets an `error-handler` workflow that writes `workflow.failed` (IDs
  only) through `api.record_workflow_error`.
- **No dev seed file.** Seeding need-states or templates would invent business policy
  (decisions items 1, 5 and 9 are open).
- **Q3 (alert threshold) is a labelled placeholder of 4 hours** in
  `n8n/workflows/stale-job-alert.json` until decisions item 24 is DONE.

---

## 1. Requirements

**Functional (Phase 0 only)**

- A dev environment that runs Postgres 16 and n8n with one command.
- A production skeleton on the VPS (Postgres, n8n, Caddy) and on the workstation (llama-server, worker, review console placeholder), all restarting on their own.
- The Ledger schema, applied by `dbmate up` to an empty database.
- A worker that claims jobs, calls the local model, validates the result, writes it back with an event, retries, and alerts.
- An eval harness, encrypted backups, and a stale-job alert.

**Non-functional**

| Concern | Target | Why |
| --- | --- | --- |
| Volume | Tens of leads and jobs a day in V1; hundreds at the $20k/month level | Section 6: 140–200 leads a month at target |
| Speed-to-lead | Template out in under 60 s with the workstation off | Design rule 3. Phase 0 only has to not get in the way |
| Job latency | Test job round trip under 30 s | Phase 0 acceptance test |
| Availability | VPS always on. Workstation may be off for hours | Design rule 2 |
| Privacy | Call content never leaves the workstation; logs, Slack and n8n carry IDs only | Guardrail "prospect conversations stay local" |
| Auditability | Every row change produces an event with an actor | Ledger entities spec |
| Cost | One small VPS; no paid services added in Phase 0 beyond backup storage | Section 8: tens of dollars a month |

**Constraints:** one builder (Jack), Python with uv, Postgres 16, dbmate, n8n Community self-hosted, Tailscale, a Windows workstation running WSL2 with an RTX 5090.

---

## 2. Topology

```
                         Public internet
                               |
                         443 (webhooks only)
                               |
 VPS ───────────────────────────────────────────────────────────────
   Caddy ──► n8n  (/webhook/* public; editor UI on Tailscale only)
              │  role: n8n_app (EXECUTE on api.* functions only)
              ▼
   Postgres 16   databases: ledger, n8n
     bound to the VPS Tailscale IP only, never 0.0.0.0
   pg_backup timer ──► encrypted dump ──► backup target
 ───────────────────────────────────────────────────────────────────
                               ▲
                Tailscale (outbound from both ends, no open home port)
                               │  role: worker
 Workstation (WSL2, systemd) ──────────────────────────────────────
   cl-worker      claims jobs over Tailscale, writes results + events
   llama-server   127.0.0.1 only; OpenAI-compatible API
   cl-console     placeholder in Phase 0; binds the Tailscale IP only
   PRIVATE_DATA_DIR=/home/clworker/cl-private   (recordings, transcripts,
                  private evals; restic ──► backup target)
 ───────────────────────────────────────────────────────────────────
   Slack incoming webhook ◄── worker and n8n (IDs and links only)
```

**Data flow for a job.** A producer (n8n flow or the worker itself) calls `api.enqueue_job(...)`. The worker claims it with a lease, does the work locally, writes structured results and an event in one transaction, and marks the job done. If the worker dies, the lease expires and the job is claimable again.

---

## 3. Key decisions and trade-offs

| # | Decision | Chosen | Alternative | Why |
| --- | --- | --- | --- | --- |
| D1 | Job queue | A `jobs` table, claimed with `FOR UPDATE SKIP LOCKED` and a lease column | Redis, pg-boss, n8n queue mode | Volumes are tiny, the queue lives in the Ledger so jobs and events share a transaction, and there is one less service to run and back up |
| D2 | Crash recovery | Lease (`lease_expires_at`), renewed by heartbeat, reaped by the worker on startup and every minute | Hold a transaction open for the whole job | Transcription can take minutes over a Tailscale link; a long open transaction is fragile. A lease makes "kill the worker mid-job" recoverable by design |
| D3 | Event capture | A trigger on every table writes the event. It reads the actor from a session setting and **raises an error if none is set** | Each writer remembers to insert an event | "An events row for every change" becomes something the database enforces, including for n8n writes. The application still adds domain events (e.g. `match.confirmed`) on top |
| D4 | How n8n writes | Through SQL functions in an `api` schema (`api.create_lead`, `api.enqueue_job`, `api.record_outbound`). The n8n role has no direct table writes | n8n Postgres nodes inserting into tables | Functions set the actor, validate, and keep business rules in one place. Cost: changing a flow sometimes means a migration |
| D5 | Call-derived text | Structured values (budget, readiness, need-state) in the Ledger. Free text quoted or paraphrased from a call (goals, objections, match reasons citing the call) stays on the workstation; the Ledger holds a pointer and a hash | Everything in the Ledger | The plan keeps prospect conversations local. The Ledger is on a rented VPS. **This is question Q1** |
| D6 | Spans | Stored as offsets (ms or character range) into the local transcript, never as text | Copy the passage | Same reason as D5; the review console resolves offsets locally |
| D7 | Enumerations | `text` columns with `CHECK` constraints | Postgres `ENUM` types | Adding a value is a one-line migration instead of an `ALTER TYPE` |
| D8 | Need-states | A `need_states` lookup table | Hard-coded values | Decisions item 1 is open; launch need-states become seed data, not a migration |
| D9 | IDs and money | `uuid` primary keys (`gen_random_uuid()`); money in integer cents; all times `timestamptz` | Serial IDs; numeric dollars | IDs are safe to put in Slack and URLs; cents avoid rounding |
| D10 | Vetting rule | A trigger refuses `coaches.status = 'active'` unless all six vetting items are verified by a founder | Check in application code | Phase 1's test ("a coach with a missing item cannot be marked active") holds no matter which tool writes |
| D11 | Database roles | `ledger_owner` (migrations), `n8n_app`, `worker`, `console`, `reporting` (read-only). `UPDATE`/`DELETE` on `events` and `consent_records` revoked from all of them | One shared login | Least privilege; append-only is enforced, not promised |
| D12 | n8n exposure | Caddy proxies only `/webhook/*` publicly. The editor UI is reachable over Tailscale | Editor UI on the public domain behind n8n's login | The editor holds every credential; there is no reason for it to face the internet |
| D13 | Model client | `httpx` with Pydantic models and a JSON-schema `response_format`; thinking off via the chat-template flag | The `openai` SDK | Fewer moving parts; the request shape is the same. Exact llama-server parameters are confirmed against its docs at build time |

---

## 4. Files

```
pyproject.toml, uv.lock          uv project; ruff, pytest, psycopg[binary,pool], pydantic,
                                 pydantic-settings, httpx
.pre-commit-config.yaml          ruff (lint + format), pytest unit suite
.github/workflows/ci.yml         lint + unit tests with the fake model client;
                                 Postgres service container for migration and queue tests
.gitignore                       .env*, audio, PRIVATE_DATA_DIR, evals/private
.env.example                     every variable, no values

db/migrations/20261010000001_ledger_init.sql   schema, triggers, roles, api functions
db/schema.sql                    dbmate dump, committed
db/seeds/dev.sql                 actors, one test template, placeholder need-states

deploy/compose.dev.yml           postgres:16, n8n; ports on 127.0.0.1
deploy/compose.prod.yml          postgres:16 (published on the Tailscale IP only), n8n, caddy,
                                 restart: unless-stopped, named volumes
deploy/Caddyfile                 public /webhook/* only; TLS automatic
deploy/postgres/init/01-roles.sql  creates the ledger and n8n databases and roles
deploy/backup/pg_backup.sh       pg_dump | encrypt | upload; systemd timer on the VPS
deploy/tailscale-acl.hujson      workstation → VPS:5432; founders → workstation:console port
deploy/systemd/llama-server.service, cl-worker.service, cl-console.service,
               cl-restic.service, cl-restic.timer
deploy/windows/start-wsl.md      Task Scheduler task that starts the WSL distro at boot
deploy/README.md                 numbered VPS and workstation steps

worker/config.py                 settings from the EnvironmentFile
worker/db.py                     connection pool; every transaction sets the actor
worker/queue.py                  claim, renew lease, complete, fail with backoff, reap
worker/main.py                   loop, dispatch registry, heartbeat, graceful SIGTERM
worker/jobs/__init__.py          registry: job_type → handler
worker/jobs/healthcheck.py       synthetic job used by the acceptance test
worker/local_llm.py              structured-output client, validation, one retry, metrics
worker/cloud_llm.py              stub: allowlist of callers + provenance check, refuses by default
worker/provenance.py             Provenanced[T] wrapper: public_coach | founder_brief | prospect | call
worker/logging.py                JSON logs; scrubbing filter (IDs, counts, timings only)
worker/slack.py                  incoming-webhook alerts, IDs and console links only

console/app.py                   placeholder: /healthz, binds the Tailscale IP (built out in 2c)

evals/run.py                     run a system's prompts over evals/synthetic/<system>/, score
evals/synthetic/healthcheck/     one example so the harness is testable
scripts/eval_private.sh          same over $PRIVATE_DATA_DIR/evals/, prints aggregates only
scripts/deploy_local.sh          copy commit to /opt/chosen-local, uv sync, install units

n8n/workflows/stale-job-alert.json   schedule: oldest pending job older than threshold → Slack

tests/fakes/fake_llm.py
tests/unit/test_queue.py, test_local_llm.py, test_provenance.py,
           test_import_boundary.py, test_log_scrubbing.py
tests/db/test_migrations.py, test_events_trigger.py, test_vetting_rule.py   (Postgres in CI)

docs/runbook.md                  start/stop/health/recover for every Phase 0 service; restore test
docs/integrations.md             Slack webhook row filled in
```

**Assumed import-boundary rule.** `worker/cloud_llm.py` may be imported only by modules on an explicit allowlist (in Phase 0, none; later recruiting research and brief-based script drafts). The test walks the import graph of `worker/` and fails if any module that touches `PRIVATE_DATA_DIR`, `calls`, `call_extractions` or `leads` imports `cloud_llm`, directly or transitively. At runtime `cloud_llm` also refuses any input whose provenance is `prospect` or `call`.

**Log-scrubbing test.** Sentinel strings (fake name, phone, email, a fake transcript line) are fed through a full healthcheck job, including a forced failure path; the test asserts none of them appear in captured log output, Slack payloads or `jobs.error_detail`.

---

## 5. Ledger schema

### Conventions

- Every table has `id uuid primary key default gen_random_uuid()`, `created_at timestamptz not null default now()` and `updated_at timestamptz not null default now()` (maintained by trigger), except the append-only tables noted below, which have no `updated_at` because their rows never change.
- Every insert, update and delete on every table writes an `events` row through one trigger. The actor comes from `set_config('app.actor', …)`; `app.job_id`, `app.prompt_version`, `app.model` and `app.approval_id` are picked up when set. No actor, no write.
- Event payloads hold changed column names and non-sensitive values only. Columns marked **P** below are personal or call-derived; the trigger records that they changed, not what they changed to.
- Status columns are `text` with `CHECK` constraints listing the values.

### Reference

**`actors`** — every human or system that can change the Ledger

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| kind | text | founder, system, coach, lead, external |
| name | text unique | `sam`, `jack`, `intake`, `worker`, `safety_screen`, … |
| active | bool | |

**`need_states`**

| Column | Type | Notes |
| --- | --- | --- |
| key | text pk | e.g. `career` |
| label | text | |
| active | bool | |
| launched_at | timestamptz | null until it is a launch need-state |

### Leads and consent

**`leads`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| first_name, phone_e164, email | text | **P** |
| need_state_key | text fk need_states | |
| budget_band, income_band, start_timeframe | text | bands from decisions item 7 |
| intake_note | text | **P**; prospect's own free text, safety-screened |
| submitted_at | timestamptz | |
| source_platform | text | meta, tiktok, organic, referral, test |
| ad_id | uuid fk ads | null if unknown |
| utm_source, utm_medium, utm_campaign, utm_content, click_id, landing_variant | text | attribution |
| close_lead_id | text unique | |
| dedupe_key | text | rule from decisions item 20 |
| merged_into_lead_id | uuid fk leads | set on duplicates |
| status | text | new, booked, called, qualified, disqualified, matched, introduced, won, lost, dormant |
| qualification_result | text | qualified, not_qualified, null |
| qualification_reason | text | short code, not prose |
| prescore | smallint | |
| first_contact_at | timestamptz | speed-to-lead |
| consent_call, consent_text | bool | current state, mirrored from `consent_records` |
| consent_call_at, consent_text_at | timestamptz | |
| consent_wording_version | text | version of `docs/inputs/consent.md` shown |
| safety_hold | bool default false | |
| safety_hold_set_by, safety_hold_cleared_by | uuid fk actors | |
| safety_hold_set_at, safety_hold_cleared_at | timestamptz | |

**`consent_records`** (append-only, new) — the history behind the flags, including STOP replies

| Column | Type | Notes |
| --- | --- | --- |
| id, created_at | | |
| lead_id | uuid fk leads | |
| channel | text | call, text, email, recording, coach_share |
| action | text | granted, revoked |
| source | text | intake_form, sms_reply, call, founder |
| wording_version | text | |
| evidence_ref | text | form submission id, message id |

**`bookings`** (new) — Chosen Local calls booked through Cal.com; needed for booking and show rates

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| lead_id | uuid fk leads | |
| calcom_booking_uid | text unique | |
| scheduled_for | timestamptz | |
| status | text | booked, rescheduled, cancelled, attended, no_show |
| status_changed_at | timestamptz | |

### Operations

**`events`** (append-only)

| Column | Type | Notes |
| --- | --- | --- |
| id | bigint identity | ordering |
| occurred_at | timestamptz | |
| entity_type | text | table name or domain entity |
| entity_id | uuid | |
| event_type | text | `row.insert`, `row.update`, `lead.safety_hold_set`, … |
| actor_id | uuid fk actors | not null |
| job_id | uuid fk jobs | |
| approval_id | uuid fk approvals | the "approver" link |
| prompt_version, model | text | |
| payload | jsonb | changed columns; values only for non-P columns |

**`jobs`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| job_type | text | `healthcheck`, later `prescore`, `safety_screen`, `process_call`, … |
| payload | jsonb | IDs and parameters only, never content |
| entity_type, entity_id | text, uuid | lets a safety hold cancel a lead's pending jobs |
| status | text | pending, running, succeeded, failed, dead, cancelled |
| priority | smallint | |
| run_after | timestamptz | backoff |
| attempts, max_attempts | smallint | default max 3 |
| claimed_by | text | worker id |
| claimed_at, lease_expires_at | timestamptz | |
| idempotency_key | text unique | optional |
| result_ref | jsonb | IDs of rows written, or a local path for private output |
| error_class | text | exception class name |
| error_detail | text | scrubbed, short |
| finished_at | timestamptz | |

**`worker_heartbeats`** (new)

| Column | Type | Notes |
| --- | --- | --- |
| worker_id | text pk | host + process |
| host, git_sha | text | |
| started_at, last_seen_at | timestamptz | |
| current_job_id | uuid fk jobs | |

**`approvals`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| kind | text | message, outreach, invoice, match, template, spend, safety_clear |
| subject_type, subject_id | text, uuid | what is being approved |
| proposed_by | uuid fk actors | the system |
| proposal_ref | text | where the draft lives; a local path when it holds call content |
| proposal_hash | text | so the sent version can be checked against the approved one |
| decided_by | uuid fk actors | must be a founder |
| decision | text | approved, rejected, edited |
| decision_note | text | |
| decided_at | timestamptz | |

### Calls

**`calls`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| lead_id | uuid fk leads | |
| booking_id | uuid fk bookings | |
| close_activity_id | text unique | |
| started_at | timestamptz | |
| duration_s | integer | |
| recording_path, transcript_path | text | relative to `PRIVATE_DATA_DIR`; never content |
| recording_sha256 | text | |
| transcription_model, diarization_model | text | |
| readiness_rating | smallint check 1–5 | |
| processing_status | text | pending, transcribed, extracted, reviewed, failed |
| reviewed_by | uuid fk actors | |
| reviewed_at | timestamptz | |
| purge_after, purged_at | timestamptz | retention from decisions item 16 |

**`call_extractions`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| call_id | uuid fk calls | |
| field_name | text | from `docs/inputs/call-fields.md` |
| value_json | jsonb | structured values (budget, readiness, need-state, lists of codes) |
| value_ref | text | pointer to local text for free-text fields (see Q1) |
| span_start_ms, span_end_ms | integer | offsets into the local transcript |
| speaker | text | sam, prospect |
| confidence | real | |
| prompt_version, model | text | |
| job_id | uuid fk jobs | |
| corrected_value_json, corrected_value_ref | jsonb, text | |
| corrected_by | uuid fk actors | |
| corrected_at | timestamptz | |

**`safety_flags`** (new) — one row per flag, so holds can be audited and recall measured

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| lead_id | uuid fk leads | |
| source | text | intake, transcript |
| call_id | uuid fk calls | null for intake |
| layer | text | phrase_list, model |
| category | text | crisis, self_harm, clinical, addiction, medical, other |
| span_ref | jsonb | offsets only |
| score | real | model layer |
| job_id | uuid fk jobs | |
| cleared_by | uuid fk actors | founder |
| cleared_at | timestamptz | |
| resolution | text | cleared_false_alarm, referred_out, other |

### Coaches

**`coaches`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| display_name, legal_name, email, phone, website | text | |
| profile_summary | text | from recruiting research; public information |
| price_per_month_cents | integer | |
| program_length_months | smallint | |
| capacity_open | smallint | new clients they can take now |
| capacity_confirmed_at | timestamptz | weekly check |
| home_area | text | geography rule waits on decisions item 19 |
| serves_remote | bool | |
| status | text | prospect, vetting, active, paused, removed |
| status_changed_by | uuid fk actors | founder for active |
| status_changed_at | timestamptz | |
| partner_agreement_version | text | |
| source | text | research, referral, inbound |

**`coach_need_states`** (new) — a coach can cover more than one

| Column | Type | Notes |
| --- | --- | --- |
| coach_id | uuid fk coaches | pk part |
| need_state_key | text fk need_states | pk part |

**`vetting_items`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| coach_id | uuid fk coaches | |
| item_type | text | interview, reference_1, reference_2, paying_client_evidence, program_and_price, signed_agreement |
| evidence_location | text | |
| notes | text | |
| verified_by | uuid fk actors | must be a founder (trigger) |
| verified_at | timestamptz | |
| | | unique (coach_id, item_type) |

### Matching

**`matches`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| lead_id | uuid fk leads | |
| run | text | pre_call, post_call |
| status | text | proposed, confirmed, introduced, accepted, declined, expired |
| chosen_coach_id | uuid fk coaches | |
| confirmed_by | uuid fk actors | |
| confirmed_at | timestamptz | |
| override_reason | text | required when the chosen coach is not rank 1 (check) |
| prompt_version, model | text | |
| introduced_at | timestamptz | |
| coach_response | text | accepted, declined, no_response |
| coach_responded_at | timestamptz | |
| coach_response_seconds | integer generated | responded_at − introduced_at |

**`match_candidates`** (new) — the ranked list, one row per coach considered

| Column | Type | Notes |
| --- | --- | --- |
| match_id | uuid fk matches | pk part |
| coach_id | uuid fk coaches | pk part |
| passed_filters | bool | |
| filter_failures | jsonb | which hard filters failed |
| rank | smallint | null if filtered out |
| reasons | jsonb | each reason: short text + citations `[{kind: field|vetting_item, id}]`; reasons quoting a call follow Q1 |

### Fit calls, outcomes and money

**`fit_calls`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| match_id | uuid fk matches | |
| lead_id, coach_id | uuid | |
| scheduled_for | timestamptz | |
| booking_ref | text | |
| held | bool | |
| held_reported_by | uuid fk actors | |

**`outcomes`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| fit_call_id | uuid fk fit_calls | |
| lead_id, coach_id | uuid | |
| result | text | closed, lost, pending |
| reported_by | uuid fk actors | coach, lead or founder |
| reported_at | timestamptz | |
| closed_at | timestamptz | close-reporting lag = reported_at − closed_at |
| first_payment_at | timestamptz | commission trigger in the proposed terms |
| program_price_cents | integer | |
| lost_reason | text | code |

**`commissions`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| outcome_id | uuid fk outcomes unique | |
| coach_id | uuid fk coaches | |
| amount_cents | integer | |
| earned_at | timestamptz | |
| attribution_window_end | timestamptz | |
| invoice_draft_ref | text | |
| approval_id | uuid fk approvals | required before status leaves draft |
| qbo_invoice_id | text | |
| invoiced_at, due_at, paid_at | timestamptz | |
| status | text | draft, approved, sent, paid, reversed, written_off |

**`commission_adjustments`** (new) — refunds and reversals as their own records

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| commission_id | uuid fk commissions | |
| kind | text | refund_reversal, partial_reversal, chargeback, write_off |
| amount_cents | integer | negative |
| reason | text | |
| approval_id | uuid fk approvals | |

### Messaging

**`templates`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| key | text | `booking_link`, `reminder_24h`, … |
| version | integer | unique (key, version) |
| channel | text | sms, email |
| audience | text | prospect, coach |
| body | text | immutable once approved (trigger) |
| variables | jsonb | allowed fields |
| status | text | draft, approved, retired |
| approved_by | uuid fk actors | founder |
| approved_at | timestamptz | |

**`outbound_messages`**

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| recipient_type | text | lead, coach |
| lead_id, coach_id | uuid | exactly one set (check) |
| channel | text | |
| template_id | uuid fk templates | exactly one of template_id or approval_id (check) |
| approval_id | uuid fk approvals | |
| rendered_hash | text | proves what was sent without storing the body |
| provider_message_id | text | Close activity id |
| status | text | queued, sent, delivered, failed, refused |
| refusal_reason | text | no_consent, safety_hold, unapproved, other |
| sent_at | timestamptz | |

### Ads

**`ads`** (new) — one row per ad, so leads and spend join on a real key

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | |
| platform | text | meta, tiktok |
| platform_ad_id | text | unique (platform, platform_ad_id) |
| campaign_id, adset_id | text | |
| name | text | |
| need_state_key | text fk need_states | |
| status | text | |

**`ad_daily`** — proposed merge of `ad_spend` and `ad_performance` (see Q8)

| Column | Type | Notes |
| --- | --- | --- |
| ad_id | uuid fk ads | pk part |
| date | date | pk part, platform account time zone |
| spend_cents | integer | |
| impressions, clicks, platform_leads | integer | |
| imported_at | timestamptz | |
| import_run_id | text | |

That is 25 tables: the 15 named in the build plan (with `ad_spend` and `ad_performance` merged) plus `actors`, `need_states`, `consent_records`, `bookings`, `worker_heartbeats`, `safety_flags`, `coach_need_states`, `match_candidates`, `commission_adjustments` and `ads`. Each addition exists because a later phase's test or one of the twelve viability metrics needs it.

---

## 6. Worker and model client

**Claim.** `UPDATE jobs SET status='running', claimed_by=$1, claimed_at=now(), lease_expires_at=now()+lease, attempts=attempts+1 WHERE id = (SELECT id FROM jobs WHERE status='pending' AND run_after<=now() ORDER BY priority, run_after FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`. Polling every 2 s; `LISTEN/NOTIFY` can be added if latency matters.

**Lease.** 5 minutes by default, set per job type (transcription longer). Renewed with the heartbeat every 30 s. A reaper returns expired `running` jobs to `pending`.

**Failure.** Exponential backoff with jitter (30 s, 2 min, 8 min). After `max_attempts` the job goes to `dead` and Slack gets `job <id> (<type>) dead: <error_class>`.

**Result write.** Handler output, the job's `succeeded` status and the domain event commit in one transaction, with `app.actor='worker'`, `app.job_id`, `app.prompt_version` and `app.model` set.

**Local model client.** POST to `$LOCAL_LLM_BASE_URL/v1/chat/completions` with a JSON-schema response format built from the Pydantic model, temperature 0 for extraction, thinking disabled. Validate with Pydantic; on failure retry once with the validation error appended; then raise `ModelOutputInvalid`. Log tokens in/out, latency and model name, never prompt or output text.

**llama-server.** Bound to 127.0.0.1, slots and metrics endpoints off, no prompt logging, fixed context size and parallel slots sized for 10 concurrent draft jobs (the Phase 1 load test). Flags are confirmed against llama.cpp's current docs at build time and recorded in `docs/integrations.md`.

---

## 7. Acceptance tests and how each is checked

| Test | How |
| --- | --- |
| Dev stack comes up | `docker compose -f deploy/compose.dev.yml up -d` then a health script hits Postgres and n8n |
| Migration applies | `dbmate up` on an empty database in CI and locally; `dbmate down`/`up` round trip |
| Job round trip under 30 s | Integration test inserts a `healthcheck` job; the worker calls the real local model; the test asserts result, event and elapsed time |
| Kill mid-job | Test starts a slow job, sends SIGKILL, shortens the lease, restarts the worker, asserts the job completes; a forced-failure job ends `dead` and the fake Slack endpoint receives one alert |
| Import boundary, log scrubbing | Unit tests in CI |
| Services survive restart | `wsl --shutdown`, wait, Windows task restarts the distro; `systemctl is-active` on all three units as `clworker` |
| Private eval prints only metrics | Script output checked against a regex allowing only metric lines |
| Events enforced (added) | Test writes without an actor and expects an error; test tries `UPDATE events` as each role and expects a permission error |

---

## 8. Scale and reliability

- **Load.** At the $20k/month level the Ledger sees a few thousand rows a month. One small VPS and one Postgres instance carry this for years; no caching layer is needed.
- **Single points of failure.** The VPS is the one thing a prospect depends on; nightly encrypted dumps plus a written restore test cover it, and a second region is not worth it before the loop is proven. The workstation going down only delays model jobs, by design.
- **Monitoring.** Stale-job alert (n8n schedule), dead-job alert (worker), heartbeat age (the stale-job flow also alerts when no heartbeat for the threshold while jobs are pending), backup success or failure posted daily.

**Revisit as it grows**

- Partition `events` by month once it passes a few million rows.
- Move to managed Postgres when a second caller or city makes VPS downtime costly.
- Replace polling with `LISTEN/NOTIFY` or a dedicated queue only if job volume or latency demands it.
- A second GPU host behind the same queue when model jobs back up; the lease design already allows several workers.

---

## 9. Questions

| # | Question | My default if you agree |
| --- | --- | --- |
| Q1 | Should free text derived from calls (goals, objections in his words, match reasons that quote him) live only on the workstation, with the cloud Ledger holding structured values, offsets and pointers? | Yes. It costs a small local store read by the review console and the handoff-brief job, and keeps the "prospect conversations stay local" guardrail literal |
| Q2 | Database-enforced events with a required actor, and n8n writing only through `api.*` functions (D3, D4)? | Yes |
| Q3 | Alert threshold for the oldest pending job (decisions item 24) | 4 hours |
| Q4 | Backup target and key custody for both the VPS dumps and the workstation restic repo | One Backblaze B2 bucket with two prefixes; keys in both founders' password managers, never on the VPS in plain text |
| Q5 | The domain for Caddy (brand item 6 is open) | A subdomain such as `hooks.<domain>` once the domain exists; until then the VPS work stops at Tailscale-only access |
| Q6 | Workstation setup: confirm Windows + WSL2, Tailscale installed inside the WSL distro (so the console can bind its Tailscale IP), and a Task Scheduler job to start WSL at boot | Yes to all three; WSL's idle shutdown turned off so the distro keeps running |
| Q7 | `CLAUDE.md` and `.claude/rules/worker.md` are not in this project. Is the import-boundary rule in section 4 what the rules file says? | Use section 4 until I see the file |
| Q8 | Merge `ad_spend` and `ad_performance` into `ad_daily`, and approve the ten added tables and `events` having no `updated_at` | Yes |

Nothing else in `docs/decisions.md` blocks Phase 0. Items 1, 7, 16, 19 and 20 shape seed data and later columns, not this migration.
