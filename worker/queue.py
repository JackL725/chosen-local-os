"""The job queue: a Postgres table claimed with FOR UPDATE SKIP LOCKED and a lease.

Lifecycle:
  pending --claim--> running --complete--> succeeded
                        |--fail (retryable, attempts left)--> pending (run_after = backoff)
                        |--fail (permanent, or attempts used up)--> failed  (+ Slack alert)
                        |--lease expires (worker died)--> reaped back to pending, or failed
                           if that was the last attempt

`attempts` counts claims, so a job killed mid-run has used an attempt.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from worker.db import transaction

ACTOR = "worker"


@dataclass(frozen=True)
class Job:
    id: str
    job_type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    entity_type: str | None
    entity_id: str | None
    claimed_at: datetime | None


def _job(row: dict[str, Any]) -> Job:
    return Job(
        id=str(row["id"]),
        job_type=row["job_type"],
        payload=row["payload"] or {},
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        entity_type=row["entity_type"],
        entity_id=str(row["entity_id"]) if row["entity_id"] else None,
        claimed_at=row["claimed_at"],
    )


def enqueue(
    conn: psycopg.Connection,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    actor: str = ACTOR,
    max_attempts: int = 3,
    priority: int = 100,
    idempotency_key: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> str:
    with transaction(conn, actor=actor), conn.cursor() as cur:
        cur.execute(
            """INSERT INTO jobs (job_type, payload, max_attempts, priority, idempotency_key,
                                 entity_type, entity_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                job_type,
                Jsonb(payload or {}),
                max_attempts,
                priority,
                idempotency_key,
                entity_type,
                entity_id,
            ),
        )
        row = cur.fetchone()
    assert row is not None
    return str(row["id"])


def claim(conn: psycopg.Connection, worker_id: str, lease_s: int) -> Job | None:
    """Claim the next due job, skipping jobs of systems that are turned off.

    Those jobs wait (api.queue_health counts them apart and never alerts on them) and
    run once a founder turns the system back on. Job types no system declares are still
    claimed, so a typo fails loudly with UnknownJobType instead of waiting silently.
    """
    with transaction(conn, actor=ACTOR), conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs
                  SET status = 'running', claimed_by = %(w)s, claimed_at = now(),
                      lease_expires_at = now() + make_interval(secs => %(lease)s),
                      attempts = attempts + 1
                WHERE id = (SELECT j.id FROM jobs j
                             WHERE j.status = 'pending' AND j.run_after <= now()
                               AND NOT EXISTS (
                                     SELECT 1 FROM system_job_types t
                                       JOIN systems s ON s.key = t.system_key
                                      WHERE t.job_type = j.job_type
                                        AND NOT (s.enabled OR s.required))
                             ORDER BY j.priority, j.run_after
                             FOR UPDATE OF j SKIP LOCKED
                             LIMIT 1)
            RETURNING *""",
            {"w": worker_id, "lease": lease_s},
        )
        row = cur.fetchone()
    return _job(row) if row else None


def renew_lease(conn: psycopg.Connection, job_id: str, worker_id: str, lease_s: int) -> bool:
    with transaction(conn, actor=ACTOR), conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs SET lease_expires_at = now() + make_interval(secs => %s)
                WHERE id = %s AND status = 'running' AND claimed_by = %s""",
            (lease_s, job_id, worker_id),
        )
        return cur.rowcount == 1


def complete(
    conn: psycopg.Connection,
    job: Job,
    worker_id: str,
    *,
    result_ref: dict[str, Any],
    duration_ms: int,
    prompt_version: str | None = None,
    model: str | None = None,
    writes: Any = None,
) -> bool:
    """Commit the handler's writes, the job's success and its event together."""
    with transaction(conn, actor=ACTOR, job_id=job.id, prompt_version=prompt_version, model=model):
        if writes is not None:
            writes(conn)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE jobs
                      SET status = 'succeeded', result_ref = %s, finished_at = now(),
                          lease_expires_at = NULL, error_class = NULL, error_detail = NULL
                    WHERE id = %s AND status = 'running' AND claimed_by = %s""",
                (Jsonb(result_ref), job.id, worker_id),
            )
            if cur.rowcount != 1:
                # Our lease was lost and someone else owns the job now. Roll back.
                raise LeaseLost()
            cur.execute(
                "SELECT ledger_emit('job', %s, 'job.succeeded', %s, %s, %s)",
                (
                    job.id,
                    Jsonb(
                        {
                            "job_type": job.job_type,
                            "duration_ms": duration_ms,
                            "attempt": job.attempts,
                        }
                    ),
                    Jsonb({"job_id": job.id}),
                    Jsonb(result_ref),
                ),
            )
    return True


class LeaseLost(Exception):
    pass


def backoff_seconds(attempt: int, base_s: float) -> float:
    """30 s, 2 min, 8 min ... with +/-20% jitter (for base_s = 30)."""
    delay = base_s * (4 ** max(attempt - 1, 0))
    return delay * random.uniform(0.8, 1.2)  # noqa: S311 - jitter, not security


def fail(
    conn: psycopg.Connection,
    job: Job,
    worker_id: str,
    *,
    error_class: str,
    error_detail: str,
    retryable: bool,
    retry_base_s: float,
) -> str:
    """Record a failure. Returns the job's new status: 'pending' (will retry) or 'failed'."""
    terminal = (not retryable) or job.attempts >= job.max_attempts
    with transaction(conn, actor=ACTOR, job_id=job.id), conn.cursor() as cur:
        if terminal:
            cur.execute(
                """UPDATE jobs SET status = 'failed', finished_at = now(), lease_expires_at = NULL,
                                   error_class = %s, error_detail = %s
                    WHERE id = %s AND status = 'running' AND claimed_by = %s""",
                (error_class, error_detail[:500], job.id, worker_id),
            )
        else:
            cur.execute(
                """UPDATE jobs SET status = 'pending', lease_expires_at = NULL,
                                   claimed_by = NULL, claimed_at = NULL,
                                   run_after = now() + make_interval(secs => %s),
                                   error_class = %s, error_detail = %s
                    WHERE id = %s AND status = 'running' AND claimed_by = %s""",
                (
                    backoff_seconds(job.attempts, retry_base_s),
                    error_class,
                    error_detail[:500],
                    job.id,
                    worker_id,
                ),
            )
        if cur.rowcount != 1:
            return "lost"
        cur.execute(
            "SELECT ledger_emit('job', %s, %s, %s)",
            (
                job.id,
                "job.failed" if terminal else "job.retry_scheduled",
                Jsonb(
                    {"job_type": job.job_type, "attempt": job.attempts, "error_class": error_class}
                ),
            ),
        )
    return "failed" if terminal else "pending"


@dataclass(frozen=True)
class ReapedJob:
    id: str
    job_type: str
    status: str


def reap_expired(conn: psycopg.Connection) -> list[ReapedJob]:
    """Return jobs whose worker vanished to the queue, or fail them if out of attempts."""
    with transaction(conn, actor=ACTOR), conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs
                  SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
                      finished_at = CASE WHEN attempts >= max_attempts THEN now() END,
                      claimed_by = NULL, claimed_at = NULL, lease_expires_at = NULL,
                      error_class = 'LeaseExpired',
                      error_detail = 'worker stopped while running this job'
                WHERE id IN (SELECT id FROM jobs
                              WHERE status = 'running' AND lease_expires_at < now()
                              FOR UPDATE SKIP LOCKED)
            RETURNING id, job_type, status"""
        )
        rows = cur.fetchall()
        for row in rows:
            cur.execute(
                "SELECT ledger_emit('job', %s, %s, %s)",
                (
                    str(row["id"]),
                    "job.lease_expired",
                    Jsonb({"job_type": row["job_type"], "new_status": row["status"]}),
                ),
            )
    return [ReapedJob(str(r["id"]), r["job_type"], r["status"]) for r in rows]


def heartbeat(
    conn: psycopg.Connection,
    worker_id: str,
    host: str,
    git_sha: str,
    started_at: datetime,
    current_job_id: str | None,
) -> None:
    with transaction(conn, actor=ACTOR), conn.cursor() as cur:
        cur.execute(
            """INSERT INTO worker_heartbeats (worker_id, host, git_sha, started_at, last_seen_at,
                                              current_job_id)
               VALUES (%s, %s, %s, %s, now(), %s)
               ON CONFLICT (worker_id) DO UPDATE
                  SET last_seen_at = now(), current_job_id = EXCLUDED.current_job_id,
                      git_sha = EXCLUDED.git_sha""",
            (worker_id, host, git_sha or None, started_at, current_job_id),
        )
