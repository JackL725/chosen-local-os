"""Phase 1 load check: N healthcheck jobs at once through the worker and llama-server.

    uv run python scripts/load_check.py --jobs 10

Run on the workstation with llama-server up and a worker running with
WORKER_CONCURRENCY equal to LLAMA_PARALLEL (docs/runbook.md, "Load check"). The
healthcheck system must be turned on. It enqueues N jobs with random nonces, waits for
them, and prints aggregate numbers only. Pass: every job succeeded.
"""

from __future__ import annotations

import argparse
import os
import secrets
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker import queue
from worker.db import connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/load_check.py")
    parser.add_argument("--jobs", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("load_check_error=DATABASE_URL is not set")
        return 2
    with connect(url) as conn:
        row = conn.execute(
            "SELECT enabled OR required AS is_on FROM systems WHERE key = 'healthcheck'"
        ).fetchone()
        conn.commit()
        if not row or not row["is_on"]:
            print("load_check_error=turn the healthcheck system on in the console first")
            return 2
        started = time.monotonic()
        ids = [
            queue.enqueue(conn, "healthcheck", {"nonce": secrets.token_hex(8)}, actor="eval")
            for _ in range(args.jobs)
        ]
        while True:
            rows = conn.execute(
                "SELECT status, count(*) AS n FROM jobs WHERE id = ANY(%s::uuid[]) GROUP BY status",
                (ids,),
            ).fetchall()
            conn.commit()
            counts = {r["status"]: r["n"] for r in rows}
            done = counts.get("succeeded", 0) + counts.get("failed", 0)
            if done == len(ids) or time.monotonic() - started > args.timeout:
                break
            time.sleep(1)
        wall = time.monotonic() - started
        durations = [
            int(r["d"])
            for r in conn.execute(
                """SELECT (payload ->> 'duration_ms') AS d FROM events
                    WHERE event_type = 'job.succeeded' AND entity_id = ANY(%s)""",
                ([str(i) for i in ids],),
            ).fetchall()
        ]
        workers = conn.execute(
            "SELECT count(DISTINCT claimed_by) AS n FROM jobs WHERE id = ANY(%s::uuid[])", (ids,)
        ).fetchone()["n"]
        conn.commit()
    ok = counts.get("succeeded", 0) == len(ids)
    durations.sort()
    print(f"jobs={len(ids)}")
    print(f"succeeded={counts.get('succeeded', 0)}")
    print(f"failed={counts.get('failed', 0)}")
    print(f"unfinished={len(ids) - done}")
    print(f"worker_slots_used={workers}")
    print(f"wall_s={wall:.1f}")
    if durations:
        print(f"job_ms_median={int(statistics.median(durations))}")
        print(f"job_ms_max={durations[-1]}")
    print(f"result={'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
