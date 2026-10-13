# Phase 1 plan: coach supply on a modular base

Oct 13, 2026 · proposal for Jack · nothing is built yet

Phase 1 was written as "coach operations, part 1". After the decisions below it is narrower on
features and wider on foundations: the review console's core, the coach roster and vetting file,
and a base that later AI systems plug into without touching the core. Recruiting research and
outreach move out and come back later as plug-in systems.

Sections 2 and 11 are the ones to read first. Section 2 records what you decided today. Section 11
lists what still needs your call before the build starts.

---

## 1. What Phase 1 delivers

| Deliverable | What it is |
| --- | --- |
| Modular base | A contract for "a system", a registry that discovers systems, an on/off switch, and a scaffold command. The existing healthcheck becomes the reference system. |
| Rules as data | Need-states, the vetting standard, markets and thresholds live in Ledger tables that founders edit in the console. |
| Console core | The review console becomes a real app: sign-in, founder identity on every write, layout, settings screens. System screens plug into it. |
| Coaches system | Roster, coach form and CSV import, the vetting file with evidence, the status flow, and a readiness view for the Phase 1 gate. No model. |
| Load check | Ten parallel jobs through the worker and the local model server, using the reference system. |

**The gate is unchanged:** two coaches per launch need-state, each with a complete vetting file and
a signed agreement. The build can finish before the gate does. Phase 2 can start once the build's
acceptance tests pass, while recruiting continues.

---

## 2. Decisions made on Oct 13

| # | Question | Decision |
| --- | --- | --- |
| 1 | Review console is a placeholder until 2c | Build the console core now. 2c adds the call screens on top. |
| 2 | Recruiting research | Deferred. Returns later as a plug-in system. |
| 3 | Coach outreach | Deferred too. Founders recruit outside the system and add coaches to the roster. This replaces the earlier "founder sends by hand" answer. |
| 4 | What must stay changeable | Plug-in AI systems, rules editable as data, models set per system, more cities, other businesses. |
| 5 | Other businesses and cities | A market (city) key in the schema now. Another business gets its own install of the same code, with its own database, Close and Slack. No shared multi-tenant database. |
| 6 | Launch need-states (item 1) | Fitness and performance; life direction and discipline. Adding a need-state later must need no code change. |
| 7 | Vetting standard (item 2) | Not decided. The checklist is data. The plan's six-item default is loaded as a draft and a founder activates a standard before the first coach is vetted. |
| 8 | Console sign-in | Tailscale identity plus a per-founder password. |
| 9 | Vetting evidence | Stored in the workstation's private store, uploaded through the console. |
| 10 | Partner and commission terms (items 3, 4) | Left open. They block the gate, not the build. Section 10 proposes a decide-by date. |

Two consequences of decision 6 that the plan carries as risks:

- **Life direction** leans hardest on the safety screen. The out-of-scope list and its clinician
  review (item 10) should start now, not when 2b starts.
- **Fitness** is where Meta is most likely to class the domain as health and wellness, and where
  programs at $2,000 a month or more are thinnest. Coach supply is the first thing to test
  (section 10).

---

## 3. Scope

**In**

- The system contract, registry, on/off switch and scaffold command.
- Migration 2: markets, settings, need-state detail, the vetting standard as data, vetting evidence,
  console users and sessions.
- Console core and its settings screens.
- The coaches system.
- The ten-job load check and worker concurrency.
- A bootstrap command that sets up an empty install from a business profile file.

**Out, and where it goes**

| Item | Goes to |
| --- | --- |
| Recruiting research (page to prospect record) | A later plug-in system, unscheduled |
| Outreach drafts and the outreach template (item 5) | A later plug-in system, after `send-gate` exists (2a) |
| `send-gate`, Close integration | 2a, as planned |
| Wiring a cloud model into `cloud_llm` | With the first system that needs one |
| Phase 1 test "outreach never sends without an approval record" | Moves with outreach |
| Shared multi-tenant database | Not planned |

---

## 4. The modular base

### 4.1 What a system is

The business plan already names eleven systems. Each becomes a folder with the same shape, and the
core finds it by reading its manifest.

| Part | Where | Needed when |
| --- | --- | --- |
| Manifest | `systems/<key>/manifest.py` | always |
| Job handlers | `systems/<key>/jobs.py` | it runs jobs |
| Console screens | `systems/<key>/console.py`, `systems/<key>/templates/` | it has screens |
| Ledger writes | `systems/<key>/service.py` | it changes the Ledger |
| Prompts | `prompts/<key>/<name>.vN.md` (unchanged location) | it calls a model |
| Eval cases and scorer | `evals/synthetic/<key>/cases.jsonl`, `systems/<key>/evals.py` | it calls a model |
| n8n workflows | `n8n/workflows/<key>--<name>.json` | it has always-on steps |
| Schema | `db/migrations/<timestamp>_<key>_<what>.sql` | it needs tables |
| Tests | `tests/systems/<key>/` | always |

The manifest is data only. It declares the system's key and title, its job types and the handler
for each, the prompts and versions it uses, the model profile for each use, its data class, its
console section, its settings with defaults, and its n8n workflows. Handlers are named as strings
and imported on first use, which keeps the property the import-boundary test relies on today.

Three things make the contract real:

- **`scripts/new_system.py <key>`** creates the folder, a prompt folder, an eval folder, a test
  folder and a migration stub.
- **`tests/unit/test_system_contract.py`** loads every manifest and fails if a job type has no
  handler, a prompt file is missing, a model-calling system has no eval cases, or a system whose
  data class is prospect data names a cloud profile.
- **The registry** replaces three hand-kept lists: `LAZY_HANDLERS` in `worker/jobs/__init__.py`,
  `SYSTEMS` in `evals/run.py`, and the console's routes.

**On and off.** Each system has a setting, `system.<key>.enabled`, that a founder changes in the
console. Turning a system off removes its screens, stops the worker claiming its job types and
cancels its pending jobs with an event. Nothing turns a system on by itself.

### 4.2 What stays in the core and cannot be switched off

Modular does not make the guardrails optional. These stay in the core and in the database, and no
manifest can disable them:

- the event trigger on every table and the required actor
- founder-only actions (vetting, approvals, activating a coach, changing rules)
- the cloud boundary (`worker/cloud_llm.py`, its allowlist and the import test)
- the single send gate and the safety hold
- log and Slack scrubbing

A system gets these by using the core's helpers. The contract test checks that a system's code
does not open its own database connection, HTTP client or log handler.

### 4.3 Rules as data

| Rule | Table | Changed by | Console screen in Phase 1 |
| --- | --- | --- | --- |
| Need-states and what each covers | `need_states`, `market_need_states` | founder | yes |
| Vetting standard | `vetting_standards`, `vetting_item_types` | founder; versioned, fixed once active | yes |
| Thresholds and limits | `settings` | founder | yes |
| Markets | `markets` | founder | yes |
| System on/off | `settings` | founder | yes |
| Message templates | `templates` (exists) | founder | no; arrives with `send-gate` in 2a |

Every change is an event with the founder's name on it, like any other Ledger write.

**Need-states.** Adding one is a row in the console: key, label, what it covers, what it does not.
It then appears in the coach form and the readiness view. A test fails if a need-state key appears
as a literal in code, so later prompts and filters must read the list from the Ledger.

**Vetting standard.** A standard is a numbered version with a list of item types. Once a founder
activates it, its items cannot change; a change means a new version. A coach is vetted under one
version and stays under it, so tightening the standard later does not silently un-vet anyone. The
six-item default is loaded as a draft, version 1. Until a founder activates a standard, no coach can
be marked active. That activation is how item 2 gets decided, and it is recorded.

**Thresholds.** The stale-job alert reads its hours from `settings` and the n8n workflow stops
carrying the number. That closes the Phase 0 placeholder for item 24 once you confirm the value.

### 4.4 Models per system

Each manifest names a model profile for each thing it asks a model to do. Profiles (endpoint, model
name, thinking mode, local or cloud) stay in `worker/config.py` and the environment, as `CLAUDE.md`
requires. Phase 1 has one profile, the current local model. A second local model or a cloud model
later is a new profile plus one line in a manifest. Prompt versions move from the single dictionary
in `worker/config.py` into each manifest, so a system carries its own.

A system's data class decides what it may bind to. Prospect and call data can bind only to local
profiles. This is checked by the contract test and again when the worker starts, in addition to
the existing import test.

### 4.5 Cities and other businesses

**Cities.** A `markets` table with one row, Denver. `coaches`, `leads` and `ads` get a required
`market_key`. Need-state launch becomes per market, so a second city can launch different
need-states. The readiness view and every later report group by market.

**Other businesses.** One install per business. To make that cheap:

- A bootstrap command reads a business profile file (name, time zone, markets, founders,
  need-states) and seeds an empty database.
- A test fails if the business name, a founder's name or a city appears as a string in `worker/`,
  `review/` or `systems/`. Those come from the Ledger.
- CI runs the core test suite a second time against an install bootstrapped from a made-up
  business, to prove nothing depends on Chosen Local's own data.

One wart to accept: migration 1 seeds the actors `sam` and `jack`, and an applied migration cannot
be edited. On another business's install the bootstrap deactivates them and creates that business's
founders.

### 4.6 What this does not give you

- A system that needs tables still adds a migration. There is one ordered migration history.
- Database guards (who may activate a coach, what makes a message sendable) are core, so a system
  that needs a new guard changes the core.
- Systems are folders in this repo, not separately installed packages. That can come later if a
  second business needs a different set.

---

## 5. Console core

**Stack (proposed).** FastAPI, Jinja2 templates, uvicorn, server-rendered pages with htmx served
from the app itself (no CDN, no JavaScript build). New dependencies: `fastapi`, `uvicorn`,
`jinja2`, `python-multipart` (uploads), `argon2-cffi` (password hashing). The console keeps binding
to 127.0.0.1:8090 and Tailscale keeps publishing it.

**Sign-in.**

1. Tailscale Serve adds a `Tailscale-User-Login` header to every request it proxies, and strips
   any copy a client sends ([Tailscale docs](https://tailscale.com/kb/1312/serve), checked
   Oct 13). The console matches it to a founder.
2. That founder enters his password. Passwords are stored as argon2id hashes.
3. The console issues a session cookie (HttpOnly, Secure, SameSite=Strict). Sessions are rows in
   the Ledger, so they can be listed and revoked.
4. Every write runs with `app.actor` set to that founder, so the database's founder-only rules and
   the event trigger apply to console actions exactly as they do everywhere else.

Requests with no header are refused, apart from `/healthz`. Failed sign-ins lock the account for a
period and alert Slack with the actor ID only. Forms carry a CSRF token.

**One wrinkle found while checking the docs.** Tailscale does not send identity headers for
traffic from tagged devices, and the workstation is tagged. So Tailscale cannot identify you when
you browse the console from the workstation itself. See item 2 in section 11.

**What the core gives every system.** The page layout and navigation, the signed-in founder, a
transaction helper that sets the actor, form and CSRF handling, the event timeline for any record
(the audit trail on screen), and the evidence store.

**Core screens.** Home (queue health, worker heartbeat, systems that are on, items waiting on a
founder), Systems (on/off, prompts and models in use), Settings (need-states, markets, vetting
standard, thresholds), Account (password, sessions).

**Privacy.** No per-request logging, `Cache-Control: no-store` on every page, and the log-scrubbing
test is extended to the console with synthetic coach names and emails.

---

## 6. Coaches system

The first real system, and the proof that the contract works. It calls no model.

- **Roster.** List with filters for status, need-state and market. Coach page with profile,
  need-states, price per month, program length, open capacity and the date it was confirmed,
  area, remote or not, source and notes.
- **Adding coaches.** A form, and a CSV import with a preview step that shows which rows are valid
  before anything is written. Imported coaches arrive as `prospect`.
- **Vetting file.** One checklist per coach, built from the active standard. For each item: the
  evidence, who verified it and when. Only the signed-in founder can verify, and the database
  already refuses anyone else.
- **Evidence.** Uploaded in the console and written to
  `$PRIVATE_DATA_DIR/coaches/<coach id>/<item id>/`. The Ledger stores the relative path, the
  SHA-256 hash and the size, never the content or the file name in events. Files are served only
  through the signed-in console. The existing encrypted backup already covers this folder.
- **Status flow.** prospect → vetting → active → paused or removed. Activation is refused unless
  every required item in the coach's standard is verified by a founder.
- **Readiness view.** For each market and launch need-state: active coaches with open capacity
  against the two required, who is in vetting, and what each is missing. This is the Phase 1 gate
  on one screen.

Capacity is entered by hand in Phase 1. The weekly capacity check stays in "after the first loop".

---

## 7. Schema changes (migration 2)

Forward-only, in one new migration. Every new table gets `created_at`, `updated_at` and the event
trigger unless noted.

| Change | Detail |
| --- | --- |
| `markets` (new) | key, name, time zone, active. Seeded with Denver by the bootstrap. |
| `market_need_states` (new) | market, need-state, launched_at, active. Replaces `need_states.launched_at`. |
| `need_states` | add description, covers, not_covered, sort order. |
| `settings` (new) | key, value (jsonb), type, description. Founder-only writes. |
| `vetting_standards` (new) | version, status (draft, active, retired), activated_by, activated_at. One active at a time. |
| `vetting_item_types` (new) | standard, key, label, description, evidence kind, required, sort order. Fixed once the standard is active. |
| `vetting_items` | reference an item type; the fixed list in the CHECK constraint goes. |
| `vetting_evidence` (new) | item, kind (file or note), relative path, hash, size, uploaded_by, removed_at. |
| `coaches` | add `market_key` (required), `vetting_standard_id`, postal code, notes. |
| `leads`, `ads` | add `market_key` (required). |
| `coaches_status_guard` | reads the required items from the coach's standard instead of a fixed list. |
| `console_users` (new) | founder actor, Tailscale login, password hash, lockout state. Sensitive columns never appear in events. |
| `console_sessions` (new) | token hash, founder, expiry, revoked_at. No row-event trigger, following `worker_heartbeats`; sign-in, sign-out and lockout are written as events instead. |
| `api.queue_health` | reads the threshold from `settings`. The stale-job workflow is re-exported without the number. |

The Phase 1 acceptance tests that the schema already satisfies keep passing: the vetting and
activation guards are rewritten to read data, not removed.

---

## 8. Build order

Five sessions, in order. Each ends with its tests green and a commit. Kickoff prompts follow the
build plan's pattern.

**1a. System contract and registry.** The `systems/` package, the manifest type, the registry,
healthcheck moved to `systems/healthcheck/`, the contract test, the scaffold command,
`docs/systems.md` ("how to add a system"). `systems` joins the folders the import-boundary test
walks. No behaviour changes.

> Read CLAUDE.md and sections 4 and 8 of docs/phase-1-design.md. Plan 1a: the system contract, the
> registry and the healthcheck move. Every Phase 0 test must still pass unchanged in meaning. Do not
> write code until I approve.

**1b. Migration 2 and bootstrap.** Everything in section 7, the business profile file and the
bootstrap command, database tests, and the second CI run against a made-up business.

> Read sections 4.3, 4.5 and 7 of docs/phase-1-design.md and .claude/rules/n8n-and-db.md. Check
> docs/decisions.md for items 1, 2 and 24. Plan 1b as a column-level table list before writing SQL.

**1c. Console core.** The app, sign-in, sessions, layout, core screens, the deploy changes
(dependencies, service unit), runbook entries for creating a founder login and resetting a
password.

> Read section 5 of docs/phase-1-design.md. Read Tailscale's Serve docs and FastAPI's current docs
> first and record what you confirm in docs/integrations.md. Check decisions item 23. Plan 1c.

**1d. Coaches system.** Built with the scaffold command from 1a, as its first real use.

> Read section 6 of docs/phase-1-design.md and section 9 of the business plan. Create the system
> with scripts/new_system.py. List anything the contract made awkward before working around it.

**1e. Load check and close-out.** Worker concurrency, the ten-job run on the workstation with
timings recorded in `docs/integrations.md`, the full acceptance run, and the document updates in
section 12.

> Read sections 9 and 12 of docs/phase-1-design.md. Plan the worker concurrency change and the
> load check, then give me the numbered steps to run the acceptance tests on the workstation.

---

## 9. Acceptance tests

**Kept from the build plan**

- A coach moves prospect → vetting → active only with founder actions recorded in `events`.
- A coach with a missing required vetting item cannot be marked active.
- Ten parallel jobs complete without errors on the workstation.

**Added for the base**

- A new system created with the scaffold command gets a job the worker runs, a console page in the
  navigation and an eval suite, with no edit to any file under `worker/` or `review/`.
- Turning a system off removes its screens, stops its jobs being claimed and cancels its pending
  jobs, each with an event.
- A need-state added in the console appears in the coach form and the readiness view with no
  migration and no code change.
- A new vetting standard version leaves coaches vetted under the old version untouched.
- No coach can be activated while no standard is active.
- A second market added in a test keeps its coaches and readiness separate from Denver's.
- An install bootstrapped from a made-up business profile passes the core test suite.

**Added for the console**

- No Tailscale header: refused. Unknown login: refused. Wrong password: refused, then locked.
- Every console write carries the signed-in founder as the actor in `events`.
- A system actor cannot verify a vetting item or activate a coach, even through the console's own
  database role.
- No synthetic coach name, email or evidence file name appears in logs, Slack payloads or events.
- The console answers only on loopback (the Phase 0 check, kept).

---

## 10. Business track

The build does not wait on these. The gate does.

| # | Work | Owner | Blocks |
| --- | --- | --- | --- |
| B1 | Supply check: can you name at least two Denver coaches per launch need-state with programs of three months or more at $2,000+ a month? | Sam, Jack | Whether the need-state choice holds |
| B2 | Write what each launch need-state covers and does not cover (`docs/inputs/need-states.md`) | Sam, Jack | Need-state seed data, later the safety screen |
| B3 | Decide the vetting standard and activate it in the console (item 2) | Sam, Jack | Vetting any coach |
| B4 | Decide partner terms and commission terms (items 3, 4) and send them to the lawyer | Sam, Jack, lawyer | Any coach signing, so the gate |
| B5 | Start the out-of-scope list and find the reviewing clinician (item 10) | Sam, Jack | 2b, and safe ads for life direction |
| B6 | Sam's Tailscale login and the devices each founder uses (item 23) | Jack, Sam | Sam using the console |
| B7 | Recruit and vet: interviews, references, evidence, agreements | Sam, Jack | The gate |

**B1 comes first.** It costs nothing but your time, and it is the cheapest point to learn whether
fitness coaches at this price are scarce in Denver. If they are, the choice is between a
different second need-state and a lower price floor, and both are easier to change now than after
the funnel is built around them.

**B4 is the critical path.** A signed agreement is a required item in the default standard, so no
coach can be active until the terms exist and the lawyer has seen them. Legal review is the step
with the least control over timing. Proposed decide-by date for items 3 and 4: **Friday, Oct 23**.
Change it if that is wrong, but give it a date.

---

## 11. Still needs your call

Each has a proposed default. None is assumed until you confirm it.

| # | Question | Proposed default |
| --- | --- | --- |
| 1 | Decide-by date for items 3 and 4 | Friday, Oct 23 |
| 2 | Signing in from the workstation itself, where Tailscale cannot identify you | Allow it for one named founder (you), password only, set in `worker.env`. Without it you can only use the console from another device, which makes large uploads slow. |
| 3 | Keep the ten-job load check in Phase 1 now that no Phase 1 system calls a model | Keep it. It proves the reference system end to end before 2b to 2e depend on the server. |
| 4 | Session and lockout policy | Sessions end after 12 hours idle. Five wrong passwords lock the account for 15 minutes. |
| 5 | Stale-job alert threshold (item 24) | 4 hours, now stored in `settings`. |
| 6 | Roster minimums from plan section 9 ($2,000 a month, three months) | Store them as settings and warn on the coach page. Do not block activation; a founder decides. |
| 7 | Whether coaches must be male (open in plan section 2) | Not captured until you decide. Adding the field later is small. |
| 8 | How long to keep a removed coach's evidence | Keep until you set a period, and add the question to the lawyer's list with item 21. |
| 9 | Coach geography, ahead of the matching rule (item 19) | Capture postal code and a free-text area now, so any later distance rule has data. |
| 10 | The console stack in section 5 | As proposed. |

---

## 12. Changes to the fixed documents

`CLAUDE.md` says the architecture changes only with your agreement. These are the edits this plan
needs, made in session 1e (or earlier where a session depends on them):

- **`CLAUDE.md`**: add `systems/` to the layout and the conventions; state that prompt versions
  live in manifests; state the system contract in three lines and point to `docs/systems.md`.
- **`.claude/rules/worker.md`**: extend its paths to `systems/**`; name `systems.<key>.jobs`
  modules as the form cloud allowlist entries take.
- **`.claude/rules/`**: a new `systems.md` with the contract rules from section 4.
- **`docs/build-plan.md`**: replace the Phase 1 section with this scope; move the outreach test;
  add research and outreach to a "later systems" list; note that 2c builds on the Phase 1 console.
- **`docs/decisions.md`**: item 1 done once B2 is written; item 2 becomes "activate a standard in
  the console"; item 5 moves to the outreach system; item 23 is needed by Phase 1; item 24 done on
  your confirmation; new rows for section 11 items 2, 4, 6, 7 and 8.
- **`docs/runbook.md`**: console start, stop and health; creating a founder login; resetting a
  password; restoring evidence files; turning a system off.

`docs/decisions.md` says you update it, so I have not touched it.

---

## 13. Risks

| Risk | How the plan handles it |
| --- | --- |
| The base is built ahead of the proof, the risk the business plan names itself. | The base is deliberately thin: a manifest, a registry, a settings table and a scaffold. No package system, no shared tenancy, no model-routing screen. Session 1d is the test: if the coaches system fights the contract, fix the contract then. |
| The contract is designed around one system and fits the next one badly. | Healthcheck (a model job, no screens) and coaches (screens, no model) are opposite shapes, so the contract is exercised from both ends before 2a. |
| Fitness coach supply at the price floor is thin. | B1 runs first, before any funnel work depends on the need-state. |
| Life direction draws leads with clinical needs. | B5 starts now. Nothing reaches a prospect before 2b's safety screen exists. |
| Legal review stalls the gate. | B4 has a date, and Phase 2 build work does not wait on it. |
| Console sign-in is weaker on the workstation than elsewhere. | Section 11 item 2 makes it an explicit choice, limited to one founder. |
| Evidence files hold third parties' details (a coach's clients). | Local store, hash in the Ledger, served only to a signed-in founder, covered by the encrypted backup, retention on the lawyer's list. |

---

## 14. Not built in Phase 1

Recruiting research, outreach drafts, `send-gate`, the Close integration, any cloud model call, a
coach-facing form or portal, automatic capacity checks, a screen for editing message templates,
recorded eval scores gating a model switch, and shared multi-tenancy.
