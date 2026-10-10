# Decisions and inputs register

Opus reads this file before each phase and stops when a needed item is still OPEN.
Jack updates it. Put the content itself in `docs/inputs/` (or where listed) and mark the row DONE.

| # | Item | Needed by | Status | Where |
| --- | --- | --- | --- | --- |
| 1 | Two launch need-states | Phase 1 | OPEN | `docs/inputs/need-states.md` |
| 2 | Vetting standard (the five items, or your changes) | Phase 1 | OPEN | plan section 9 |
| 3 | Partner agreement terms: response times, reporting duty | Phase 1 | OPEN | plan section 9 |
| 4 | Commission terms: when earned, invoice terms, attribution window, refunds | Phase 1 (agreement), Phase 4 (code) | OPEN | plan section 9 |
| 5 | Coach outreach template | Phase 1 | OPEN | `templates/` |
| 6 | Brand basics: name, domain, logo, colors, headline | Phase 2a | OPEN | `docs/inputs/brand.md` |
| 7 | Intake questions and answer options, including income bands | Phase 2a | OPEN | `docs/inputs/intake-fields.md` |
| 8 | Consent wording: calls, texts, recording disclosure (lawyer-reviewed) | Phase 2a | OPEN | `docs/inputs/consent.md` |
| 9 | Prospect message templates: booking link, reminders, no-show | Phase 2a | OPEN | `templates/` |
| 10 | Out-of-scope list and escalation script (clinician-reviewed) | Phase 2b | OPEN | `docs/inputs/out-of-scope.md` |
| 11 | Call extraction fields and the readiness scale | Phase 2c | OPEN | `docs/inputs/call-fields.md` |
| 12 | Five mock call recordings with answer keys | Phase 2c | OPEN | `evals/synthetic/calls/` (audio is git-ignored) |
| 13 | What a prospect agrees to share with a matched coach | Phase 2e | OPEN | `docs/inputs/consent.md` |
| 14 | Meta ad policy text for the copy check | Phase 2f | OPEN | `docs/inputs/meta-policy.md` |
| 15 | Cost-per-lead limit that triggers an automatic ad pause | Phase 3 | OPEN | this file |
| 16 | Retention period for recordings and transcripts | Phase 2c | OPEN | this file |
| 17 | Hand-corrected reference transcripts, with speaker turns, for at least 3 mock calls | Phase 2c | OPEN | `evals/synthetic/calls/` |
| 18 | Coach-facing templates: handoff, fit-call reminders, outcome check-ins, payment follow-ups | Phase 2e | OPEN | `templates/` |
| 19 | Geography rule for matching (e.g. maximum distance or neighborhoods) | Phase 2d | OPEN | this file |
| 20 | Definition of a qualified lead, and the key used to merge duplicate submissions | Phase 2a | OPEN | this file |
| 21 | Privacy policy (lawyer-reviewed) | Phase 2a | OPEN | `docs/inputs/privacy-policy.md` |
| 22 | Customer check-in schedule after the fit call | Phase 4 | OPEN | this file |
| 23 | Review console users: which founder devices are on Tailscale | Phase 2c | OPEN | this file |
| 24 | Alert threshold for the oldest pending job (hours) | Phase 0 | OPEN | this file |
