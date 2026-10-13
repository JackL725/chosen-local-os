"""Acceptance tests that need a real worker process.

* Killing the worker mid-job leaves the job retryable; a new worker finishes it.
* Three failures post exactly one Slack alert (to a local fake webhook).
* Nothing the worker prints contains a synthetic sentinel.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from worker import queue

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[2]


def _env(db_url: str, **extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "PYTHONPATH", "PSYCOPG_IMPL", "HOME", "LANG", "VIRTUAL_ENV")
    }
    env.update(
        {
            "DATABASE_URL": db_url,
            "PRIVATE_DATA_DIR": str(ROOT / "evals" / "synthetic"),
            "LOCAL_LLM_BASE_URL": "http://127.0.0.1:9/v1",  # nothing listens; test jobs need no model
            "WORKER_ENABLE_TEST_JOBS": "1",
            "WORKER_POLL_INTERVAL_S": "0.1",
            "WORKER_HEARTBEAT_S": "0.3",
            "WORKER_REAP_INTERVAL_S": "0.2",
            "WORKER_LEASE_S": "2",
            "WORKER_RETRY_BASE_S": "0.05",
        }
    )
    env.update(extra)
    return env


def _start(env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "worker"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for(predicate, timeout: float = 20.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def _status(conn, job_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts, claimed_by FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    conn.commit()
    return row


def _stop(proc: subprocess.Popen) -> str:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.stdout.read() if proc.stdout else ""


def test_killing_the_worker_mid_job_leaves_it_retryable(conn, db_url):
    job_id = queue.enqueue(conn, "test_sleep", {"sleep_s": 60})
    env = _env(db_url)
    first = _start(env)
    try:
        _wait_for(lambda: _status(conn, job_id)["status"] == "running")
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=5)
        assert _status(conn, job_id)["status"] == "running"  # orphaned until its lease expires
    finally:
        _stop(first)
    second = _start(env)
    try:
        row = _wait_for(lambda: (r := _status(conn, job_id))["status"] == "succeeded" and r)
        assert row["attempts"] == 2
    finally:
        _stop(second)


class _FakeSlack(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        _FakeSlack.received.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:
        return


def test_three_failures_post_one_slack_alert_and_nothing_leaks(conn, db_url, sentinels):
    _FakeSlack.received = []
    server = HTTPServer(("127.0.0.1", 0), _FakeSlack)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/services/T000/B000/fake"
    leak = sentinels["names"][1] + " " + sentinels["phones"][0]
    job_id = queue.enqueue(conn, "test_fail", {"leak": leak}, max_attempts=3)
    proc = _start(_env(db_url, SLACK_ALERT_WEBHOOK_URL=url))
    try:
        _wait_for(lambda: _status(conn, job_id)["status"] == "failed")
        _wait_for(lambda: len(_FakeSlack.received) >= 1)
        time.sleep(0.5)
    finally:
        output = _stop(proc)
        server.shutdown()
    assert _status(conn, job_id)["attempts"] == 3
    assert len(_FakeSlack.received) == 1
    text = _FakeSlack.received[0]["text"]
    assert job_id in text and "test_fail" in text
    for s in (*sentinels["names"], *sentinels["phones"], url):
        assert s not in text
        assert s not in output, "worker output leaked a sentinel or the webhook URL"
    assert '"msg":"job failed"' in output


def test_worker_slots_run_jobs_in_parallel_and_all_stop_on_sigterm(conn, db_url):
    jobs = [queue.enqueue(conn, "test_sleep", {"sleep_s": 2}) for _ in range(3)]
    proc = _start(_env(db_url, WORKER_CONCURRENCY="3"))
    started = time.monotonic()
    try:
        _wait_for(lambda: all(_status(conn, j)["status"] == "succeeded" for j in jobs), timeout=15)
        elapsed = time.monotonic() - started
    finally:
        output = _stop(proc)
    assert elapsed < 5.5, f"3 jobs of 2 s took {elapsed:.1f} s: slots did not run in parallel"
    claimers = {_status(conn, j)["claimed_by"] for j in jobs}
    assert len(claimers) == 3 and all(c.rsplit("-", 1)[1].startswith("s") for c in claimers)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM worker_heartbeats")
        assert cur.fetchone()["n"] == 3
    conn.commit()
    assert proc.returncode == 0
    assert output.count('"msg":"worker stopped"') == 3


def test_the_load_check_script_passes_against_a_worker(conn, db_url):
    from tests.conftest import enable_systems

    enable_systems(db_url, "healthcheck")
    # The fake model: the healthcheck handler needs a model, so stand one up that echoes.
    import http.server

    class _Echo(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            nonce = body["messages"][-1]["content"]
            content = json.dumps({"ok": True, "echo": nonce})
            out = json.dumps(
                {
                    "model": "fake",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    "choices": [{"message": {"content": content}}],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    model_url = f"http://127.0.0.1:{server.server_port}/v1"
    proc = _start(_env(db_url, WORKER_CONCURRENCY="4", LOCAL_LLM_BASE_URL=model_url))
    try:
        env = {**_env(db_url), "DATABASE_URL": db_url}
        run = subprocess.run(
            [sys.executable, "scripts/load_check.py", "--jobs", "10", "--timeout", "60"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    finally:
        _stop(proc)
        server.shutdown()
    assert run.returncode == 0, run.stdout + run.stderr
    assert "result=PASS" in run.stdout and "succeeded=10" in run.stdout
    assert re.search(r"worker_slots_used=[2-4]", run.stdout)
    for line in run.stdout.splitlines():
        assert re.match(r"^[a-z_]+=[A-Za-z0-9_.]+$", line), line
