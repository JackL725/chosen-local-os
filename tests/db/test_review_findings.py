"""Regression tests for the independent review of Phase 1 (Oct 13). Each one reproduces
a hole the reviewer found with a real login, and now proves it is closed."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.db.test_phase1_schema import _connect_as, _standard, as_actor
from worker import bootstrap

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def raw(db_url: str):
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as c:
        yield c


def _active_coach(raw, items=("interview", "reference", "agreement")) -> str:
    as_actor(raw, "sam")
    coach = raw.execute(
        "INSERT INTO coaches (display_name, status) VALUES ('X', 'vetting') RETURNING id"
    ).fetchone()["id"]
    for key in items:
        raw.execute(
            """INSERT INTO vetting_items (coach_id, item_type, verified_by, verified_at)
               VALUES (%s, %s, (SELECT id FROM actors WHERE name = 'sam'), now())""",
            (coach, key),
        )
    raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))
    return str(coach)


# Finding 2: the worker login moved founder-verified items onto another coach.
def test_the_worker_cannot_move_verified_items_to_another_coach(raw, db_url):
    _standard(raw)
    x = _active_coach(raw)
    with _connect_as(raw, db_url, "ledger_worker") as w:
        as_actor(w, "worker")
        y = w.execute("INSERT INTO coaches (display_name) VALUES ('Y') RETURNING id").fetchone()[
            "id"
        ]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            w.execute("UPDATE vetting_items SET coach_id = %s WHERE coach_id = %s", (y, x))
    # Not even a founder may re-parent a vetting item.
    as_actor(raw, "sam")
    y2 = raw.execute(
        "INSERT INTO coaches (display_name, status) VALUES ('Z', 'vetting') RETURNING id"
    ).fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation, match="another coach"):
        raw.execute("UPDATE vetting_items SET coach_id = %s WHERE coach_id = %s", (y2, x))


# Finding 3: a never-active coach was activated under a retired standard; the worker
# could change statuses other than active.
def test_status_changes_are_founder_only(raw, db_url):
    _standard(raw)
    with _connect_as(raw, db_url, "ledger_worker") as w:
        as_actor(w, "worker")
        c = w.execute("INSERT INTO coaches (display_name) VALUES ('C') RETURNING id").fetchone()[
            "id"
        ]
        for status in ("vetting", "paused", "removed"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="founder"):
                w.execute("UPDATE coaches SET status = %s WHERE id = %s", (status, c))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="founder"):
            w.execute("INSERT INTO coaches (display_name, status) VALUES ('D', 'vetting')")


def test_only_a_coach_who_was_active_comes_back_under_a_retired_standard(raw):
    _standard(raw, version=1)
    as_actor(raw, "sam")
    coach = raw.execute(
        "INSERT INTO coaches (display_name, status) VALUES ('V', 'vetting') RETURNING id"
    ).fetchone()["id"]
    for key in ("interview", "reference", "agreement"):
        raw.execute(
            """INSERT INTO vetting_items (coach_id, item_type, verified_by, verified_at)
               VALUES (%s, %s, (SELECT id FROM actors WHERE name = 'sam'), now())""",
            (coach, key),
        )
    _standard(raw, items=("interview", "reference", "agreement", "background_check"), version=2)
    as_actor(raw, "sam")
    with pytest.raises(psycopg.errors.CheckViolation):
        raw.execute("UPDATE coaches SET status = 'paused' WHERE id = %s", (coach,))
    raw.execute("UPDATE coaches SET status = 'prospect' WHERE id = %s", (coach,))
    with pytest.raises(psycopg.errors.CheckViolation):
        raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))


# Finding 4: flipping `required` on and off turned a system on without a founder.
def test_a_non_founder_cannot_turn_a_system_on_through_required(raw):
    as_actor(raw, "worker")
    raw.execute("INSERT INTO systems (key, title, version) VALUES ('demo', 'Demo', 1)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="required"):
        raw.execute("UPDATE systems SET required = true WHERE key = 'demo'")
    row = raw.execute("SELECT enabled, required FROM systems WHERE key = 'demo'").fetchone()
    assert (row["enabled"], row["required"]) == (False, False)


def test_a_required_system_counts_as_on_without_its_flag(raw):
    as_actor(raw, "worker")
    raw.execute(
        "INSERT INTO systems (key, title, version, required) VALUES ('guard', 'Guard', 1, true)"
    )
    raw.execute(
        "INSERT INTO system_job_types (job_type, system_key) VALUES ('guard.check', 'guard')"
    )
    raw.execute("INSERT INTO jobs (job_type) VALUES ('guard.check')")
    health = raw.execute("SELECT * FROM api.queue_health()").fetchone()
    assert (health["pending_count"], health["paused_pending_count"]) == (1, 0)


# Finding 5: coach names were stored by value in event payloads.
def test_coach_contact_and_name_fields_never_enter_events(raw):
    as_actor(raw, "sam")
    raw.execute(
        """INSERT INTO coaches (display_name, website, home_area, postal_code, profile_summary)
           VALUES ('Coach Xavier Real', 'https://xavier.example', 'Highlands', '80211', 'Bio')"""
    )
    dump = str(raw.execute("SELECT payload FROM events WHERE entity_type = 'coaches'").fetchall())
    for s in ("Xavier", "xavier.example", "Highlands", "80211", "Bio"):
        assert s not in dump


# Finding 8: rerunning the bootstrap restored a draft item a founder had deleted.
def test_rerunning_the_bootstrap_keeps_a_founders_draft_edits(raw):
    sql = bootstrap.to_sql(bootstrap.load(ROOT / "deploy" / "business" / "chosen-local.toml"))
    raw.execute(sql)
    as_actor(raw, "sam")
    raw.execute("DELETE FROM vetting_item_types WHERE key = 'reference_2'")
    raw.execute(sql)
    keys = {r["key"] for r in raw.execute("SELECT key FROM vetting_item_types").fetchall()}
    assert "reference_2" not in keys and len(keys) == 5


# Finding 6: parallel wrong passwords lost count of failures.
def test_parallel_wrong_passwords_are_all_counted(db_url, monkeypatch):
    from review import admin, auth, passwords
    from worker.db import connect

    monkeypatch.setattr(passwords, "DEFAULT_LOG2_N", 12)
    with connect(db_url) as conn:
        admin.create_user(
            conn,
            "sam",
            tailscale_login="sam@t.test",
            allow_local=False,
            prompt=lambda _p: "correct horse battery",
        )
        conn.execute("SELECT set_config('app.actor', 'migration', false)")
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('console.max_failed_signins', '20')"
        )
        conn.commit()

    def attempt(_n: int) -> None:
        with psycopg.connect(db_url, row_factory=dict_row) as c:
            with pytest.raises(auth.SignInRefused):
                auth.sign_in(
                    c,
                    tailscale_login="sam@t.test",
                    name=None,
                    password="wrong password here",
                    allow_local=False,
                )

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        list(pool.map(attempt, range(8)))
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        assert (
            c.execute("SELECT failed_signins FROM console_users").fetchone()["failed_signins"] == 8
        )


# Re-check of the fixes: `required` itself was still writable by the worker.
def test_only_a_founder_changes_whether_a_system_is_required(raw, db_url):
    with _connect_as(raw, db_url, "ledger_worker") as w:
        as_actor(w, "worker")
        w.execute("INSERT INTO systems (key, title, version) VALUES ('demo', 'Demo', 1)")
        w.execute(
            "INSERT INTO systems (key, title, version, required) VALUES ('guard', 'Guard', 1, true)"
        )
        for sql in (
            "UPDATE systems SET required = true WHERE key = 'demo'",
            "UPDATE systems SET required = false WHERE key = 'guard'",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="required"):
                w.execute(sql)


def test_a_job_type_stays_with_the_system_that_registered_it(raw, db_url):
    with _connect_as(raw, db_url, "ledger_worker") as w:
        as_actor(w, "worker")
        w.execute("INSERT INTO systems (key, title, version) VALUES ('paused', 'P', 1)")
        w.execute(
            "INSERT INTO systems (key, title, version, required) VALUES ('mine', 'M', 1, true)"
        )
        w.execute(
            "INSERT INTO system_job_types (job_type, system_key) VALUES ('paused.work', 'paused')"
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="another system"):
            w.execute(
                "UPDATE system_job_types SET system_key = 'mine' WHERE job_type = 'paused.work'"
            )


def test_an_active_coach_cannot_change_standard_even_by_a_founder(raw):
    _standard(raw, version=1)
    coach = _active_coach(raw)
    v2 = _standard(raw, items=("interview",), version=2)
    as_actor(raw, "sam")
    with pytest.raises(psycopg.errors.CheckViolation, match="pause the coach"):
        raw.execute("UPDATE coaches SET vetting_standard_id = %s WHERE id = %s", (v2, coach))


def test_during_a_lockout_right_and_wrong_passwords_get_the_same_answer(db_url, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from review import admin, auth, passwords
    from worker.db import connect

    monkeypatch.setattr(passwords, "DEFAULT_LOG2_N", 10)
    with connect(db_url) as conn:
        admin.create_user(
            conn,
            "sam",
            tailscale_login="sam@t.test",
            allow_local=False,
            prompt=lambda _p: "correct horse battery",
        )
        user = conn.execute("SELECT * FROM console_users").fetchone()
        conn.commit()
    # Simulate guesses already past the lock check when a parallel guess locks the account.
    real_verify = passwords.verify

    def verify_then_lock(password, stored):
        ok = real_verify(password, stored)
        with psycopg.connect(db_url, autocommit=True) as c:
            c.execute("SELECT set_config('app.actor', 'review_console', false)")
            c.execute(
                "UPDATE console_users SET locked_until = %s WHERE id = %s",
                (datetime.now(UTC) + timedelta(minutes=15), user["id"]),
            )
        return ok

    monkeypatch.setattr(passwords, "verify", verify_then_lock)
    answers = []
    for pw in ("correct horse battery", "wrong password here"):
        with psycopg.connect(db_url, autocommit=True) as c:
            c.execute("SELECT set_config('app.actor', 'review_console', false)")
            c.execute("UPDATE console_users SET locked_until = NULL, failed_signins = 0")
        with psycopg.connect(db_url, row_factory=dict_row) as c:
            with pytest.raises(auth.SignInRefused) as exc:
                auth.sign_in(
                    c, tailscale_login="sam@t.test", name=None, password=pw, allow_local=False
                )
            answers.append(str(exc.value))
    assert answers[0] == answers[1] and "locked" in answers[0]
