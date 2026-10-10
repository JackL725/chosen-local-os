"""Error types and the only way an error becomes text the Ledger keeps.

Error records store the exception class and a safe message chosen by our code. An
exception's own message (which may quote model output, a payload or a transcript) is
never stored, logged or sent anywhere.
"""

from __future__ import annotations

import psycopg


class WorkerError(Exception):
    """Base class. `safe_message` is fixed text written by us, never data."""

    safe_message = "worker error"
    retryable = False


class RetryableError(WorkerError):
    safe_message = "transient error; will retry"
    retryable = True


class LocalModelUnavailable(RetryableError):
    safe_message = "local model server unreachable or returned a server error"


class ModelOutputInvalid(WorkerError):
    safe_message = "model output failed validation after one retry"


class UnknownJobType(WorkerError):
    safe_message = "no handler registered for this job type"


class ProspectDataRefused(WorkerError):
    safe_message = "cloud model call refused: prospect data flag not explicitly False"


class CallerNotAllowed(WorkerError):
    safe_message = "cloud model call refused: calling module is not on the allowlist"


# Library exceptions that mean "the network or a server blinked": back off and retry.
# (HTTP errors from the model server arrive already wrapped as LocalModelUnavailable.)
_RETRYABLE_LIBRARY_ERRORS: tuple[type[BaseException], ...] = (
    psycopg.OperationalError,
    TimeoutError,
    ConnectionError,
)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, WorkerError):
        return exc.retryable
    return isinstance(exc, _RETRYABLE_LIBRARY_ERRORS)


def error_class(exc: BaseException) -> str:
    return type(exc).__name__[:100]


def safe_message(exc: BaseException) -> str:
    """Text safe to store in jobs.error_detail. Never derived from the exception's args."""
    if isinstance(exc, WorkerError):
        return exc.safe_message
    if is_retryable(exc):
        return RetryableError.safe_message
    return "unhandled error"
