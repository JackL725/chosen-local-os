"""The Ledger enforces its own rules, whichever tool writes."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

pytestmark = pytest.mark.db


def as_actor(conn: psycopg.Connection, actor: str) -> None:
    conn.execute("SELECT set_config('app.actor', %s, false)", (actor,))


@pytest.fixture
def raw(db_url: str):
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as c:
        yield c


EXPECTED_TABLES = {
    "actors",
    "need_states",
    "leads",
    "consent_records",
    "bookings",
    "events",
    "jobs",
    "worker_heartbeats",
    "approvals",
    "calls",
    "call_extractions",
    "safety_flags",
    "coaches",
    "coach_need_states",
    "vetting_items",
    "matches",
    "match_candidates",
    "fit_calls",
    "outcomes",
    "commissions",
    "commission_adjustments",
    "templates",
    "outbound_messages",
    "ads",
    "ad_daily",
}


def test_all_tables_exist_with_timestamps(raw):
    rows = raw.execute(
        """SELECT table_name,
                  bool_or(column_name = 'created_at') AS c, bool_or(column_name = 'updated_at') AS u
             FROM information_schema.columns WHERE table_schema = 'public'
            GROUP BY table_name"""
    ).fetchall()
    tables = {r["table_name"]: r for r in rows}
    assert EXPECTED_TABLES <= set(tables)
    for name in EXPECTED_TABLES:
        assert tables[name]["c"] and tables[name]["u"], name


def test_writes_without_an_actor_are_refused(raw):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match=r"app\.actor"):
        raw.execute("INSERT INTO need_states (key, label) VALUES ('career', 'Career')")
    as_actor(raw, "nobody")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="unknown"):
        raw.execute("INSERT INTO need_states (key, label) VALUES ('career', 'Career')")


def test_every_change_writes_an_event_without_sensitive_values(raw, sentinels):
    as_actor(raw, "n8n")
    name, phone, email = sentinels["names"][0], sentinels["phones"][0], sentinels["emails"][0]
    lead = raw.execute(
        """INSERT INTO leads (first_name, phone_e164, email, source_platform, intake_note)
           VALUES (%s, %s, %s, 'test', %s) RETURNING id""",
        (name, phone, email, sentinels["transcript_sentences"][0]),
    ).fetchone()["id"]
    raw.execute("UPDATE leads SET status = 'booked' WHERE id = %s", (lead,))
    raw.execute("UPDATE leads SET status = 'booked' WHERE id = %s", (lead,))  # no-op: no event
    events = raw.execute(
        """SELECT e.event_type, e.payload, a.name AS actor FROM events e
             JOIN actors a ON a.id = e.actor_id
            WHERE entity_type = 'leads' AND entity_id = %s ORDER BY e.id""",
        (str(lead),),
    ).fetchall()
    assert [e["event_type"] for e in events] == ["row.insert", "row.update"]
    assert all(e["actor"] == "n8n" for e in events)
    insert = events[0]["payload"]
    assert {"first_name", "phone_e164", "email"} <= set(insert["sensitive_changed"])
    assert insert["values"]["source_platform"] == "test"
    assert events[1]["payload"]["values"] == {"status": "booked"}
    dump = str([e["payload"] for e in events])
    for s in (name, phone, email, sentinels["transcript_sentences"][0]):
        assert s not in dump


def test_events_and_consent_are_append_only_even_for_the_owner(raw):
    as_actor(raw, "n8n")
    lead = raw.execute(
        "INSERT INTO leads (source_platform) VALUES ('test') RETURNING id"
    ).fetchone()["id"]
    raw.execute(
        """INSERT INTO consent_records (lead_id, channel, action, source)
                   VALUES (%s, 'text', 'granted', 'intake_form')""",
        (lead,),
    )
    for sql in (
        "UPDATE events SET event_type = 'x'",
        "DELETE FROM events",
        "UPDATE consent_records SET action = 'revoked'",
        "DELETE FROM consent_records",
        "TRUNCATE events CASCADE",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            raw.execute(sql)


def test_role_privileges(raw):
    raw.execute("SET ROLE ledger_n8n")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw.execute("SELECT count(*) FROM public.leads")
    job = raw.execute("SELECT api.enqueue_job('healthcheck', '{\"nonce\": \"abc123\"}')").fetchone()
    assert job["enqueue_job"] is not None
    again = raw.execute(
        "SELECT api.enqueue_job('healthcheck', '{}', NULL, NULL, 'key-1') AS a, "
        "api.enqueue_job('healthcheck', '{}', NULL, NULL, 'key-1') AS b"
    ).fetchone()
    assert again["a"] == again["b"]
    health = raw.execute("SELECT * FROM api.queue_health()").fetchone()
    assert health["pending_count"] == 2 and health["is_stale"] is False
    assert health["should_alert"] is False
    raw.execute("RESET ROLE")
    raw.execute("SET ROLE ledger_worker")
    as_actor(raw, "worker")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw.execute("UPDATE events SET model = 'x'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw.execute("INSERT INTO actors (kind, name) VALUES ('founder', 'mallory')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw.execute("DELETE FROM leads")
    raw.execute("RESET ROLE")
    raw.execute("SET ROLE ledger_reporting")
    raw.execute("SELECT count(*) FROM events")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw.execute("UPDATE jobs SET priority = 1")
    raw.execute("RESET ROLE")


def _coach(raw) -> str:
    as_actor(raw, "worker")
    return raw.execute(
        "INSERT INTO coaches (display_name) VALUES ('Test Coach') RETURNING id"
    ).fetchone()["id"]


ITEMS = [
    "interview",
    "reference_1",
    "reference_2",
    "paying_client_evidence",
    "program_and_price",
    "signed_agreement",
]


def _standard(raw, items=ITEMS, *, activate: bool = True) -> str:
    """A vetting standard holding the given item keys, activated by a founder."""
    as_actor(raw, "migration")
    version = raw.execute(
        "SELECT coalesce(max(version), 0) + 1 AS v FROM vetting_standards"
    ).fetchone()["v"]
    sid = raw.execute(
        "INSERT INTO vetting_standards (version, title) VALUES (%s, 'Test standard') RETURNING id",
        (version,),
    ).fetchone()["id"]
    for n, key in enumerate(items):
        raw.execute(
            """INSERT INTO vetting_item_types (standard_id, key, label, sort_order)
               VALUES (%s, %s, %s, %s)""",
            (sid, key, key.replace("_", " "), n),
        )
    if activate:
        as_actor(raw, "jack")
        raw.execute("UPDATE vetting_standards SET status = 'retired' WHERE status = 'active'")
        raw.execute("UPDATE vetting_standards SET status = 'active' WHERE id = %s", (sid,))
    return sid


def test_coach_cannot_go_active_until_every_item_is_founder_verified(raw):
    _standard(raw)
    coach = _coach(raw)
    as_actor(raw, "worker")
    raw.execute("UPDATE coaches SET status = 'vetting' WHERE id = %s", (coach,))
    as_actor(raw, "sam")
    for item in ITEMS[:-1]:
        raw.execute(
            """INSERT INTO vetting_items (coach_id, item_type, verified_by, verified_at)
                       SELECT %s, %s, id, now() FROM actors WHERE name = 'sam'""",
            (coach, item),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="signed_agreement"):
        raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))
    # A system cannot verify an item, and cannot verify on a founder's behalf.
    as_actor(raw, "worker")
    with pytest.raises(psycopg.errors.CheckViolation):
        raw.execute(
            """INSERT INTO vetting_items (coach_id, item_type, verified_by, verified_at)
                       SELECT %s, 'signed_agreement', id, now() FROM actors WHERE name = 'worker'""",
            (coach,),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        raw.execute(
            """INSERT INTO vetting_items (coach_id, item_type, verified_by, verified_at)
                       SELECT %s, 'signed_agreement', id, now() FROM actors WHERE name = 'sam'""",
            (coach,),
        )
    as_actor(raw, "jack")
    raw.execute(
        """INSERT INTO vetting_items (coach_id, item_type, verified_by, verified_at)
                   SELECT %s, 'signed_agreement', id, now() FROM actors WHERE name = 'jack'""",
        (coach,),
    )
    as_actor(raw, "worker")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="founder"):
        raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))
    as_actor(raw, "sam")
    raw.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (coach,))
    row = raw.execute(
        """SELECT c.status, a.name FROM coaches c JOIN actors a ON a.id = c.status_changed_by
                          WHERE c.id = %s""",
        (coach,),
    ).fetchone()
    assert (row["status"], row["name"]) == ("active", "sam")


def test_only_a_founder_clears_a_safety_hold(raw):
    as_actor(raw, "worker")
    lead = raw.execute(
        "INSERT INTO leads (source_platform) VALUES ('test') RETURNING id"
    ).fetchone()["id"]
    raw.execute("UPDATE leads SET safety_hold = true WHERE id = %s", (lead,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="founder"):
        raw.execute("UPDATE leads SET safety_hold = false WHERE id = %s", (lead,))
    as_actor(raw, "sam")
    raw.execute("UPDATE leads SET safety_hold = false WHERE id = %s", (lead,))
    row = raw.execute(
        """SELECT s.name AS set_by, c.name AS cleared_by FROM leads l
                           JOIN actors s ON s.id = l.safety_hold_set_by
                           JOIN actors c ON c.id = l.safety_hold_cleared_by WHERE l.id = %s""",
        (lead,),
    ).fetchone()
    assert (row["set_by"], row["cleared_by"]) == ("worker", "sam")


def _template(raw, status: str = "draft") -> str:
    as_actor(raw, "jack")
    return raw.execute(
        """INSERT INTO templates (key, version, channel, audience, body, status, approved_by, approved_at)
           SELECT 'booking-link-sms', 1, 'sms', 'prospect', 'Hi {first_name}', %s,
                  CASE WHEN %s = 'draft' THEN NULL ELSE id END,
                  CASE WHEN %s = 'draft' THEN NULL ELSE now() END
             FROM actors WHERE name = 'jack' RETURNING id""",
        (status, status, status),
    ).fetchone()["id"]


def test_approved_template_is_immutable(raw):
    tpl = _template(raw, "approved")
    with pytest.raises(psycopg.errors.CheckViolation, match="immutable"):
        raw.execute("UPDATE templates SET body = 'changed' WHERE id = %s", (tpl,))
    raw.execute("UPDATE templates SET status = 'retired' WHERE id = %s", (tpl,))


def test_ledger_refuses_to_record_an_ungated_send(raw):
    draft = _template(raw, "draft")
    as_actor(raw, "n8n")
    lead = raw.execute(
        "INSERT INTO leads (source_platform, consent_text) VALUES ('test', true) RETURNING id"
    ).fetchone()["id"]
    send = """INSERT INTO outbound_messages (recipient_type, lead_id, channel, template_id)
              VALUES ('lead', %s, 'sms', %s)"""
    with pytest.raises(psycopg.errors.CheckViolation, match="not approved"):
        raw.execute(send, (lead, draft))
    as_actor(raw, "jack")
    raw.execute(
        """UPDATE templates SET status = 'approved', approved_by = a.id, approved_at = now()
                     FROM actors a WHERE a.name = 'jack' AND templates.id = %s""",
        (draft,),
    )
    as_actor(raw, "n8n")
    raw.execute(send, (lead, draft))
    raw.execute("UPDATE leads SET safety_hold = true WHERE id = %s", (lead,))
    with pytest.raises(psycopg.errors.CheckViolation, match="safety hold"):
        raw.execute(send, (lead, draft))
    raw.execute(
        """INSERT INTO outbound_messages (recipient_type, lead_id, channel, template_id, status,
                                                  refusal_reason)
                   VALUES ('lead', %s, 'sms', %s, 'refused', 'safety_hold')""",
        (lead, draft),
    )
    as_actor(raw, "sam")
    raw.execute("UPDATE leads SET safety_hold = false, consent_text = false WHERE id = %s", (lead,))
    as_actor(raw, "n8n")
    with pytest.raises(psycopg.errors.CheckViolation, match="consented"):
        raw.execute(send, (lead, draft))


def test_choosing_a_lower_ranked_coach_needs_a_reason(raw):
    c1, c2 = _coach(raw), _coach(raw)
    lead = raw.execute(
        "INSERT INTO leads (source_platform) VALUES ('test') RETURNING id"
    ).fetchone()["id"]
    match = raw.execute(
        "INSERT INTO matches (lead_id, run) VALUES (%s, 'post_call') RETURNING id", (lead,)
    ).fetchone()["id"]
    raw.execute(
        """INSERT INTO match_candidates (match_id, coach_id, passed_filters, rank)
                   VALUES (%s, %s, true, 1), (%s, %s, true, 2)""",
        (match, c1, match, c2),
    )
    as_actor(raw, "sam")
    confirm = """UPDATE matches SET status = 'confirmed', chosen_coach_id = %s, confirmed_by = a.id,
                        confirmed_at = now(), override_reason = %s
                   FROM actors a WHERE a.name = 'sam' AND matches.id = %s"""
    with pytest.raises(psycopg.errors.CheckViolation, match="override_reason"):
        raw.execute(confirm, (c2, None, match))
    raw.execute(confirm, (c2, "prospect asked for an evening schedule", match))


def test_commission_leaves_draft_only_with_an_approval(raw):
    coach = _coach(raw)
    lead = raw.execute(
        "INSERT INTO leads (source_platform) VALUES ('test') RETURNING id"
    ).fetchone()["id"]
    match = raw.execute(
        "INSERT INTO matches (lead_id, run) VALUES (%s, 'post_call') RETURNING id", (lead,)
    ).fetchone()["id"]
    fit = raw.execute(
        "INSERT INTO fit_calls (match_id, lead_id, coach_id) VALUES (%s, %s, %s) RETURNING id",
        (match, lead, coach),
    ).fetchone()["id"]
    outcome = raw.execute(
        """INSERT INTO outcomes (fit_call_id, lead_id, coach_id, result)
                             VALUES (%s, %s, %s, 'closed') RETURNING id""",
        (fit, lead, coach),
    ).fetchone()["id"]
    comm = raw.execute(
        """INSERT INTO commissions (outcome_id, coach_id, amount_cents)
                          VALUES (%s, %s, 150000) RETURNING id""",
        (outcome, coach),
    ).fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        raw.execute("UPDATE commissions SET status = 'approved' WHERE id = %s", (comm,))
    approval = raw.execute(
        """INSERT INTO approvals (kind, subject_type, subject_id, proposed_by)
           SELECT 'invoice', 'commissions', %s, id FROM actors WHERE name = 'worker' RETURNING id""",
        (comm,),
    ).fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation, match="founder"):
        raw.execute(
            """UPDATE approvals SET decision = 'approved', decided_by = a.id, decided_at = now()
                         FROM actors a WHERE a.name = 'worker' AND approvals.id = %s""",
            (approval,),
        )
    as_actor(raw, "jack")
    raw.execute(
        """UPDATE approvals SET decision = 'approved', decided_by = a.id, decided_at = now()
                     FROM actors a WHERE a.name = 'jack' AND approvals.id = %s""",
        (approval,),
    )
    raw.execute(
        "UPDATE commissions SET status = 'approved', approval_id = %s WHERE id = %s",
        (approval, comm),
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="cannot change"):
        raw.execute("UPDATE approvals SET decision = 'rejected' WHERE id = %s", (approval,))


def test_call_paths_must_be_relative_to_private_data_dir(raw):
    as_actor(raw, "worker")
    lead = raw.execute(
        "INSERT INTO leads (source_platform) VALUES ('test') RETURNING id"
    ).fetchone()["id"]
    for bad in ("/home/clworker/cl-private/x.wav", "../etc/passwd", "calls/../../x"):
        with pytest.raises(psycopg.errors.CheckViolation):
            raw.execute("INSERT INTO calls (lead_id, recording_path) VALUES (%s, %s)", (lead, bad))
    raw.execute(
        "INSERT INTO calls (lead_id, recording_path) VALUES (%s, 'calls/2026/10/abc.wav')", (lead,)
    )


def test_n8n_error_branch_records_ids_only(raw):
    raw.execute("SET ROLE ledger_n8n")
    raw.execute(
        "SELECT api.record_workflow_error('stale-job-alert', '1234', 'Query queue', 'NodeOperationError')"
    )
    raw.execute("RESET ROLE")
    row = raw.execute(
        "SELECT event_type, payload FROM events WHERE entity_type = 'n8n_workflow'"
    ).fetchone()
    assert row["event_type"] == "workflow.failed"
    assert row["payload"]["execution_id"] == "1234"


@pytest.mark.parametrize(
    ("age_min", "stale", "alert"),
    [
        (200, False, False),
        (240, True, True),
        (250, True, True),
        (260, True, False),
        (480, True, True),
        (495, True, False),
    ],
)
def test_queue_health_alerts_on_crossing_then_every_four_hours(raw, age_min, stale, alert):
    as_actor(raw, "worker")
    raw.execute(
        "INSERT INTO jobs (job_type, run_after) VALUES ('healthcheck', now() - make_interval(mins => %s))",
        (age_min,),
    )
    raw.execute("SET ROLE ledger_n8n")
    row = raw.execute("SELECT * FROM api.queue_health(15)").fetchone()  # 4 h default (item 24)
    raw.execute("RESET ROLE")
    assert (row["oldest_pending_age_min"], row["is_stale"], row["should_alert"]) == (
        age_min,
        stale,
        alert,
    )
