# Build plan

This file turns sections 7, 8 and 10 of `docs/business-plan-v1.1.md` into build steps. Work one
phase at a time. Each phase lists what to build, the specs, the acceptance tests that close it,
and the kickoff prompt Jack pastes to start it. Business phases 1 to 4 match the plan. Phase 0 is
setup and comes first.

Nothing beyond Phase 4 is built until a sale has closed.

---

## Phase 0: Foundation

**Goal:** a dev environment, a production skeleton and the Ledger schema, with nothing customer-facing.

### Build

1. Repo tooling: `pyproject.toml` (uv), ruff, pytest, a pre-commit config that runs both (Jack runs
   `uv run pre-commit install` himself; the sandbox cannot write to `.git/hooks`), and a GitHub
   Actions workflow that runs lint and unit tests with a fake model client. CI has no local model.
2. `deploy/compose.dev.yml`: Postgres 16 and n8n for local development.
3. `deploy/compose.prod.yml` and `deploy/Caddyfile` for the VPS: Postgres reachable only over
   Tailscale and surviving reboots, n8n behind Caddy with TLS on the public domain, n8n execution
   data for successful runs off with pruning on, nightly encrypted `pg_dump` to the backup target.
   `deploy/README.md` with numbered steps Jack runs on the VPS, including the Tailscale ACL that
   admits only the workstation to Postgres and the founders' devices to the review console.
4. Ledger schema, first migration (see "Ledger entities" below).
5. Worker skeleton: claims jobs from Postgres, dispatches by `job_type`, writes results and an
   event, retries with backoff, marks failures and alerts Slack with the job ID. Writes a heartbeat.
6. Local model client (`worker/local_llm.py`): OpenAI-compatible client for `LOCAL_LLM_BASE_URL`,
   JSON-schema structured output, thinking off by default, Pydantic validation, one retry, token
   and latency logging. `worker/cloud_llm.py` stub with the allowlist and provenance check, plus the
   import-boundary test described in `.claude/rules/worker.md`.
7. Eval harness: `evals/run.py` runs a system's prompts over `evals/synthetic/<system>/` and scores
   against expected outputs, locally. `scripts/eval_private.sh` does the same over
   `$PRIVATE_DATA_DIR/evals/` and prints only aggregate metrics.
8. Production layout on the workstation: `scripts/deploy_local.sh` (Jack runs it with sudo) copies
   the current commit to `/opt/chosen-local`, installs dependencies, and installs systemd units for
   `llama-server`, the worker and the review console, all running as `clworker` with
   `EnvironmentFile=/home/clworker/.config/chosen-local/worker.env`. Units restart on failure and
   start at boot. Run `llama-server` with its slot and metrics endpoints off and no prompt logging;
   confirm the current flags in llama.cpp's docs.
9. Encrypted backups (restic or similar) of `/home/clworker/cl-private` to the target Jack chose,
   run as `clworker`, with a restore test documented in the runbook.
10. Slack alert helper (incoming webhook, IDs only) and an n8n schedule that alerts when the oldest
    pending job is older than the threshold in `docs/decisions.md`.

### Ledger entities

Design the schema, but cover at least these, each with `created_at`, `updated_at`, and an
`events` row for every change:

- `leads`: contact info, source ad, need-state, income band, consent flags and timestamps, status,
  qualification result, safety_hold (bool, set_by, cleared_by, cleared_at)
- `events`: append-only; entity type and id, event type, actor (system name or founder), payload
  reference, prompt version, model, approver
- `jobs`: type, payload, status, attempts, claimed_by, claimed_at, result reference, error class
- `calls`: lead, Close activity id, recording and transcript locations (paths under
  `PRIVATE_DATA_DIR` on the workstation, never the content), duration, readiness rating,
  reviewed_by, purge_after
- `call_extractions`: call, field name, value, transcript span, confidence, prompt version, model,
  corrected_value, corrected_by
- `coaches`: profile, need-states, price per month, program length, capacity, geography, status
  (prospect, vetting, active, paused)
- `vetting_items`: coach, item type (interview, reference 1, reference 2, paying-client evidence,
  program and price, signed agreement), evidence location, verified_by, verified_at
- `matches`: lead, ranked candidate coaches with reasons, chosen coach, confirmed_by, override
  reason, coach response, response time
- `fit_calls`, `outcomes`: scheduled time, held, result (closed, lost, pending), reported_by,
  reported_at, first payment date
- `commissions`: outcome, amount, invoice draft, invoice id, status, refund and reversal records
- `templates`: id, version, channel, audience, body, approved_by, approved_at
- `approvals`: what was proposed, by which system, decided_by, decision, decided_at
- `outbound_messages`: lead or coach, template id and version or approval id, channel, status
- `ad_spend` and `ad_performance`: daily rows per ad, imported from Meta and TikTok

### Acceptance tests

- `docker compose -f deploy/compose.dev.yml up` gives a working local Postgres and n8n.
- `dbmate up` applies cleanly to an empty database.
- A test job inserted in the dev database is claimed by the worker, sent to the local model,
  validated, and written back with an event, in under 30 seconds.
- Killing the worker mid-job leaves the job retryable. Three failures post a Slack alert.
- The import-boundary test and the log-scrubbing test pass. CI runs lint and unit tests green.
- After `scripts/deploy_local.sh` and a WSL restart, the three services come back as `clworker`
  without anyone logging in to a shell.
- The private eval script prints only metrics.

### Kickoff prompt

> Read CLAUDE.md, then the Phase 0 section of docs/build-plan.md and section 8 of
> docs/business-plan-v1.1.md. Propose your plan for Phase 0: files, the full Ledger schema as a
> table list with columns, and any questions. Do not write code until I approve.

---

## Phase 1: Supply and offer

**Goal:** the coach side of the Ledger works, so recruiting and vetting run while the funnel is built.

### Build

1. **Coach operations, part 1 (system 7).**
   - Recruiting research: given a coach's public website or profile text, summarize their offer,
     price signals and need-state fit into a prospect-coach record. May use a cloud model; the
     input is public coach information, never prospect data.
   - Coach intake: a form or CSV import that creates `coaches` rows.
   - Vetting-file assembly: a checklist view per coach in the review console showing the items,
     their evidence and who verified each. Only a founder can mark an item verified or a coach
     active. The system never does.
   - Outreach drafts from the approved outreach template, sent for approval, then out through
     `send-gate`.
2. **Local model server under load:** 10 parallel draft jobs complete without errors.

### Acceptance tests

- A coach moves prospect → vetting → active only with founder actions recorded in `events`.
- A coach with a missing vetting item cannot be marked active.
- Outreach never sends without an approval record.

### Kickoff prompt

> Read the Phase 1 section of docs/build-plan.md and sections 7 and 9 of the business plan. Check
> docs/decisions.md for items 1 to 5. Plan Phase 1 and list anything you need from me.

---

## Phase 2: Funnel and system

**Goal:** a test lead can travel the whole journey. Build 2a to 2f in order, one session each.

### 2a. Landing page, intake and the send gate (system 2)

- Static landing page in `landing/` from `docs/inputs/brand.md` and `docs/inputs/intake-fields.md`,
  with the privacy policy page (decisions item 21). Consent checkboxes for calls, texts and the
  recording disclosure, worded exactly as in `docs/inputs/consent.md`.
- `send-gate` sub-workflow in n8n: checks approved template version or approval record,
  `safety_hold`, and consent for the channel, then sends through Close and records an
  `outbound_messages` row. Refuses and logs otherwise.
- Intake flow: validate → merge duplicates on the key in decisions item 20 → write `leads` row and
  event → create or update the Close lead → booking-link template through `send-gate` → enqueue
  `prescore` and `safety_screen` jobs.
- Reminders and no-show recovery from templates, driven by Cal.com webhooks, sent through
  `send-gate`. Cal.com's own reminder emails stay off.
- Spam protection: honeypot field and rate limit.
- Meta pixel and Conversions API: generic event names, no health language, no intake answers.
  Read Meta's current docs first.

**Tests:** booking link arrives in under 60 seconds with the workstation off. Duplicates merge. A
lead without call consent gets no call task; without text consent, no texts. A lead with
`safety_hold` set receives nothing, verified by trying every template.

> Kickoff: Read section 2a of docs/build-plan.md and sections 4 and 5 of the business plan. Check
> decisions items 6 to 9 and 20 to 21. List what is still open, then plan 2a.

### 2b. Safety screen (system 3)

- Runs on intake answers and on every transcript.
- Two layers: the phrase list in `docs/inputs/out-of-scope.md`, then a local-model classifier.
  Tune for recall: a false alarm costs a founder a minute; a miss costs far more.
- On a flag: set `safety_hold`, cancel pending messages, post to Slack the lead ID, the category
  and a review-console link, and log it. The passage itself is shown only in the review console.
  Never message the lead.

**Tests:** recall at least 0.98 on flagged cases in `evals/synthetic/safety/`. A flagged lead
receives zero automated messages until a founder clears the hold, end to end. No passage text
appears in Slack, n8n execution data or logs.

> Kickoff: Read section 2b of docs/build-plan.md and the guardrails in section 7 of the business
> plan. Check decisions item 10. Plan 2b, including the synthetic test set you will write.

### 2c. Call system and review console (system 4)

- **Review console:** a small web app run by the worker host as `clworker`, served only over
  Tailscale to the founders' devices, with a login. Shows extractions beside their source passages,
  safety flags, pre-call briefs and drafts with call content. Founders correct and approve there.
- **Before the call:** a one-page brief from intake data, attached to the Close lead and shown in
  the console 15 minutes before the booked time; Slack gets a link.
- **After the call:** n8n receives the Close call event and enqueues `process_call`. The worker
  downloads the recording through the Close API into `PRIVATE_DATA_DIR/recordings/`, transcribes,
  separates speakers (Sam vs. prospect), extracts the fields in `docs/inputs/call-fields.md` into
  `call_extractions` with spans, runs the safety screen, and writes a short call note to Close.
  The follow-up message is a draft approved in the console, then sent through `send-gate`.
- **Transcription choice:** compare Parakeet and Whisper large-v3 on the founders' mock calls in
  `evals/synthetic/calls/` against their hand-corrected reference transcripts (decisions item 17),
  by word error rate and speaker-labelling accuracy. Record the choice in `docs/integrations.md`.
  Diarization: pyannote `speaker-diarization-community-1`.
- **First 20 calls:** Sam's corrections are stored and those calls join the private eval set.
- **Retention:** a daily job deletes recordings and transcripts past the retention period
  (decisions item 16) and records the deletion.

**Tests:** a mock recording produces a complete extraction with spans in under 5 minutes.
Extraction accuracy on synthetic transcripts at least 0.9 per field. No transcript text in Close
beyond the short note, and none in Slack, logs or n8n. The console is unreachable off Tailscale.

> Kickoff: Read section 2c of docs/build-plan.md and section 7 of the business plan. Check
> decisions items 11, 12, 16, 17 and 23. Plan the review console first, then the call pipeline.

### 2d. Matching engine (system 5)

- Hard filters: need-state, active status, capacity, price within stated budget, and the geography
  rule in decisions item 19.
- Ranking: the local model ranks the coaches that pass and writes two or three reasons each,
  citing intake or call fields and vetting items only.
- Runs once after intake and again after the call. Sam confirms or overrides in the console.
  Override reasons are required and stored.

**Tests:** a coach failing any hard filter never appears. Every reason cites a real field or item.
With zero eligible coaches the system alerts instead of guessing.

> Kickoff: Read section 2d of docs/build-plan.md. Check decisions item 19. Plan 2d.

### 2e. Pre-sell and handoff (system 6)

- "Why this coach" summary for the prospect: a draft from the vetting file and the prospect's own
  stated goals, approved in the console.
- Coach handoff brief: goals, budget, readiness and objections, limited to what the prospect
  agreed to share (decisions item 13), never safety-screen details. Uses the coach templates in
  decisions item 18 and goes out through `send-gate` after approval.
- Fit-call booking and reminders to both sides. Coach response time is recorded.

**Tests:** every coach claim in a draft carries a vetting-item citation. Handoff briefs never
contain safety details or fields outside the agreed set.

> Kickoff: Read section 2e of docs/build-plan.md. Check decisions items 13 and 18. Plan 2e.

### 2f. Acquisition support (system 9, minimal)

- Script and hook drafts for Sam. Drafts from Jack's or Sam's written brief may use a cloud model.
  Drafts that draw on what prospects said on calls run on the local model only.
- Copy check against the policy text in `docs/inputs/meta-policy.md`.
- Invoice draft record in the Ledger for a closed test outcome, so the Phase 2 gate can be met.
  The QuickBooks push comes in Phase 4.

> Kickoff: Read section 2f of docs/build-plan.md. Check decisions item 14. Plan 2f.

### Phase 2 gate (the plan's gate)

A founder's test lead travels the whole journey: booking link in under a minute, call transcribed
and extracted, match recommended, handoff brief sent, test invoice drafted.

---

## Phase 3: Paid test

**Goal:** run $500 of ads through the system and fix what breaks.

### Build

- Daily ad report, the one new piece: Meta and TikTok spend and results imported into `ad_spend`
  and `ad_performance`, with cost per lead and per booked call by ad, posted to Slack each morning.
- Spend rule: the system may pause an ad that breaches the limit in decisions item 15 and must tell
  a founder. It never raises a budget.
- The first-20-calls review loop from 2c, in daily use.

### Acceptance tests

- 20 to 50 real leads handled.
- Lead counts and spend in the Ledger match the ad platforms within 5%. Cost per lead, booking
  rate and show rate are computed from the Ledger.

> Kickoff: Read the Phase 3 section of docs/build-plan.md and section 5 of the business plan.
> Check decisions item 15. Plan the ad report, reading the Meta and TikTok reporting docs first.

---

## Phase 4: Prove one loop

**Goal:** a real commission collected, with every step recorded.

### Build

1. **Close tracking and commission (system 8).** Scheduled check-ins with customer and coach after
   the fit call until an outcome is recorded, sent through `send-gate`. On a close: push the
   invoice draft to QuickBooks for founder approval, payment follow-up reminders, refund and
   reversal records per the commission terms in decisions item 4.
2. **Weekly operating review (system 10).** Every Monday: the twelve viability metrics from section
   6 of the plan, the funnel by ad, need-state and coach, anomalies, and two proposed experiments.
   Numbers come from SQL saved in the repo; the model writes the narrative only, from aggregates.

### Acceptance tests

- An outcome reported by either side updates the Ledger and stops the check-ins.
- An invoice is never sent without an approval record.
- Every number in the weekly review traces to a saved SQL query.

> Kickoff: Read the Phase 4 section of docs/build-plan.md and section 6 of the business plan.
> Check decisions items 4 and 22. Plan Phase 4, reading the QuickBooks API docs first.

---

## After the first loop (do not build yet)

Back office (system 11), automatic weekly capacity checks, spend recommendations, model-drafted
personal messages under approval, and matching that learns from outcomes.
