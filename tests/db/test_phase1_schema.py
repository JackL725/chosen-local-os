"""Migration 2 (docs/phase-1-design.md section 4): the rules the Ledger now enforces."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import TEST_MARKET
from worker import bootstrap

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "deploy" / "business" / "chosen-local.toml"
NORTHWIND = ROOT / "tests" / "fixtures" / "business" / "northwind.toml"
TEST_LOGIN_PASSWORD = "test-only-login"


def as_actor(conn: psycopg.Connection, actor: str) -> None:
    conn.execute("SELECT set_config('app.actor', %s, false)", (actor,))


@pytest.fixture
def raw(db_url: str):
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as c:
        yield c


def _login(raw, db_url: str, group: str) -> str:
    """A real login in `group`, like cl_worker or cl_console in production."""
    name = f"t_{group}"
    raw.execute(
        f"""DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN
                CREATE ROLE {name} LOGIN PASSWORD '{TEST_LOGIN_PASSWORD}';
              END IF;
            END $$"""
    )
    raw.execute(f"GRANT {group} TO {name}")
    u = urlparse(db_url)
    netloc = f"{name}:{TEST_LOGIN_PASSWORD}@{u.hostname}" + (f":{u.port}" if u.port else "")
    return urlunparse(u._replace(netloc=netloc))


def _connect_as(raw, db_url: str, group: str) -> psycopg.Connection:
    return psycopg.connect(_login(raw, db_url, group), autocommit=True, row_factory=dict_row)


# --------------------------------------------------------------- login -> actor guard
def test_the_worker_login_can_act_only_as_a_system(raw, db_url):
    with _connect_as(raw, db_url, "ledger_worker") as w:
        as_actor(w, "worker")
        w.execute("INSERT INTO coaches (display_name) VALUES ('Coach A')")
        for impostor in ("sam", "jack", "migration", "review_console"):
            as_actor(w, impostor)
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="may not act as"):
                w.execute("INSERT INTO coaches (display_name) VALUES ('Coach B')")


def test_the_console_login_acts_as_founders_but_not_as_systems(raw, db_url):
    with _connect_as(raw, db_url, "ledger_console") as c:
        for ok in ("sam", "jack", "review_console"):
            as_actor(c, ok)
            c.execute("INSERT INTO coaches (display_name) VALUES ('Coach')")
        for impostor in ("worker", "n8n", "migration"):
            as_actor(c, impostor)
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="may not act as"):
                c.execute("INSERT INTO coaches (display_name) VALUES ('Coach')")


def test_the_n8n_login_still_enqueues_through_the_api(raw, db_url):
    with _connect_as(raw, db_url, "ledger_n8n") as n:
        assert n.execute("SELECT api.enqueue_job('healthcheck')").fetchone()["enqueue_job"]
        health = n.execute("SELECT * FROM api.queue_health()").fetchone()
        assert health["pending_count"] == 1 and health["threshold_hours"] == 4


def test_a_worker_login_cannot_verify_vetting_or_activate_a_coach(raw, db_url):
    _standard(raw)
    coach = _coach_in_vetting(raw)
    with _connect_as(raw, db_url, "ledger_worker") as w:
        as_actor(w, "sam")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            w.execute(
                """UPDATE vetting_items SET verified_by = (SELECT id FROM actors WHERE name = 'sam'),
                          verified_at = now() WHERE coach_id = %s""",
                (coach,),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            w.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))


# --------------------------------------------------------------------- rules as data
@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO settings (key, value) VALUES ('core.stale_job_alert_hours', '2')",
        "INSERT INTO markets (key, name, time_zone) VALUES ('boulder', 'Boulder', 'America/Denver')",
        "INSERT INTO need_states (key, label) VALUES ('career', 'Career')",
        "INSERT INTO market_need_states (market_key, need_state_key) VALUES ('testville', 'career')",
        "INSERT INTO vetting_standards (version, title) VALUES (9, 'x')",
    ],
)
def test_business_rules_are_founder_only(raw, sql):
    as_actor(raw, "migration")
    raw.execute(
        "INSERT INTO need_states (key, label) VALUES ('career', 'Career') ON CONFLICT DO NOTHING"
    )
    as_actor(raw, "worker")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="founder"):
        raw.execute(sql)
    as_actor(raw, "sam")
    raw.execute(sql.replace("'career', 'Career'", "'dating', 'Dating'"))


def test_settings_record_who_changed_them_and_queue_health_reads_them(raw):
    as_actor(raw, "sam")
    raw.execute("INSERT INTO settings (key, value) VALUES ('core.stale_job_alert_hours', '2')")
    row = raw.execute(
        """SELECT a.name FROM settings s JOIN actors a ON a.id = s.changed_by
            WHERE s.key = 'core.stale_job_alert_hours'"""
    ).fetchone()
    assert row["name"] == "sam"
    as_actor(raw, "worker")
    raw.execute(
        "INSERT INTO jobs (job_type, run_after) VALUES ('healthcheck', now() - interval '130 minutes')"
    )
    health = raw.execute("SELECT * FROM api.queue_health()").fetchone()
    assert health["threshold_hours"] == 2 and health["is_stale"] and health["should_alert"]


def test_markets_reject_unknown_time_zones(raw):
    as_actor(raw, "sam")
    with pytest.raises(psycopg.errors.CheckViolation, match="time zone"):
        raw.execute("INSERT INTO markets (key, name, time_zone) VALUES ('x', 'X', 'Mars/Olympus')")


def test_one_active_market_is_the_default_and_two_need_an_explicit_market(raw):
    as_actor(raw, "worker")
    coach = raw.execute(
        "INSERT INTO coaches (display_name) VALUES ('A') RETURNING market_key"
    ).fetchone()
    assert coach["market_key"] == TEST_MARKET
    as_actor(raw, "sam")
    raw.execute(
        "INSERT INTO markets (key, name, time_zone) VALUES ('boulder', 'Boulder', 'America/Denver')"
    )
    as_actor(raw, "worker")
    with pytest.raises(psycopg.errors.NotNullViolation, match="market_key is required"):
        raw.execute("INSERT INTO coaches (display_name) VALUES ('B')")
    raw.execute("INSERT INTO coaches (display_name, market_key) VALUES ('B', 'boulder')")


# ----------------------------------------------------------------- systems registry
def test_systems_arrive_off_and_required_systems_cannot_be_turned_off(raw):
    as_actor(raw, "worker")
    raw.execute(
        "INSERT INTO systems (key, title, version, enabled) VALUES ('demo', 'Demo', 1, true)"
    )
    raw.execute(
        "INSERT INTO systems (key, title, version, required) VALUES ('guard', 'Guard', 1, true)"
    )
    rows = {
        r["key"]: r["enabled"] for r in raw.execute("SELECT key, enabled FROM systems").fetchall()
    }
    assert rows == {"demo": False, "guard": True}
    as_actor(raw, "sam")
    raw.execute("UPDATE systems SET enabled = true WHERE key = 'demo'")
    with pytest.raises(psycopg.errors.CheckViolation, match="required"):
        raw.execute("UPDATE systems SET enabled = false WHERE key = 'guard'")
    as_actor(raw, "worker")
    raw.execute("INSERT INTO jobs (job_type) VALUES ('demo.summarise')")
    with pytest.raises(psycopg.errors.CheckViolation):
        raw.execute("INSERT INTO jobs (job_type) VALUES ('demo.Bad.type')")


# ----------------------------------------------------------------- vetting standards
ITEMS = ["interview", "reference", "agreement"]


def _standard(raw, items=ITEMS, *, version: int = 1, activate: bool = True) -> str:
    as_actor(raw, "migration")
    sid = raw.execute(
        "INSERT INTO vetting_standards (version, title) VALUES (%s, 'Standard') RETURNING id",
        (version,),
    ).fetchone()["id"]
    for n, key in enumerate(items):
        raw.execute(
            "INSERT INTO vetting_item_types (standard_id, key, label, sort_order) VALUES (%s, %s, %s, %s)",
            (sid, key, key.title(), n),
        )
    if activate:
        as_actor(raw, "jack")
        raw.execute("UPDATE vetting_standards SET status = 'retired' WHERE status = 'active'")
        raw.execute("UPDATE vetting_standards SET status = 'active' WHERE id = %s", (sid,))
    return sid


def _coach_in_vetting(raw, *, verify: tuple[str, ...] = ()) -> str:
    as_actor(raw, "sam")
    coach = raw.execute(
        "INSERT INTO coaches (display_name, status) VALUES ('Coach', 'vetting') RETURNING id"
    ).fetchone()["id"]
    std = raw.execute("SELECT vetting_standard_id FROM coaches WHERE id = %s", (coach,)).fetchone()
    keys = raw.execute(
        "SELECT key FROM vetting_item_types WHERE standard_id = %s", (std["vetting_standard_id"],)
    ).fetchall()
    for k in keys:
        raw.execute(
            "INSERT INTO vetting_items (coach_id, item_type) VALUES (%s, %s)", (coach, k["key"])
        )
    for k in verify:
        raw.execute(
            """UPDATE vetting_items SET verified_by = (SELECT id FROM actors WHERE name = 'sam'),
                      verified_at = now() WHERE coach_id = %s AND item_type = %s""",
            (coach, k),
        )
    return coach


def test_a_standard_is_editable_as_a_draft_and_fixed_once_active(raw):
    sid = _standard(raw, activate=False)
    as_actor(raw, "migration")
    raw.execute("UPDATE vetting_item_types SET label = 'Founder interview' WHERE key = 'interview'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only a founder can activate"):
        raw.execute("UPDATE vetting_standards SET status = 'active' WHERE id = %s", (sid,))
    as_actor(raw, "sam")
    raw.execute("UPDATE vetting_standards SET status = 'active' WHERE id = %s", (sid,))
    row = raw.execute(
        """SELECT s.activated_at, a.name FROM vetting_standards s JOIN actors a ON a.id = s.activated_by
            WHERE s.id = %s""",
        (sid,),
    ).fetchone()
    assert row["name"] == "sam" and row["activated_at"] is not None
    for sql in (
        "UPDATE vetting_item_types SET label = 'x'",
        "DELETE FROM vetting_item_types",
        f"INSERT INTO vetting_item_types (standard_id, key, label) VALUES ('{sid}', 'more', 'More')",
        "UPDATE vetting_standards SET title = 'changed'",
        "UPDATE vetting_standards SET status = 'draft'",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            raw.execute(sql)


def test_only_one_standard_is_active_and_an_empty_one_cannot_be(raw):
    _standard(raw, version=1)
    second = _standard(raw, version=2, activate=False)
    as_actor(raw, "sam")
    with pytest.raises(psycopg.errors.UniqueViolation):
        raw.execute("UPDATE vetting_standards SET status = 'active' WHERE id = %s", (second,))
    empty = _standard(raw, items=[], version=3, activate=False)
    as_actor(raw, "sam")
    with pytest.raises(psycopg.errors.CheckViolation, match="at least one required item"):
        raw.execute("UPDATE vetting_standards SET status = 'active' WHERE id = %s", (empty,))


def test_coaches_can_be_vetted_under_a_draft_but_not_activated(raw):
    _standard(raw, activate=False)
    coach = _coach_in_vetting(raw, verify=tuple(ITEMS))
    with pytest.raises(psycopg.errors.CheckViolation, match="standard is not active"):
        raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))
    as_actor(raw, "jack")
    raw.execute("UPDATE vetting_standards SET status = 'active'")
    raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))


def test_a_new_standard_leaves_active_coaches_alone_and_paused_ones_can_return(raw):
    _standard(raw, version=1)
    coach = _coach_in_vetting(raw, verify=tuple(ITEMS))
    raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))
    _standard(raw, items=[*ITEMS, "background_check"], version=2)  # retires version 1
    as_actor(raw, "sam")
    assert (
        raw.execute("SELECT status FROM coaches WHERE id = %s", (coach,)).fetchone()["status"]
        == "active"
    )
    raw.execute("UPDATE coaches SET status = 'paused' WHERE id = %s", (coach,))
    raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))
    # A coach still in vetting under the retired standard must move to the new one first.
    _standard(raw, version=3)
    as_actor(raw, "sam")
    with pytest.raises(psycopg.errors.CheckViolation):
        raw.execute(
            "INSERT INTO vetting_items (coach_id, item_type) VALUES (%s, 'background_check')",
            (coach,),
        )


def test_only_a_founder_moves_a_coach_to_another_standard(raw):
    _standard(raw, version=1)
    coach = _coach_in_vetting(raw)
    new = _standard(raw, version=2)
    as_actor(raw, "worker")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="another vetting standard"):
        raw.execute("UPDATE coaches SET vetting_standard_id = %s WHERE id = %s", (new, coach))
    as_actor(raw, "sam")
    raw.execute("UPDATE coaches SET vetting_standard_id = %s WHERE id = %s", (new, coach))


def test_vetting_items_must_belong_to_the_coachs_standard(raw):
    _standard(raw)
    as_actor(raw, "sam")
    coach = raw.execute("INSERT INTO coaches (display_name) VALUES ('C') RETURNING id").fetchone()[
        "id"
    ]
    with pytest.raises(psycopg.errors.CheckViolation, match="no vetting standard yet"):
        raw.execute(
            "INSERT INTO vetting_items (coach_id, item_type) VALUES (%s, 'interview')", (coach,)
        )
    raw.execute("UPDATE coaches SET status = 'vetting' WHERE id = %s", (coach,))
    with pytest.raises(psycopg.errors.CheckViolation, match="not part of"):
        raw.execute(
            "INSERT INTO vetting_items (coach_id, item_type) VALUES (%s, 'astrology')", (coach,)
        )
    row = raw.execute(
        "INSERT INTO vetting_items (coach_id, item_type) VALUES (%s, 'interview') RETURNING item_type_id",
        (coach,),
    ).fetchone()
    assert row["item_type_id"] is not None


# ------------------------------------------------------------------- vetting evidence
def test_evidence_is_founder_only_never_edited_and_never_in_events(raw, sentinels):
    _standard(raw)
    coach = _coach_in_vetting(raw)
    item = raw.execute(
        "SELECT id FROM vetting_items WHERE coach_id = %s LIMIT 1", (coach,)
    ).fetchone()["id"]
    secret_name = sentinels["names"][0]
    as_actor(raw, "worker")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw.execute(
            "INSERT INTO vetting_evidence (vetting_item_id, kind, note, uploaded_by) "
            "VALUES (%s, 'note', 'x', (SELECT id FROM actors WHERE name = 'worker'))",
            (item,),
        )
    as_actor(raw, "sam")
    with pytest.raises(psycopg.errors.CheckViolation):
        raw.execute(
            """INSERT INTO vetting_evidence (vetting_item_id, kind, relative_path, sha256, size_bytes, uploaded_by)
               VALUES (%s, 'file', '../../etc/passwd', repeat('a', 64), 1,
                       (SELECT id FROM actors WHERE name = 'sam'))""",
            (item,),
        )
    ev = raw.execute(
        """INSERT INTO vetting_evidence
               (vetting_item_id, kind, relative_path, display_name, sha256, size_bytes, uploaded_by)
           VALUES (%s, 'file', %s, %s, repeat('b', 64), 10, (SELECT id FROM actors WHERE name = 'worker'))
           RETURNING id, uploaded_by""",
        (item, f"coaches/{coach}/{item}/abc.pdf", f"Reference from {secret_name}.pdf"),
    ).fetchone()
    sam = raw.execute("SELECT id FROM actors WHERE name = 'sam'").fetchone()["id"]
    assert ev["uploaded_by"] == sam  # stamped with the real actor, whatever the writer claims
    with pytest.raises(psycopg.errors.CheckViolation, match="never edited"):
        raw.execute("UPDATE vetting_evidence SET size_bytes = 11 WHERE id = %s", (ev["id"],))
    raw.execute("UPDATE vetting_evidence SET removed_at = now() WHERE id = %s", (ev["id"],))
    dump = str(
        raw.execute("SELECT payload FROM events WHERE entity_type = 'vetting_evidence'").fetchall()
    )
    assert secret_name not in dump and "abc.pdf" not in dump


# -------------------------------------------------------------------------- bootstrap
def _counts(raw) -> dict[str, int]:
    tables = (
        "actors",
        "markets",
        "need_states",
        "market_need_states",
        "vetting_standards",
        "vetting_item_types",
        "settings",
    )
    return {t: raw.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in tables}


def test_the_chosen_local_profile_bootstraps_idempotently(raw):
    sql = bootstrap.to_sql(bootstrap.load(PROFILE))
    raw.execute(sql)
    first = _counts(raw)
    as_actor(raw, "sam")
    raw.execute("UPDATE need_states SET label = 'Fitness' WHERE key = 'fitness'")
    raw.execute(sql)
    assert _counts(raw) == first
    assert (
        raw.execute("SELECT label FROM need_states WHERE key = 'fitness'").fetchone()["label"]
        == "Fitness"
    )
    founders = raw.execute(
        "SELECT name FROM actors WHERE kind = 'founder' AND active ORDER BY name"
    ).fetchall()
    assert [f["name"] for f in founders] == ["jack", "sam"]
    launched = raw.execute(
        "SELECT need_state_key FROM market_need_states WHERE market_key = 'denver' AND launched ORDER BY 1"
    ).fetchall()
    assert [r["need_state_key"] for r in launched] == ["fitness", "life_direction"]
    std = raw.execute(
        """SELECT s.status, count(t.id) AS items FROM vetting_standards s
             JOIN vetting_item_types t ON t.standard_id = s.id GROUP BY s.status"""
    ).fetchone()
    assert (std["status"], std["items"]) == ("draft", 6)


def test_a_made_up_business_runs_the_core_flow(raw):
    raw.execute(bootstrap.to_sql(bootstrap.load(NORTHWIND)))
    as_actor(raw, "sam")  # the first migration's founders are retired by this profile
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="inactive"):
        raw.execute("UPDATE markets SET name = 'x'")
    as_actor(raw, "ada")
    raw.execute("UPDATE markets SET active = false WHERE key = %s", (TEST_MARKET,))
    raw.execute("UPDATE vetting_standards SET status = 'active' WHERE version = 1")
    coach = raw.execute(
        "INSERT INTO coaches (display_name, status) VALUES ('Mentor', 'vetting') RETURNING id, market_key"
    ).fetchone()
    assert coach["market_key"] == "portland"
    for key in ("interview", "contract"):
        raw.execute(
            "INSERT INTO vetting_items (coach_id, item_type) VALUES (%s, %s)", (coach["id"], key)
        )
    raw.execute(
        "INSERT INTO coach_need_states (coach_id, need_state_key) VALUES (%s, 'career')",
        (coach["id"],),
    )
    as_actor(raw, "lin")
    raw.execute(
        """UPDATE vetting_items SET verified_by = (SELECT id FROM actors WHERE name = 'lin'),
                  verified_at = now() WHERE coach_id = %s""",
        (coach["id"],),
    )
    raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach["id"],))
    name = raw.execute("SELECT value FROM settings WHERE key = 'core.business_name'").fetchone()[
        "value"
    ]
    assert name == "Northwind Mentors"


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"founders": [{"name": "worker"}]}, "reserved"),
        ({"founders": []}, "founders"),
        ({"markets": [{"key": "Denver", "name": "D", "time_zone": "America/Denver"}]}, "market"),
        ({"need_states": [{"key": "x", "label": "X", "launch_in": ["mars"]}]}, "unknown markets"),
    ],
)
def test_bad_profiles_are_refused(patch, message):
    profile = bootstrap.load(PROFILE)
    profile.update(patch)
    with pytest.raises(bootstrap.ProfileError, match=message):
        bootstrap.validate(profile)


def test_profile_text_is_quoted_safely(raw):
    profile = bootstrap.load(NORTHWIND)
    profile["business"]["name"] = "O'Brien; DROP TABLE events; --"
    raw.execute(bootstrap.to_sql(profile))
    value = raw.execute("SELECT value FROM settings WHERE key = 'core.business_name'").fetchone()[
        "value"
    ]
    assert value == "O'Brien; DROP TABLE events; --"


def test_business_settings_round_trip_through_the_ledger(db_url):
    from worker import business_settings as bs
    from worker.db import connect, transaction

    with connect(db_url) as c:
        assert bs.get(c, "core.stale_job_alert_hours") == 4
        with transaction(c, actor="jack"):
            assert bs.put(c, "core.stale_job_alert_hours", "6") == 6
        assert bs.current(c)["core.stale_job_alert_hours"] == 6
        with pytest.raises(bs.InvalidSetting):
            bs.put(c, "core.stale_job_alert_hours", "500")
        c.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with transaction(c, actor="worker"):
                bs.put(c, "core.stale_job_alert_hours", "5")
