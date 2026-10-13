"""The queue and the worker loop, in process, with the fake model."""

from __future__ import annotations

import io
import logging
import threading
import time

import pytest

from tests.fakes.fake_llm import FakeLLM
from worker import logs, queue
from worker.db import connect
from worker.main import Worker

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _healthcheck_on(db_url):
    """New systems arrive turned off; these tests run healthcheck jobs, so turn it on."""
    from tests.conftest import enable_systems

    enable_systems(db_url, "healthcheck")


def _job_row(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    conn.commit()
    return row


def _events(conn, job_id):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT e.event_type, e.prompt_version, e.model, e.job_id, a.name AS actor,
                              e.payload, e.output_ref
                         FROM events e JOIN actors a ON a.id = e.actor_id
                        WHERE (e.entity_type = 'job' AND e.entity_id = %s) OR e.job_id = %s
                        ORDER BY e.id""",
            (job_id, job_id),
        )
        rows = cur.fetchall()
    conn.commit()
    return rows


def test_healthcheck_job_round_trip_writes_result_and_event(conn, db_url, settings_factory):
    settings = settings_factory(database_url=db_url)
    alerts: list[str] = []
    job_id = queue.enqueue(conn, "healthcheck", {"nonce": "Zq81kP03"})
    started = time.monotonic()
    processed = Worker(settings, FakeLLM(), alerts.append).run(idle_exit=True)
    assert processed == 1
    assert time.monotonic() - started < 30
    row = _job_row(conn, job_id)
    assert row["status"] == "succeeded" and row["result_ref"]["ok"] is True
    events = _events(conn, job_id)
    done = [e for e in events if e["event_type"] == "job.succeeded"]
    assert len(done) == 1
    assert done[0]["actor"] == "worker"
    assert done[0]["prompt_version"] == "healthcheck/echo.v1"
    assert done[0]["model"] == "fake-model"
    assert str(done[0]["job_id"]) == job_id
    assert alerts == []


def test_skip_locked_means_two_workers_never_share_a_job(conn, db_url):
    for _ in range(6):
        queue.enqueue(conn, "healthcheck", {"nonce": "abcdef12"})
    other = connect(db_url)
    try:
        claimed = []
        for c, wid in ((conn, "w1"), (other, "w2"), (conn, "w1"), (other, "w2")):
            job = queue.claim(c, wid, lease_s=60)
            assert job is not None
            claimed.append(job.id)
        assert len(set(claimed)) == 4
    finally:
        other.close()


def test_network_errors_back_off_then_fail_and_alert_once(
    conn, db_url, settings_factory, sentinels
):
    settings = settings_factory(database_url=db_url, worker_retry_base_s=0.01)
    alerts: list[str] = []
    leak = sentinels["transcript_sentences"][0]
    job_id = queue.enqueue(conn, "test_fail", {"leak": leak}, max_attempts=3)
    worker = Worker(settings, FakeLLM(), alerts.append)
    for _ in range(3):
        time.sleep(0.1)  # let the backoff elapse
        worker.run(idle_exit=True)
    row = _job_row(conn, job_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 3
    assert row["error_class"] == "InjectedFailure"
    assert leak not in (row["error_detail"] or "")
    types = [e["event_type"] for e in _events(conn, job_id)]
    assert types.count("job.retry_scheduled") == 2 and types.count("job.failed") == 1
    assert len(alerts) == 1
    assert job_id in alerts[0] and "test_fail" in alerts[0] and leak not in alerts[0]


def test_permanent_errors_fail_without_retrying(conn, db_url, settings_factory):
    settings = settings_factory(database_url=db_url)
    alerts: list[str] = []
    job_id = queue.enqueue(conn, "test_fail", {"permanent": True}, max_attempts=3)
    Worker(settings, FakeLLM(), alerts.append).run(idle_exit=True)
    row = _job_row(conn, job_id)
    assert (row["status"], row["attempts"]) == ("failed", 1)
    assert len(alerts) == 1


def test_model_validation_failure_retries_once_then_fails(conn, db_url, settings_factory):
    settings = settings_factory(database_url=db_url)
    alerts: list[str] = []
    llm = FakeLLM(lambda messages: '{"ok": true, "echo": "wrong"}')
    job_id = queue.enqueue(conn, "healthcheck", {"nonce": "Zq81kP03"})
    Worker(settings, llm, alerts.append).run(idle_exit=True)
    row = _job_row(conn, job_id)
    assert (row["status"], row["error_class"]) == ("failed", "ModelOutputInvalid")
    assert len(alerts) == 1


def test_unknown_job_type_fails_cleanly(conn, db_url, settings_factory):
    settings = settings_factory(database_url=db_url, worker_enable_test_jobs=False)
    job_id = queue.enqueue(conn, "test_sleep", {"sleep_s": 0})
    Worker(settings, FakeLLM(), lambda _t: None).run(idle_exit=True)
    assert _job_row(conn, job_id)["error_class"] == "UnknownJobType"


def test_expired_lease_is_reaped_back_to_pending_then_failed_on_last_attempt(conn, db_url):
    alerts: list[str] = []
    job_id = queue.enqueue(conn, "healthcheck", {"nonce": "abcdef12"}, max_attempts=2)
    for expected in ("pending", "failed"):
        job = queue.claim(conn, "dead-worker", lease_s=0)
        assert job is not None and job.id == job_id
        time.sleep(0.05)
        reaped = queue.reap_expired(conn)
        assert [(r.id, r.status) for r in reaped] == [(job_id, expected)]
    assert _job_row(conn, job_id)["error_class"] == "LeaseExpired"
    assert alerts == []  # the worker loop sends the alert; covered in the process test


def test_complete_after_lease_lost_is_discarded(conn, db_url):
    job_id = queue.enqueue(conn, "healthcheck", {"nonce": "abcdef12"})
    job = queue.claim(conn, "slow-worker", lease_s=0)
    time.sleep(0.05)
    queue.reap_expired(conn)
    other = queue.claim(conn, "fast-worker", lease_s=60)
    assert other is not None and other.id == job_id
    with pytest.raises(queue.LeaseLost):
        queue.complete(conn, job, "slow-worker", result_ref={}, duration_ms=1)
    assert _job_row(conn, job_id)["claimed_by"] == "fast-worker"


def test_heartbeat_row_and_lease_renewal(conn, db_url, settings_factory):
    settings = settings_factory(database_url=db_url, worker_heartbeat_s=0.05, worker_lease_s=2)
    job_id = queue.enqueue(conn, "test_sleep", {"sleep_s": 0.6})
    worker = Worker(settings, FakeLLM(), lambda _t: None)
    t = threading.Thread(target=worker.run, kwargs={"max_jobs": 1})
    t.start()
    time.sleep(0.3)
    first = _job_row(conn, job_id)["lease_expires_at"]
    time.sleep(0.2)
    second = _job_row(conn, job_id)["lease_expires_at"]
    t.join(timeout=10)
    assert first is not None and second is not None and second > first
    with conn.cursor() as cur:
        cur.execute("SELECT worker_id FROM worker_heartbeats")
        assert [r["worker_id"] for r in cur.fetchall()] == ["test-worker"]
    conn.commit()
    assert _job_row(conn, job_id)["status"] == "succeeded"


def test_full_synthetic_run_leaks_nothing(conn, db_url, settings_factory, sentinels):
    """Every sentinel is pushed through success and failure paths; none may surface."""
    stream = io.StringIO()
    logs.configure(level=logging.DEBUG, stream=stream)
    settings = settings_factory(database_url=db_url)
    alerts: list[str] = []
    every = [
        s
        for group in ("names", "phones", "emails", "transcript_sentences")
        for s in sentinels[group]
    ]

    def chatty_model(messages):  # invalid first, quoting a transcript line, then valid
        chatty_model.n += 1
        if chatty_model.n % 2:  # schema-invalid: "ok" must be a boolean
            return '{"ok": "' + every[chatty_model.n % len(every)] + '", "echo": 1}'
        return '{"ok": true, "echo": "' + messages[-1]["content"] + '"}'

    chatty_model.n = 0
    ids = [queue.enqueue(conn, "healthcheck", {"nonce": f"Nonce{i:04d}"}) for i in range(4)]
    for s in every:
        ids.append(queue.enqueue(conn, "test_fail", {"leak": s, "permanent": True}))
    Worker(settings, FakeLLM(chatty_model), alerts.append).run(idle_exit=True)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(string_agg(coalesce(error_detail, '') || error_class, ' '), '') AS t "
            "FROM jobs WHERE status = 'failed'"
        )
        errors_text = cur.fetchone()["t"]
        cur.execute(
            "SELECT string_agg(payload::text || coalesce(output_ref::text, ''), ' ') AS t FROM events"
        )
        events_text = cur.fetchone()["t"]
    conn.commit()
    haystacks = {
        "logs": stream.getvalue(),
        "slack": "\n".join(alerts),
        "jobs.error": errors_text,
        "events": events_text,
    }
    assert "job succeeded" in haystacks["logs"]
    assert len(alerts) == len(every)
    for where, text in haystacks.items():
        for s in every:
            assert s not in text, f"sentinel leaked into {where}"


def test_jobs_of_a_paused_system_wait_then_run_when_it_is_turned_on(conn, db_url, settings_factory):
    from worker.db import transaction

    settings = settings_factory(database_url=db_url)
    with transaction(conn, actor="sam"):
        conn.execute("UPDATE systems SET enabled = false WHERE key = 'healthcheck'")
    job_id = queue.enqueue(conn, "healthcheck", {"nonce": "paused01"})
    Worker(settings, FakeLLM(), lambda _t: None).run(idle_exit=True)
    assert _job_row(conn, job_id)["status"] == "pending"
    health = conn.execute("SELECT * FROM api.queue_health()").fetchone()
    conn.commit()
    assert (health["pending_count"], health["paused_pending_count"]) == (0, 1)
    assert health["oldest_pending_job_id"] is None

    with transaction(conn, actor="sam"):
        conn.execute("UPDATE systems SET enabled = true WHERE key = 'healthcheck'")
    Worker(settings, FakeLLM(), lambda _t: None).run(idle_exit=True)
    assert _job_row(conn, job_id)["status"] == "succeeded"


def test_a_system_cannot_turn_itself_on(conn, db_url):
    import psycopg

    from worker.db import transaction

    with transaction(conn, actor="sam"):
        conn.execute("UPDATE systems SET enabled = false WHERE key = 'healthcheck'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="founder"):
        with transaction(conn, actor="worker"):
            conn.execute("UPDATE systems SET enabled = true WHERE key = 'healthcheck'")
    conn.rollback()


def test_registering_systems_again_writes_no_events(conn, db_url):
    from worker.system_sync import sync_systems

    def count():
        n = conn.execute(
            "SELECT count(*) AS n FROM events WHERE entity_type IN ('systems', 'system_job_types')"
        ).fetchone()["n"]
        conn.commit()
        return n

    before = count()
    sync_systems(conn)
    sync_systems(conn)
    assert count() == before
