"""Job context, job results, and resolving a job_type to its handler.

A handler is a pure function of (job, context) that returns a JobResult. Any Ledger
writes it needs are returned as `writes`, a callable the worker runs inside the same
transaction that marks the job succeeded and writes its event.

Handlers belong to systems (systems/<key>/jobs.py) and are found through the system
registry, which imports them by name on first use. Nothing here imports a handler
module directly, so this module stays outside the cloud boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

import systems
from worker.config import Settings
from worker.errors import UnknownJobType
from worker.local_llm import StructuredLLM
from worker.queue import Job


@dataclass(frozen=True)
class JobContext:
    settings: Settings
    llm: StructuredLLM
    private_data_dir: Path


@dataclass(frozen=True)
class JobResult:
    result_ref: dict[str, Any]
    prompt_version: str | None = None
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    writes: Callable[[psycopg.Connection], None] | None = field(default=None, compare=False)


Handler = Callable[[Job, JobContext], JobResult]

# Only registered when WORKER_ENABLE_TEST_JOBS=1 (tests and the dev stack).
TEST_HANDLERS: dict[str, str] = {
    "test_sleep": "worker.jobs.testing:sleep",
    "test_fail": "worker.jobs.testing:fail",
}


def handled_job_types(*, enable_test_jobs: bool) -> set[str]:
    """Every job type this code base has a handler for."""
    types = set(systems.job_types())
    if enable_test_jobs:
        types |= set(TEST_HANDLERS)
    return types


def resolve(job_type: str, *, enable_test_jobs: bool) -> Handler:
    found = systems.job_spec(job_type)
    if found is not None:
        return systems.load_target(found[1].handler)
    if enable_test_jobs and job_type in TEST_HANDLERS:
        return systems.load_target(TEST_HANDLERS[job_type])
    raise UnknownJobType()
