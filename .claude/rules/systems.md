---
paths: systems/**, review/**, scripts/new_system.py
---

# System rules

- A system is `systems/<key>/` with `manifest.py` defining `MANIFEST`. Manifests are data only:
  they import nothing but `systems._contract`. Handlers, console routes and eval scorers are named
  as `"systems.<key>.<module>:<attr>"` strings and imported on first use.
- Job types are `<key>` or `<key>.<name>`; settings are `<key>.<name>`; n8n workflows are
  `n8n/workflows/<key>--<name>.json`; templates live in `systems/<key>/templates/<key>/`.
- A system never opens its own database connection, HTTP client or log handler. It uses the
  caller's connection (`review.web.Page.write()` in the console, the job's `writes` in the worker),
  so the actor, the event trigger and log scrubbing always apply.
- `data_class` is honest: anything from intake, calls, transcripts or extractions is `prospect`;
  the roster and vetting files are `coach`. Both use local model profiles only and never appear on
  `cloud_llm.ALLOWED_CALLERS`.
- A system that calls a model ships eval cases in `evals/synthetic/<key>/` and a scorer.
- New systems arrive turned off; nothing in code turns a system on. Mark a system `required` only
  when the business must never run without it (the safety screen).
- Business data (the business name, founders, markets, need-state keys) comes from the Ledger,
  never from string literals.
- `uv run python scripts/check_systems.py` must pass before a system is reported done.
