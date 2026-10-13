# Phase 1 plan: coach supply on a modular base

Oct 13, 2026 · v2, revised before the build · built the same day (see "As built")

Phase 1 builds the review console's core, the coach roster and vetting file, and a base that later
AI systems plug into without touching the core. Recruiting research and outreach are deferred and
return later as plug-in systems.

**Gate (unchanged):** two coaches per launch need-state, each with a complete vetting file and a
signed agreement. The build can finish before the gate does; Phase 2 starts once the build's
acceptance tests pass, while recruiting continues.

---

## As built (Oct 13)

Built in five commits on `claude/phase-1` (1a to 1e), 170 tests passing, CI green. Where the
build differs from the sections below, this list wins:

- **Bootstrap** is `python3 -m worker.bootstrap <profile>`, not `systems.bootstrap`. It prints SQL
  and uses the standard library only, so it runs on the VPS checkout and is piped into psql as
  the owner (`deploy/PHASE1.md` V4).
- **Vetting-standard screens** live in the coaches system (`/s/coaches/standards`), not in the
  core Settings screen. The rules stay in the database (core).
- **Unknown job types still fail loudly.** Only jobs of a system that is turned off wait; a job
  type no system declares is claimed and fails with `UnknownJobType`, so a typo in an n8n
  workflow alerts at once rather than after the stale-job threshold.
- **One active market fills itself in.** With exactly one active market, `coaches`, `leads` and
  `ads` may leave `market_key` out; with more, the Ledger requires it.
- **Verifying an item needs evidence** of the kind its standard asks for (file, note or either).
  The console shows "needs a file" until there is one. This is a workflow rule in the coaches
  system, not a database rule.
- **An active coach's file cannot be un-verified**; pause the coach first.
- **The console runs as its own Linux user, `clconsole`** (decision 14, Oct 13, after the
  review). Its database login, `cl_console` (created by `deploy/postgres/create_console_login.sh`;
  rerunning `create_logins.sh` would rotate every login), lives in the root-only
  `/etc/chosen-local/console.env`, which systemd reads before dropping privileges. The worker
  can read neither the file nor the console's process environment. `clworker` and `clconsole`
  share only `cl-private`, through the `clprivate` group. `REVIEW_CONSOLE_SECRET` is gone;
  sessions live in the Ledger.
- **Independent review** (a separate agent, after 1e) found eight holes, now closed with a
  regression test each in `tests/db/test_review_findings.py` and alongside: the worker could
  read the console's login; the worker could move verified vetting items between coaches;
  status changes were not founder-only (and a never-active coach could return under a retired
  standard); toggling `required` turned a system on; coach names went into event payloads;
  parallel wrong passwords were under-counted; readiness listed missing items once per
  need-state; a bootstrap rerun restored draft items a founder had deleted. Also fixed: the
  contract checker missed aliased database connections; console tests now run through a real
  console login; a SIGTERM before a slot starts is no longer lost.
- **A re-check of those fixes** by the same reviewer found three more, now closed with tests:
  the worker could still set `required` directly (and re-assign a job type to another system);
  a founder could move an active coach to another standard in raw SQL; and during a lockout a
  right and a wrong password got different answers. The re-check also showed that hiding
  `console.env` was not enough while both services ran as `clworker` (the worker could read the
  console's process environment), which led to decision 14.
- **`uv.lock` was not regenerated** (no package index where it was built). Run `uv lock` before
  deploying (`deploy/PHASE1.md` L2). CI resolves fresh each run, so it is unaffected.
- **Local verification** ran on Python 3.13 with ruff 0.16.8 and a real Postgres 16.15; CI runs
  the locked Python 3.12 toolchain and passed on every chunk.
- **Not verified here:** anything on the workstation (the load check, Tailscale Serve in front of
  the console, systemd restarts). Those are `deploy/PHASE1.md` section A.

---

## 1. What changed from v1

| # | v1 | v2 | Why |
| --- | --- | --- | --- |
| 1 | FastAPI, htmx, argon2-cffi | Starlette, Jinja2, plain HTML forms, stdlib `hashlib.scrypt` | Fewer dependencies. Starlette is what FastAPI is built on and is enough for a forms app. scrypt is a memory-hard password hash that ships with Python. |
| 2 | "A system actor cannot verify a vetting item, even through the console's role" (not enforceable as written) | The database ties each login to the actors it may claim. The worker login can only act as a system actor; only the console login can act as a founder. | In v1 any login could claim to be Sam. Now a compromised worker job cannot impersonate a founder. |
| 3 | Turning a system off cancels its pending jobs | Off pauses: jobs wait and resume when it is turned back on. Systems marked required (the safety screen, later) cannot be turned off. New systems arrive off until a founder turns them on. | Cancelling loses work; an always-required safety screen should never be switchable. |
| 4 | On/off held in `settings` | A `systems` table in the Ledger, filled from the code's manifests when the worker or console starts | The database needs to know which job types belong to which system so the stale-job alert ignores paused systems. |
| 5 | Worker claims any job type | Worker claims only job types of systems that are on and that it has handlers for | Fixes jobs failing as "unknown type" when a handler is not deployed yet. |
| 6 | Second CI run against a made-up business | One smoke test that bootstraps a made-up business and runs the core flow | Same proof, a fraction of the CI time. |
| 7 | Separate tests for business names and need-state keys in code | One test, driven by the business profile file | One place to maintain. |
| 8 | Vetting could not start until a standard was active | Coaches can be vetted under the draft standard; activating a coach requires its standard to be active | Lets vetting work start while you and Sam settle the standard. |
| 9 | Build order put worker changes in 1e | The worker's registry and claim filter move into 1b, with the schema they need | Each session ends in a working state. |

---

## 2. Decisions on record (Oct 13)

| # | Question | Decision |
| --- | --- | --- |
| 1 | Review console | Build the core now; 2c adds call screens. |
| 2 | Recruiting research | Deferred to a later plug-in system. |
| 3 | Coach outreach | Deferred. Founders recruit outside the system and add coaches to the roster. |
| 4 | What must stay changeable | Plug-in systems, rules as data, models per system, cities, other businesses. |
| 5 | Other businesses | A market (city) key now; one install per business, no shared multi-tenant database. |
| 6 | Launch need-states (item 1) | Fitness and performance; life direction and discipline. Adding one later needs no code. |
| 7 | Vetting standard (item 2) | Not decided. Editable data; the plan's six items loaded as a draft. |
| 8 | Console sign-in | Tailscale identity plus a per-founder password. |
| 9 | Vetting evidence | Workstation private store, uploaded through the console. |
| 10 | Partner and commission terms (items 3, 4) | Open; decide by **Friday, Oct 16** so the lawyer can start. They block the gate, not the build. |
| 11 | Local sign-in at the workstation | Allowed for Jack only, password alone. |
| 12 | Roster minimums | None. Coaches at a wide range of prices and in many specialties are welcome; the two launch need-states are where marketing goes first. |
| 13 | Stale-job alert threshold (item 24) | 4 hours, stored as a console setting. |
| 14 | Console's Linux user (after the review) | Its own user, `clconsole`, sharing only `cl-private` with `clworker` through the `clprivate` group. |

Decision 12 means a coach may cover any active need-state, not just the launched ones.
"Launched" is a marketing flag per market: it decides what the readiness view counts toward the
gate and, from 2a, which niches the ads and landing page serve.

Risks from decisions 6 and 12: **life direction** leans hardest on the safety screen, so start the
out-of-scope list and clinician review (item 10) now; **fitness** is most likely to be classed as
health and wellness by Meta and to have few programs at $2,000+ a month, so check coach supply first
(section 9, B1); **lower-priced coaches** earn smaller commissions, since the commission is 100% of
month one. A $500-a-month coach returns $500 against a $200–$500 acquisition cost, so the funnel
report should show commission by price band from Phase 3.

---

## 3. The modular base

### 3.1 A system is a folder

```
systems/<key>/
  manifest.py      data only: key, title, job types → handler names, prompts and versions,
                   model profile per use, data class, settings with defaults, console section,
                   n8n workflows, required (cannot be turned off)
  jobs.py          job handlers (if it runs jobs)
  service.py       its Ledger writes, using the core's transaction helper
  console.py       its screens (if it has any); templates in systems/<key>/templates/
  evals.py         its eval scorer (if it calls a model)
prompts/<key>/<name>.vN.md          unchanged location
evals/synthetic/<key>/cases.jsonl   unchanged location
tests/systems/<key>/
db/migrations/<timestamp>_<key>_<what>.sql   if it needs tables
n8n/workflows/<key>--<name>.json             if it has always-on steps
```

- **Registry** (`systems/__init__.py`) discovers manifests by package name and imports handlers
  only on first use, so the import-boundary test keeps working. It replaces `LAZY_HANDLERS` in
  `worker/jobs/__init__.py`, `SYSTEMS` in `evals/run.py`, and `prompt_versions` in `worker/config.py`.
- **`scripts/new_system.py <key>`** scaffolds every folder above.
- **`tests/unit/test_system_contract.py`** fails if a job type has no handler, a prompt file or eval
  suite is missing, a prospect-data system names a cloud profile or sits on the cloud allowlist, or
  a system opens its own database connection, HTTP client or log handler.

### 3.2 On and off

The `systems` table holds each system's key, title, version, `required` and `enabled`. The worker
and console upsert it from the manifests at start-up; new systems arrive with `enabled = false`
unless they are required. Only a founder can change `enabled`, and the database refuses to turn off
a required system. The worker claims only job types of enabled systems. Jobs for a paused system
wait in the queue, and `api.queue_health` leaves them out of the stale-job alert.

### 3.3 What the core keeps

No manifest can switch off: the event trigger and required actor, founder-only actions, the cloud
boundary, the single send gate and safety hold, and log and Slack scrubbing. Systems get these
by using the core's helpers.

### 3.4 Rules as data

| Rule | Where | Edited in the console in Phase 1 |
| --- | --- | --- |
| Need-states, per market | `need_states`, `market_need_states` | yes |
| Vetting standard | `vetting_standards`, `vetting_item_types` | yes |
| Markets | `markets` | yes |
| Thresholds and limits | `settings` (definitions in code, values in the Ledger) | yes |
| System on/off | `systems` | yes |
| Message templates | `templates` | no; arrives with `send-gate` (2a) |

All writes are founder-only and every change is an event.

**Vetting standard.** Numbered versions; a draft is editable, an active one is fixed. A coach's
vetting file is built from one version and stays on it until a founder moves it (items carry over
by key, with an event). A coach can be vetted under a draft, but becoming active requires every
required item of an **active** standard to be verified by a founder. The six-item default is
loaded as draft version 1. Activating it in the console is how item 2 gets decided.

### 3.5 Models per system

Manifests name a model profile per use; profiles (endpoint, model, thinking, local or cloud) stay in
`worker/config.py` and the environment. Phase 1 has one profile, the current local model. A system's
data class decides which profiles it may use; prospect and call data may use local profiles only,
checked by the contract test and again at worker start.

### 3.6 Cities and other businesses

- `markets` table; `coaches`, `leads` and `ads` get a required `market_key`. Need-state launch is
  per market. The readiness view and later reports group by market.
- **One install per business.** `python3 -m worker.bootstrap <profile.toml>` seeds an empty Ledger
  from a business profile: name, markets, founders, need-states, the draft vetting standard.
  `deploy/business/chosen-local.toml` is Chosen Local's. It is idempotent, so it also runs on the
  existing production Ledger.
- A test fails if the business name, a founder name, a market or a need-state key from the profile
  appears as a string in `worker/`, `review/` or `systems/`.
- A smoke test bootstraps a made-up business and runs the core flow (standard, coach, vetting,
  activation, readiness).
- Migration 1 seeds actors `sam` and `jack` and cannot be edited; another business's bootstrap
  deactivates them and creates its own founders.

---

## 4. Database changes (migration 2)

| Change | Detail |
| --- | --- |
| Login → actor guard | `ledger_current_actor_id()` also checks the login: worker logins may claim system actors only; n8n logins only `n8n`; console logins founders and `review_console`; owners and superusers unrestricted. Postgres counts superusers as members of every role, so superusers are checked first (verified). |
| `ledger_console` login | `create_logins.sh` adds `cl_console`; the console reads `CONSOLE_DATABASE_URL`. |
| `systems`, `system_job_types` | as in 3.2; `enabled` changes founder-only; required systems cannot be disabled. |
| `settings` | key, value (jsonb). Founder-only writes. |
| `markets`, `market_need_states` | as in 3.6. `need_states.launched_at` moves to `market_need_states`. |
| `need_states` | adds description, covers, not_covered, sort order. |
| `vetting_standards`, `vetting_item_types` | as in 3.4. One active standard at a time; types fixed once active. |
| `vetting_items` | adds `item_type_id`; the fixed CHECK list goes; the type must belong to the coach's standard. |
| `vetting_evidence` | item, kind (file or note), relative path, SHA-256, size, uploaded_by, removed_at. Path and notes never appear in events. |
| `coaches` | adds `market_key`, `vetting_standard_id`, postal code, notes. |
| `leads`, `ads` | add `market_key`. The migration fails loudly if either table already has rows (both are empty after Phase 0). |
| `coaches_status_guard` | reads required items from the coach's standard and requires that standard to be active. |
| `console_users`, `console_sessions` | founder, Tailscale login, scrypt hash, lockout; sessions by token hash with expiry and revocation. Sessions get no row-event trigger (like `worker_heartbeats`); sign-in, sign-out and lockout are written as events. |
| `api.queue_health` | threshold and repeat interval from `settings`; jobs of paused systems are left out. The stale-job workflow drops its hard-coded 4. |
| Grants | explicit grants on every new table for the worker, console and reporting roles. |

---

## 5. Console core

- **Stack:** Starlette, Jinja2 (autoescaping on), python-multipart, uvicorn. Server-rendered pages
  with plain forms; no JavaScript build, nothing from a CDN. Still binds 127.0.0.1:8090; Tailscale
  on Windows still publishes it.
- **Sign-in:** Tailscale Serve sends `Tailscale-User-Login` and strips any copy a client sends
  ([docs](https://tailscale.com/kb/1312/serve), checked Oct 13). If the header is present it must
  match the founder signing in. The password is always required, hashed with scrypt. Sessions are
  Ledger rows behind an HttpOnly, Secure, SameSite=Strict cookie; forms carry a CSRF token; failed
  sign-ins lock the account and alert Slack with the actor ID only.
- **Local sign-in:** Tailscale sends no identity header for tagged devices, and the workstation is
  tagged. A founder flagged for local sign-in can sign in from the workstation itself with the
  password alone (see section 10, item A).
- **Every write** runs with `app.actor` set to the signed-in founder, so the database's founder rules
  and event trigger apply unchanged.
- **Founder logins** are created with `python -m review.admin create-user`, run by Jack as
  `clworker`; the password is typed at a prompt, never passed on the command line.
- **Core screens:** Home (queue health, worker heartbeat, items waiting on a founder), Systems (on/off,
  prompts and models), Settings (need-states, markets, vetting standard, thresholds), Account
  (password, sessions), and an event timeline on any record.
- **Privacy:** no per-request logs, `Cache-Control: no-store`, the log-scrubbing test extended to
  console traffic with synthetic coach names, emails and file names.

---

## 6. Coaches system

No model. The first real system and the test of the contract.

- **Roster:** filters by status, need-state and market; coach page with profile, need-states,
  price, program length, open capacity and when confirmed, postal code and area, remote or not,
  source, notes.
- **Adding coaches:** a form, and a CSV import with a preview before anything is written. New
  coaches start as `prospect`.
- **Vetting file:** a checklist from the coach's standard; each item shows evidence, who verified it
  and when. Only the signed-in founder can verify.
- **Evidence:** uploaded to `$PRIVATE_DATA_DIR/coaches/<coach id>/<item id>/<random name>`; the
  Ledger keeps the relative path, hash and size. Served only to a signed-in founder. Already covered
  by the encrypted backup. Upload limit 500 MB; allowed types PDF, images, audio, video, text, Word.
- **Status flow:** prospect → vetting → active → paused or removed; activation as in section 3.4.
- **Readiness view:** per market and launched need-state: active coaches with open capacity against
  the required number, who is in vetting and what each is missing. The gate on one screen.
- **No roster minimums** (decision 12). Price and program length are recorded, not policed.

---

## 7. Build order

Each chunk ends with tests green, a commit and a push to `claude/phase-1`.

| Chunk | Contents |
| --- | --- |
| 1a | System contract, registry, healthcheck moved to `systems/healthcheck/`, contract test, scaffold command, `docs/systems.md`. No behaviour change; every Phase 0 test passes. |
| 1b | Migration 2, login guard, bootstrap and profile, worker registry sync and claim filter, stale-job workflow re-export, `create_logins.sh` adds `cl_console`, database tests. |
| 1c | Console core: app, sign-in, sessions, core screens, admin command, service unit and env changes, runbook entries. |
| 1d | Coaches system, created with the scaffold command. |
| 1e | Worker concurrency (N slots, each its own worker ID), `scripts/load_check.py`, document updates (section 11), the acceptance checklist Jack runs on the workstation, pull request. |

---

## 8. Acceptance tests

**From the build plan:** a coach moves prospect → vetting → active only with founder actions in
`events`; a coach with a missing required item cannot be activated; ten parallel jobs complete
without errors on the workstation (Jack runs `scripts/load_check.py`).

**Base:** a scaffolded system gets a running job, a console page and an eval suite with no edit
under `worker/` or `review/`; a system that is off has no screens and its jobs wait, then run when it
is turned on; a required system cannot be turned off; a need-state added in the console appears in
the coach form and readiness view with no code change; a new standard version leaves coaches on the
old one untouched; no coach activates under a draft standard; a second market stays separate; the
made-up business smoke test passes.

**Console and database:** no header and not local-flagged, refused; header for another founder,
refused; wrong password, refused then locked; every console write carries the signed-in founder as
actor; the worker login cannot act as a founder; the n8n login cannot act as anyone but `n8n`; no
synthetic name, email or evidence file name in logs, Slack payloads or events; the console answers
only on loopback.

---

## 9. Business track

| # | Work | Owner | Blocks |
| --- | --- | --- | --- |
| B1 | Supply check: two Denver coaches per launch need-state at $2,000+ a month, three months or more | Sam, Jack | Whether the need-state choice holds |
| B2 | What each launch need-state covers and does not (`docs/inputs/need-states.md`) | Sam, Jack | Need-state detail in the console; later the safety screen |
| B3 | Decide and activate the vetting standard (item 2) | Sam, Jack | Activating any coach |
| B4 | Partner and commission terms decided by Fri Oct 16, then to the lawyer (items 3, 4) | Sam, Jack, lawyer | Signed agreements, so the gate |
| B5 | Out-of-scope list and reviewing clinician (item 10) | Sam, Jack | 2b; safe ads for life direction |
| B6 | Sam's Tailscale login and founder devices (item 23) | Jack, Sam | Sam using the console |
| B7 | Recruit and vet | Sam, Jack | The gate |

---

## 10. Defaults in use

All four open calls were answered on Oct 13 (section 2, decisions 10 to 13). These smaller defaults
apply unless you change them: launch need-state keys `fitness` and `life_direction` with the plan's
labels; sessions end after 12 idle hours; five wrong passwords lock an account for 15 minutes; the
ten-job load check stays; a "male coaches only" field is not captured until you decide; removed
coaches' evidence is kept until you set a period (added to the lawyer's list).

--- | --- | --- |
| A | Signing in from the workstation itself | Allow it for Jack only, password alone |
| B | Roster minimums ($2,000 a month, three months) | Warn on the coach page; a founder may still activate |
| C | Stale-job alert threshold (item 24) | 4 hours, now a console setting |
| D | Decide-by date for partner and commission terms | Friday, Oct 23 |

Defaults used unless you say otherwise: launch need-state keys `fitness` and `life_direction`, with
the plan's labels; sessions end after 12 idle hours; five wrong passwords lock an account for 15
minutes; the ten-job load check stays; a "male coaches only" field is not captured until you decide;
removed coaches' evidence is kept until you set a period (added to the lawyer's list).

---

## 11. Document changes made in 1e

- `CLAUDE.md`: `systems/` in the layout, the system contract in three lines, prompt versions in
  manifests, the login → actor guard.
- `.claude/rules/worker.md`: paths extended to `systems/**`; new `.claude/rules/systems.md`.
- `.claude/rules/prompts.md`: switch versions in the manifest, not `worker/config.py`.
- `docs/build-plan.md`: Phase 1 replaced with this scope; research and outreach listed as later
  systems; 2c builds on the Phase 1 console.
- `docs/runbook.md`: console operations, founder logins, password reset, evidence restore, turning a
  system on or off, running the bootstrap.
- `docs/integrations.md`: Starlette, Tailscale Serve headers, scrypt parameters.
- `docs/decisions.md` is yours to update; section 12 lists the edits it needs.

## 12. Edits `docs/decisions.md` needs

Item 1: two launch need-states chosen (B2's descriptions still to write). Item 2: "activate a
standard in the console". Items 3 and 4: decide by Oct 16. Item 5: moves to the outreach system.
Item 23: needed by Phase 1. Item 24: DONE, 4 hours. New rows: local sign-in (Jack only), no roster
minimums, the male-coaches question, and evidence retention.

---

## 13. Not built in Phase 1

Recruiting research, outreach, `send-gate`, the Close integration, any cloud model call, a
coach-facing portal, automatic capacity checks, a template editor, and shared multi-tenancy.
