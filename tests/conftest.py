"""Shared fixtures.

Database tests need TEST_DATABASE_URL pointing at a disposable Postgres 16 database
whose name ends in `_test`. Each test gets a fresh schema built from the migrations,
so tests never depend on each other. Without TEST_DATABASE_URL they are skipped.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"
SYNTHETIC = ROOT / "evals" / "synthetic"


def migration_up_sql() -> list[str]:
    parts = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        up = text.split("-- migrate:up", 1)[1].split("-- migrate:down", 1)[0]
        parts.append(up)
    return parts


def _test_db_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return None
    name = (urlparse(url).path or "").lstrip("/")
    if not re.search(r"_test$", name):
        raise RuntimeError("TEST_DATABASE_URL must name a database ending in _test")
    return url


@pytest.fixture
def db_url() -> Iterator[str]:
    """A freshly migrated test database. Yields its URL."""
    url = _test_db_url()
    if url is None:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS api CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        for sql in migration_up_sql():
            conn.execute(sql)
    yield url


@pytest.fixture
def conn(db_url: str):
    from worker.db import connect

    c = connect(db_url)
    yield c
    c.close()


@pytest.fixture
def settings_factory(tmp_path: Path):
    from worker.config import Settings

    def make(**overrides):
        values = {
            "database_url": overrides.pop("database_url", "postgres://unused@127.0.0.1/none_test"),
            "private_data_dir": SYNTHETIC,
            "worker_id": "test-worker",
            "worker_poll_interval_s": 0.05,
            "worker_heartbeat_s": 0.2,
            "worker_reap_interval_s": 0.1,
            "worker_retry_base_s": 0.01,
            "worker_enable_test_jobs": True,
        }
        values.update(overrides)
        return Settings(**values)

    return make


@pytest.fixture
def sentinels() -> dict[str, list[str]]:
    import json

    return json.loads((SYNTHETIC / "sentinels.json").read_text(encoding="utf-8"))
