"""Structured logging that cannot carry prospect data.

Rules enforced here:
  * A log record keeps only allowlisted `extra` fields (IDs, counts, timings, status).
  * Exception text and tracebacks are dropped; only the exception class is kept.
  * The message itself is scrubbed for emails and phone numbers as a last line of
    defence. Our own log messages are fixed strings; data goes in allowlisted fields.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Any

ALLOWED_FIELDS = frozenset(
    {
        "job_id",
        "job_type",
        "status",
        "attempt",
        "max_attempts",
        "duration_ms",
        "latency_ms",
        "tokens_in",
        "tokens_out",
        "model",
        "prompt_version",
        "worker_id",
        "error_class",
        "count",
        "retry_in_s",
        "http_status",
        "system",
        "port",
    }
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:/-]{0,120}$")

_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "asctime",
    "taskName",
}


def scrub_text(text: str) -> str:
    # UUIDs look like long digit runs to the phone pattern; protect them first.
    uuids: list[str] = []

    def _keep(m: re.Match[str]) -> str:
        uuids.append(m.group(0))
        return f"\x00{len(uuids) - 1}\x00"

    text = _UUID.sub(_keep, text)
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[number]", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: uuids[int(m.group(1))], text)


def _safe_field(value: Any) -> Any:
    if isinstance(value, bool | int | float) or value is None:
        return value
    text = str(value)
    if _SAFE_VALUE.match(text):
        return text
    return "[redacted]"


class ScrubbingFilter(logging.Filter):
    """Strips anything that is not an allowlisted, identifier-shaped field."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(vars(record)):
            if key in _STANDARD_ATTRS:
                continue
            if key not in ALLOWED_FIELDS:
                delattr(record, key)
            else:
                setattr(record, key, _safe_field(getattr(record, key)))
        if record.exc_info:
            exc = record.exc_info[1]
            record.error_class = type(exc).__name__ if exc else None
            record.exc_info = None
            record.exc_text = None
        record.stack_info = None
        record.msg = scrub_text(record.getMessage())
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ALLOWED_FIELDS:
            if hasattr(record, key):
                out[key] = getattr(record, key)
        return json.dumps(out, separators=(",", ":"))


def configure(level: int = logging.INFO, stream: Any = None) -> None:
    """Route every logger (ours and libraries') through the scrubbing handler."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(ScrubbingFilter())
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs at INFO; webhook URLs are secrets.
    for noisy in ("httpx", "httpcore", "psycopg"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
