"""Write the code's systems into the Ledger, so the database knows which job types
belong to which system and which systems are turned on.

Run by the worker (and the console) at start-up. Idempotent: rows change only when a
manifest's title or version changes. It never turns a system on: new systems arrive off
(required ones count as on), and a founder turns them on in the console. Whether a system
is required, and which system owns a job type, are set when they first register; changing
either later is a founder's or a migration's job, and the Ledger refuses anyone else.
"""

from __future__ import annotations

import psycopg

import systems
from worker.db import transaction


def sync_systems(conn: psycopg.Connection, *, actor: str = "worker") -> None:
    manifests = systems.all_manifests()
    with transaction(conn, actor=actor), conn.cursor() as cur:
        for key, m in manifests.items():
            cur.execute(
                """INSERT INTO systems (key, title, version, required)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (key) DO UPDATE
                      SET title = EXCLUDED.title, version = EXCLUDED.version
                    WHERE (systems.title, systems.version)
                          IS DISTINCT FROM (EXCLUDED.title, EXCLUDED.version)""",
                (key, m.title, m.version, m.required),
            )
        for job_type, key in systems.job_types().items():
            cur.execute(
                """INSERT INTO system_job_types (job_type, system_key) VALUES (%s, %s)
                   ON CONFLICT (job_type) DO NOTHING""",
                (job_type, key),
            )


def enabled_systems(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT key FROM systems WHERE enabled OR required")
        rows = cur.fetchall()
    conn.commit()
    return {r["key"] for r in rows}
