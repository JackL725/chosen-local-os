"""Job handlers and the registry that maps job_type to a handler.

A handler is a pure function of (job, context) that returns a JobResult. Any Ledger
writes it needs are returned as `writes`, a callable the worker runs inside the same
transaction that marks the job succeeded and writes its event.

Modules that may reach cloud_llm must never be imported here directly: add them to
LAZY_HANDLERS by dotted name so this registry stays outside the cloud boundary.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

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

# job_type -> "module:function", imported on first use.
LAZY_HANDLERS: dict[str, str] = {
    "healthcheck": "worker.jobs.healthcheck:handle",
}

# Only registered when WORKER_ENABLE_TEST_JOBS=1 (tests and the dev stack).
TEST_HANDLERS: dict[str, str] = {
    "test_sleep": "worker.jobs.testing:sleep",
    "test_fail": "worker.jobs.testing:fail",
}


def resolve(job_type: str, *, enable_test_jobs: bool) -> Handler:
    target = LAZY_HANDLERS.get(job_type)
    if target is None and enable_test_jobs:
        target = TEST_HANDLERS.get(job_type)
    if target is None:
        raise UnknownJobType()
    module_name, func_name = target.split(":")
    return getattr(importlib.import_module(module_name), func_name)
