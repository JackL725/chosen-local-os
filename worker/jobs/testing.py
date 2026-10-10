"""Jobs that exist only for tests. Registered only when WORKER_ENABLE_TEST_JOBS=1."""

from __future__ import annotations

import time

from worker.errors import RetryableError, WorkerError
from worker.jobs import JobContext, JobResult
from worker.queue import Job


class InjectedFailure(RetryableError):
    safe_message = "test job failed on purpose"


class InjectedPermanentFailure(WorkerError):
    safe_message = "test job failed on purpose (permanent)"


def sleep(job: Job, ctx: JobContext) -> JobResult:
    """Sleep on the first attempt only, so a killed run can be retried quickly."""
    if job.attempts == 1:
        time.sleep(float(job.payload.get("sleep_s", 30)))
    return JobResult(result_ref={"slept_on_attempt": job.attempts})


def fail(job: Job, ctx: JobContext) -> JobResult:
    """Always fail. The exception text carries a payload value, which must never be stored."""
    leak = str(job.payload.get("leak", ""))
    if job.payload.get("permanent"):
        raise InjectedPermanentFailure(leak)
    raise InjectedFailure(leak)
