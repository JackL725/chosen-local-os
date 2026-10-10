"""healthcheck: ask the local model to echo a nonce as schema-valid JSON."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from worker import prompts
from worker.errors import ModelOutputInvalid
from worker.jobs import JobContext, JobResult
from worker.queue import Job

PROMPT_KEY = "healthcheck/echo"
_NONCE = re.compile(r"^[A-Za-z0-9]{6,64}$")


class HealthcheckOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    echo: str


class BadPayload(ModelOutputInvalid):
    safe_message = "healthcheck payload must carry an alphanumeric nonce"


def handle(job: Job, ctx: JobContext) -> JobResult:
    nonce = str(job.payload.get("nonce", ""))
    if not _NONCE.match(nonce):
        raise BadPayload()
    system, name = PROMPT_KEY.split("/")
    prompt = prompts.load(system, name, ctx.settings.prompt_versions[PROMPT_KEY])
    result = ctx.llm.complete(
        [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": nonce},
        ],
        HealthcheckOutput,
        prompt_version=prompt.version,
    )
    if not result.value.ok or result.value.echo != nonce:
        raise ModelOutputInvalid()
    return JobResult(
        result_ref={"ok": True, "latency_ms": result.latency_ms},
        prompt_version=result.prompt_version,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )
