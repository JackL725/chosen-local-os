---
paths: worker/**, evals/**, scripts/**, tests/**, review/**
---

# Worker rules

- Every job handler is a pure function of (job payload, config, clients) plus explicit writes. Inject
  the database, model and Close clients so tests can replace them with fakes.
- `worker/cloud_llm.py` is reachable only from an explicit allowlist of modules (coach research,
  ad-script drafting from founder-written briefs). A test walks every module's imports, including
  transitive ones, and fails if any module outside the allowlist imports `cloud_llm`, or if any
  module other than `cloud_llm` imports a cloud SDK (`anthropic`, `openai` pointed at a non-local
  URL) or calls a non-local model endpoint with `httpx`/`requests`.
- `cloud_llm` refuses every call that does not pass `contains_prospect_data=False` explicitly.
- Paths to private data come only from `PRIVATE_DATA_DIR`. Never hardcode them and never default
  them to a real path. In dev it points at `evals/synthetic/`.
- Log job IDs, durations, token counts and status. Never log transcript text, names, phone numbers
  or emails. A test runs the synthetic suite, captures all log output, and fails if any synthetic
  name, phone number or transcript sentence appears in it.
- Error records store the exception class and a safe message only. Pydantic errors are rendered
  with `include_input=False`; model output never goes into `jobs.error`.
- Retries: model validation failure retries once; network errors back off up to three attempts;
  then the job is marked failed and Slack is alerted with the job ID only.
- Evals over private data print aggregate metrics only (counts, accuracy, recall, word error rate).
  No per-example output, no examples in error messages.
- Production code paths read configuration from the environment the systemd unit provides. Nothing
  reads `~/.bashrc` or a shell profile.
