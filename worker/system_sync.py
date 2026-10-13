"""Write the code's systems into the Ledger, so the database knows which job types
belong to which system and which systems are turned on.

Run by the worker (and the console) at start-up. Idempotent: rows change only when
a manifest's title, version or `required` changes. It never turns a system on; new
systems arrive off unless required, and a founder turns them on in the console.
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
                      SET title = EXCLUDED.title, version = EXCLUDED.version,
                          required = EXCLUDED.required
                    WHERE (systems.title, systems.version, systems.required)
                          IS DISTINCT FROM (EXCLUDED.title, EXCLUDED.version, EXCLUDED.required)""",
                (key, m.title, m.version, m.required),
            )
        for job_type, key in systems.job_types().items():
            cur.execute(
                """INSERT INTO system_job_types (job_type, system_key) VALUES (%s, %s)
                   ON CONFLICT (job_type) DO UPDATE SET system_key = EXCLUDED.system_key
                    WHERE system_job_types.system_key IS DISTINCT FROM EXCLUDED.system_key""",
                (job_type, key),
            )


def enabled_systems(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT key FROM systems WHERE enabled")
        rows = cur.fetchall()
    conn.commit()
    return {r["key"] for r in rows}
