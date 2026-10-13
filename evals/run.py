"""Run a system's prompts over its eval cases and score them, on this machine.

    uv run python -m evals.run <system>                    # evals/synthetic/<system>/
    uv run python -m evals.run <system> --data-dir DIR --aggregate-only

Cases live in <data-dir>/<system>/cases.jsonl, one JSON object per line:
    {"id": "...", "input": {...}, "expected": {...}}

--aggregate-only (always used by scripts/eval_private.sh on real data) prints metric
lines only, `name=value`, and never an example, an input, an output or an error text.
Without it, per-example results are printed too; use that on synthetic data only.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import systems
from evals.outcome import Outcome, Scorer
from worker.config import Settings
from worker.errors import error_class
from worker.jobs import JobContext
from worker.local_llm import StructuredLLM

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = ROOT / "evals" / "synthetic"


def scorers() -> dict[str, Scorer]:
    """Systems that declare an eval scorer in their manifest, by key."""
    found: dict[str, Scorer] = {}
    for key, manifest in systems.all_manifests().items():
        if manifest.evals:
            found[key] = systems.load_target(manifest.evals)
    return found


def load_cases(data_dir: Path, system: str) -> list[dict[str, Any]]:
    path = data_dir / system / "cases.jsonl"
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def metrics(system: str, outcomes: list[Outcome]) -> dict[str, float | int | str]:
    n = len(outcomes)
    latencies = sorted(o.latency_ms for o in outcomes) or [0]
    return {
        "system": system,
        "examples": n,
        "accuracy": round(sum(o.correct for o in outcomes) / n, 4) if n else 0.0,
        "schema_valid_rate": round(sum(o.schema_valid for o in outcomes) / n, 4) if n else 0.0,
        "errors": sum(o.error is not None for o in outcomes),
        "latency_ms_median": int(statistics.median(latencies)),
        "latency_ms_p95": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))],
    }


def run(
    system: str,
    data_dir: Path,
    llm: StructuredLLM,
    settings: Settings,
    *,
    aggregate_only: bool,
    out=sys.stdout,
) -> dict[str, float | int | str]:
    scorer = scorers()[system]
    ctx = JobContext(settings=settings, llm=llm, private_data_dir=settings.private_data_dir)
    outcomes = [
        scorer(case, case.get("expected", {}), ctx) for case in load_cases(data_dir, system)
    ]
    if not aggregate_only:
        for o in outcomes:
            print(
                f"case {o.case_id}: {'pass' if o.correct else 'FAIL'}"
                f" ({o.latency_ms} ms){' ' + o.error if o.error else ''}",
                file=out,
            )
    result = metrics(system, outcomes)
    for key, value in result.items():
        print(f"{key}={value}", file=out)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("system", choices=sorted(scorers()))
    parser.add_argument("--data-dir", type=Path, default=SYNTHETIC)
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args(argv)

    from worker.config import get_settings
    from worker.local_llm import LocalLLM

    settings = get_settings()
    llm = LocalLLM(
        settings.local_llm_base_url,
        settings.local_llm_model,
        thinking=settings.local_llm_thinking,
        timeout_s=settings.local_llm_timeout_s,
    )
    try:
        run(args.system, args.data_dir, llm, settings, aggregate_only=args.aggregate_only)
    except Exception as exc:  # noqa: BLE001 - never print data on failure
        print(f"eval_error={error_class(exc)}")
        return 1
    finally:
        llm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
