"""Ledger connections. Every transaction names its actor before it writes.

The Ledger's triggers refuse any write without `app.actor`, so `transaction()` is the
only way worker code should open a write transaction.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=False)


@contextmanager
def transaction(
    conn: psycopg.Connection,
    *,
    actor: str,
    job_id: str | None = None,
    prompt_version: str | None = None,
    model: str | None = None,
    approval_id: str | None = None,
) -> Iterator[psycopg.Connection]:
    """Open a transaction with the Ledger context set for its duration only."""
    with conn.transaction():
        settings = {
            "app.actor": actor,
            "app.job_id": job_id or "",
            "app.prompt_version": prompt_version or "",
            "app.model": model or "",
            "app.approval_id": approval_id or "",
        }
        with conn.cursor() as cur:
            for key, value in settings.items():
                cur.execute("SELECT set_config(%s, %s, true)", (key, value))
        yield conn
