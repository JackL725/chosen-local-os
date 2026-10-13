"""What a system's eval scorer returns for one case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from worker.jobs import JobContext


@dataclass(frozen=True)
class Outcome:
    case_id: str
    correct: bool
    schema_valid: bool
    latency_ms: int
    error: str | None = None


# A scorer: (case, expected, context) -> Outcome. Each system names its own in its
# manifest (`evals="systems.<key>.evals:score"`). It must never raise or print.
Scorer = Callable[[dict[str, Any], dict[str, Any], JobContext], Outcome]
