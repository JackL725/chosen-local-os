"""healthcheck: ask the local model to echo a nonce as schema-valid JSON.

The reference system: one job, one prompt, one eval suite, no screens and no tables.
Copy its shape, or run `python scripts/new_system.py <key>`.
"""

from __future__ import annotations

from systems._contract import JobSpec, Manifest, ModelUse, PromptSpec

MANIFEST = Manifest(
    key="healthcheck",
    title="Health check",
    description="Asks the local model to echo a nonce. Used by acceptance tests and as a canary.",
    version=1,
    data_class="none",
    jobs=(
        JobSpec(
            job_type="healthcheck",
            handler="systems.healthcheck.jobs:handle",
            description="Echo a nonce through the local model.",
            model_use="echo",
        ),
    ),
    models=(ModelUse(name="echo", profile="local"),),
    prompts=(PromptSpec(name="echo", version=1),),
    evals="systems.healthcheck.evals:score",
)
