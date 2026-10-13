"""Eval scorer for healthcheck: run the real handler on each case."""

from __future__ import annotations

import time
from typing import Any

from evals.outcome import Outcome
from systems.healthcheck import jobs
from worker.errors import error_class
from worker.jobs import JobContext
from worker.queue import Job


def score(case: dict[str, Any], expected: dict[str, Any], ctx: JobContext) -> Outcome:
    job = Job(
        id=case["id"],
        job_type="healthcheck",
        payload=case["input"],
        attempts=1,
        max_attempts=1,
        entity_type=None,
        entity_id=None,
        claimed_at=None,
    )
    started = time.monotonic()
    try:
        jobs.handle(job, ctx)
    except Exception as exc:  # noqa: BLE001 - every case is scored, never raised
        return Outcome(
            case["id"],
            False,
            error_class(exc) != "ModelOutputInvalid",
            int((time.monotonic() - started) * 1000),
            error_class(exc),
        )
    ok = expected.get("ok") is True
    return Outcome(case["id"], ok, True, int((time.monotonic() - started) * 1000))
