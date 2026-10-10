"""Phase 0 acceptance: a healthcheck job goes through the REAL local model in < 30 s.

Runs only on the workstation, with llama-server up and a dev database:
    RUN_LOCAL_MODEL_TESTS=1 TEST_DATABASE_URL=postgres://cl:devpassword@localhost:5432/chosen_local_test \
        uv run pytest tests/integration -m local_model
CI skips it (no model server there).
"""

from __future__ import annotations

import os
import time

import pytest

from worker import queue
from worker.local_llm import LocalLLM
from worker.main import Worker

pytestmark = [
    pytest.mark.local_model,
    pytest.mark.db,
    pytest.mark.skipif(
        os.environ.get("RUN_LOCAL_MODEL_TESTS") != "1",
        reason="set RUN_LOCAL_MODEL_TESTS=1 with llama-server running",
    ),
]


def test_healthcheck_through_local_model_under_30s(conn, db_url, settings_factory):
    base = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    model = os.environ.get("LOCAL_LLM_MODEL", "qwen3.6-27b-q4_k_m")
    settings = settings_factory(database_url=db_url, local_llm_base_url=base, local_llm_model=model)
    llm = LocalLLM(base, model, timeout_s=30)
    alerts: list[str] = []
    job_id = queue.enqueue(conn, "healthcheck", {"nonce": "Phase0Check42"})
    started = time.monotonic()
    try:
        Worker(settings, llm, alerts.append).run(idle_exit=True)
    finally:
        llm.close()
    elapsed = time.monotonic() - started
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
        status = cur.fetchone()["status"]
        cur.execute(
            "SELECT count(*) AS n FROM events WHERE event_type = 'job.succeeded' AND entity_id = %s",
            (job_id,),
        )
        events = cur.fetchone()["n"]
    conn.commit()
    assert status == "succeeded", alerts
    assert events == 1
    assert elapsed < 30, f"took {elapsed:.1f}s"
