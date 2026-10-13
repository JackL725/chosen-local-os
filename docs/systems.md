# Systems: how to add one

Every AI system in the business plan's list (intake, safety screen, call system, matching, …) is
a folder under `systems/`. The core (`worker/`, `review/`, `db/`, `n8n/`) runs them; a new system
plugs in without editing the core.

## Make one

```bash
uv run python scripts/new_system.py lead_notes --model --data-class prospect   # calls a model
uv run python scripts/new_system.py partner_portal --console --tables          # screens and tables
uv run python scripts/check_systems.py && uv run pytest -q
```

Then fill in the manifest, replace the example job, prompt and eval cases, and turn the system on
in the console (Systems). New systems arrive turned off.

## What a system is

```
systems/<key>/
  manifest.py    MANIFEST = Manifest(...): data only, imports nothing but systems._contract
  jobs.py        job handlers: pure functions of (job, context) returning a JobResult
  service.py     Ledger writes, always on the caller's connection and transaction
  console.py     `routes`: its screens (templates in systems/<key>/templates/<key>/)
  evals.py       `score(case, expected, ctx) -> Outcome` for its eval suite
prompts/<key>/<name>.vN.md            versioned prompts (.claude/rules/prompts.md)
evals/synthetic/<key>/cases.jsonl     synthetic eval cases, one JSON object per line
tests/systems/<key>/                  its tests
db/migrations/<timestamp>_<key>_<what>.sql   if it needs tables
n8n/workflows/<key>--<name>.json             if it has always-on steps
```

## The manifest

| Field | Meaning |
| --- | --- |
| `key`, `title`, `description`, `version` | Identity. `key` is the folder name. |
| `data_class` | `none`, `public`, `founder_brief`, `coach` or `prospect`. Coach and prospect data may use local model profiles only and may never be on the cloud allowlist. |
| `required` | Always on; the console cannot turn it off (the safety screen will be). |
| `jobs` | `JobSpec(job_type, handler, model_use=…)`. Job types are `<key>` or `<key>.<name>`. |
| `models` | `ModelUse(name, profile)`. Profiles live in `worker/config.py` (`MODEL_PROFILES`). |
| `prompts` | `PromptSpec(name, version)`: the version in use. Change it only when evals hold. |
| `settings` | `SettingSpec("<key>.<name>", type, default, label)`: values founders edit in the console. |
| `console` | `ConsoleSection(title, "systems.<key>.console:routes")`. |
| `evals` | `"systems.<key>.evals:score"`. Required if the system calls a model. |
| `n8n_workflows` | File stems in `n8n/workflows/`, named `<key>--<name>`. |

## What the core guarantees, and a system cannot switch off

- Every Ledger write names its actor and writes an event (database triggers).
- Founder-only actions stay founder-only (database guards), and each database login can act only
  as the actors it is allowed to (migration 2).
- The cloud boundary: only modules on `worker/cloud_llm.py`'s allowlist reach a cloud model.
- One send gate and the safety hold (2a, 2b).
- Logs, Slack and error records carry IDs only.

`scripts/check_systems.py` (in CI and pre-commit) enforces the contract: namespaced job types and
settings, handlers inside the system's own package, prompts and eval cases present, local-only
models for coach and prospect data, and no system opening its own database connection or log
handler.
