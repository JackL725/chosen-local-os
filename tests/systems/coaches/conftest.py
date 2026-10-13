"""The console fixtures (a signed-in-able console against the test Ledger), plus a
launched need-state and a draft vetting standard in the test market."""

from __future__ import annotations

import psycopg
import pytest

from tests.review.conftest import *  # noqa: F403 - shared console fixtures

ITEMS = ("interview", "reference", "agreement")


@pytest.fixture
def ledger(db_url):
    from psycopg.rows import dict_row

    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture
def seeded(ledger, db_url):
    from tests.conftest import enable_systems

    enable_systems(db_url, "coaches")
    ledger.execute("SELECT set_config('app.actor', 'migration', false)")
    ledger.execute("INSERT INTO need_states (key, label) VALUES ('fitness', 'Fitness')")
    ledger.execute("INSERT INTO need_states (key, label) VALUES ('career', 'Career')")
    ledger.execute(
        "INSERT INTO market_need_states (market_key, need_state_key) VALUES ('testville', 'fitness')"
    )
    sid = ledger.execute(
        "INSERT INTO vetting_standards (version, title) VALUES (1, 'Draft') RETURNING id"
    ).fetchone()["id"]
    for n, key in enumerate(ITEMS):
        kind = "file" if key == "agreement" else "any"
        ledger.execute(
            """INSERT INTO vetting_item_types (standard_id, key, label, evidence_kind, sort_order)
               VALUES (%s, %s, %s, %s, %s)""",
            (sid, key, key.title(), kind, n),
        )
    ledger.execute("RESET app.actor")
    return str(sid)
