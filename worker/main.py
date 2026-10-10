"""The worker loop: claim a job, run its handler, write the result and its event.

Threads:
  * main loop: reap expired leases (every WORKER_REAP_INTERVAL_S), claim, run, record.
  * heartbeat: every WORKER_HEARTBEAT_S, upsert worker_heartbeats and renew the lease
    of the job in hand. Each thread has its own connection.

SIGTERM/SIGINT finish the current job, then exit. SIGKILL leaves the job `running`
until its lease expires; the next reaper pass returns it to the queue.
"""

from __future__ import annotations

import signal
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg

from worker import errors, queue
from worker.config import Settings
from worker.db import connect, transaction
from worker.jobs import JobContext, resolve
from worker.local_llm import StructuredLLM
from worker.logs import get_logger
from worker.slack import AlertSink, format_job_failed

log = get_logger(__name__)


class Worker:
    def __init__(
        self,
        settings: Settings,
        llm: StructuredLLM,
        alert: AlertSink,
        *,
        connect_fn: Callable[[str], psycopg.Connection] = connect,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.alert = alert
        self._connect = connect_fn
        self.worker_id = settings.worker_id
        self.host = socket.gethostname()
        self.started_at = datetime.now(UTC)
        self._stop = threading.Event()
        self._current_job_id: str | None = None
        self._lock = threading.Lock()
        self._ctx = JobContext(settings=settings, llm=llm,
                               private_data_dir=settings.private_data_dir)

    # ----------------------------------------------------------------- lifecycle
    def stop(self, *_: object) -> None:
        self._stop.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

    def run(self, *, max_jobs: int | None = None, idle_exit: bool = False) -> int:
        """Run until stopped. Returns the number of jobs processed."""
        conn = self._connect(self.settings.database_url)
        hb = threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True)
        hb.start()
        self._emit(conn, "worker.started")
        log.info("worker started", extra={"worker_id": self.worker_id})
        processed = 0
        last_reap = 0.0
        try:
            while not self._stop.is_set():
                if time.monotonic() - last_reap >= self.settings.worker_reap_interval_s:
                    self._reap(conn)
                    last_reap = time.monotonic()
                job = queue.claim(conn, self.worker_id, self.settings.worker_lease_s)
                if job is None:
                    if idle_exit:
                        break
                    self._stop.wait(self.settings.worker_poll_interval_s)
                    continue
                self.run_job(conn, job)
                processed += 1
                if max_jobs is not None and processed >= max_jobs:
                    break
        finally:
            self._stop.set()
            hb.join(timeout=5)
            try:
                self._emit(conn, "worker.stopped")
            except psycopg.Error:
                pass
            conn.close()
            log.info("worker stopped", extra={"worker_id": self.worker_id, "count": processed})
        return processed

    # --------------------------------------------------------------------- jobs
    def run_job(self, conn: psycopg.Connection, job: queue.Job) -> str:
        with self._lock:
            self._current_job_id = job.id
        started = time.monotonic()
        extra = {"job_id": job.id, "job_type": job.job_type, "attempt": job.attempts,
                 "worker_id": self.worker_id}
        log.info("job claimed", extra=extra)
        try:
            handler = resolve(job.job_type, enable_test_jobs=self.settings.worker_enable_test_jobs)
            result = handler(job, self._ctx)
            duration_ms = int((time.monotonic() - started) * 1000)
            queue.complete(conn, job, self.worker_id, result_ref=result.result_ref,
                           duration_ms=duration_ms, prompt_version=result.prompt_version,
                           model=result.model, writes=result.writes)
            log.info("job succeeded", extra={**extra, "status": "succeeded",
                                             "duration_ms": duration_ms,
                                             "tokens_in": result.tokens_in,
                                             "tokens_out": result.tokens_out})
            return "succeeded"
        except queue.LeaseLost:
            log.warning("job lease lost; result discarded", extra=extra)
            return "lost"
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, never re-raised
            return self._record_failure(conn, job, exc, started, extra)
        finally:
            with self._lock:
                self._current_job_id = None

    def _record_failure(self, conn: psycopg.Connection, job: queue.Job, exc: BaseException,
                        started: float, extra: dict) -> str:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
        cls = errors.error_class(exc)
        status = queue.fail(conn, job, self.worker_id, error_class=cls,
                            error_detail=errors.safe_message(exc),
                            retryable=errors.is_retryable(exc),
                            retry_base_s=self.settings.worker_retry_base_s)
        log.warning("job failed", extra={**extra, "status": status, "error_class": cls,
                                         "duration_ms": int((time.monotonic() - started) * 1000)})
        if status == "failed":
            self._alert_failed(job.id, job.job_type, cls)
        return status

    def _alert_failed(self, job_id: str, job_type: str, error_class: str) -> None:
        try:
            self.alert(format_job_failed(job_id, job_type, error_class,
                                         self.settings.review_console_base_url))
        except ValueError:
            self.alert(format_job_failed(job_id, "unknown", "UnknownError",
                                         self.settings.review_console_base_url))

    def _reap(self, conn: psycopg.Connection) -> None:
        for reaped in queue.reap_expired(conn):
            log.warning("job lease expired", extra={"job_id": reaped.id, "job_type": reaped.job_type,
                                                    "status": reaped.status})
            if reaped.status == "failed":
                self._alert_failed(reaped.id, reaped.job_type, "LeaseExpired")

    # ---------------------------------------------------------------- heartbeat
    def _heartbeat_loop(self) -> None:
        conn: psycopg.Connection | None = None
        while True:
            try:
                if conn is None or conn.closed:
                    conn = self._connect(self.settings.database_url)
                with self._lock:
                    job_id = self._current_job_id
                queue.heartbeat(conn, self.worker_id, self.host, self.settings.git_sha,
                                self.started_at, job_id)
                if job_id:
                    queue.renew_lease(conn, job_id, self.worker_id, self.settings.worker_lease_s)
            except psycopg.Error as exc:
                log.warning("heartbeat failed", extra={"error_class": type(exc).__name__})
                if conn is not None:
                    conn.close()
                conn = None
            if self._stop.wait(self.settings.worker_heartbeat_s):
                break
        if conn is not None:
            conn.close()

    def _emit(self, conn: psycopg.Connection, event_type: str) -> None:
        from psycopg.types.json import Jsonb

        with transaction(conn, actor="worker"), conn.cursor() as cur:
            cur.execute("SELECT ledger_emit('worker', %s, %s, %s)",
                        (self.worker_id, event_type,
                         Jsonb({"host": self.host, "git_sha": self.settings.git_sha or None})))
