"""The review console core (docs/phase-1-design.md section 5)."""

from __future__ import annotations

import io
import logging

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.review.conftest import JACK, PASSWORD, SAM, csrf_from, sign_in

pytestmark = pytest.mark.db


def _ledger(db_url):
    return psycopg.connect(db_url, autocommit=True, row_factory=dict_row)


def test_healthz_needs_no_sign_in(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_every_screen_needs_a_founder(client):
    r = client.get("/", headers={"Tailscale-User-Login": SAM})
    assert r.status_code == 303 and r.headers["location"].startswith("/signin")
    r = client.post("/settings", data={"key": "core.stale_job_alert_hours", "value": "1"})
    assert r.status_code == 403


def test_sign_in_through_tailscale_then_the_session_is_bound_to_that_login(client):
    r = sign_in(client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    home = client.get("/", headers={"Tailscale-User-Login": SAM})
    assert home.status_code == 200 and "Home" in home.text and "sam" in home.text
    # The same cookie from another Tailscale identity, or with no identity, is refused.
    assert client.get("/", headers={"Tailscale-User-Login": JACK}).status_code == 303
    assert client.get("/").status_code == 303


def test_tailscale_header_picks_the_founder_and_a_different_name_is_refused(client):
    r = sign_in(client, login=SAM, name="jack")
    assert r.status_code == 401


def test_local_sign_in_only_for_flagged_founders(client):
    assert sign_in(client, login=None, name="sam").status_code == 401
    r = sign_in(client, login=None, name="jack")
    assert r.status_code == 303
    assert client.get("/").status_code == 200
    # A local session is refused if a Tailscale identity appears on it.
    assert client.get("/", headers={"Tailscale-User-Login": JACK}).status_code == 303


def test_local_sign_in_is_off_unless_the_console_allows_it(console_settings, alerts, founders):
    from starlette.testclient import TestClient

    from review.app import build_app

    settings = console_settings.model_copy(update={"console_allow_local_signin": False})
    with TestClient(
        build_app(settings, alert=alerts.append),
        base_url="https://testserver",
        follow_redirects=False,
    ) as c:
        page = c.get("/signin")
        assert (
            "Open the console through Tailscale" in page.text and 'name="password"' not in page.text
        )
        token = c.cookies.get("cl_signin_csrf")
        r = c.post("/signin", data={"csrf": token, "name": "jack", "password": PASSWORD})
        assert r.status_code == 401


def test_wrong_passwords_lock_the_account_and_alert_with_an_id_only(client, alerts, db_url):
    for _ in range(4):
        assert sign_in(client, password="wrong password!!").status_code == 401
    r = sign_in(client, password="wrong password!!")
    assert r.status_code == 401 and "locked" in r.text
    assert sign_in(client).status_code == 401  # the right password is refused while locked
    assert len(alerts) == 1 and "console_signin_locked" in alerts[0]
    assert "sam" not in alerts[0] and SAM not in alerts[0]
    with _ledger(db_url) as c:
        events = c.execute(
            "SELECT event_type FROM events WHERE event_type LIKE 'console.%' ORDER BY id"
        ).fetchall()
    assert [e["event_type"] for e in events].count("console.signin_failed") == 5


def test_forms_need_the_session_csrf_token_and_same_site(client):
    sign_in(client)
    h = {"Tailscale-User-Login": SAM}
    data = {"key": "core.stale_job_alert_hours", "value": "3"}
    assert client.post("/settings", data=data, headers=h).status_code == 403
    page = client.get("/settings", headers=h)
    data["csrf"] = csrf_from(page.text)
    cross = {**h, "Sec-Fetch-Site": "cross-site"}
    assert client.post("/settings", data=data, headers=cross).status_code == 403
    assert client.post("/settings", data=data, headers=h).status_code == 303


def test_a_setting_saved_in_the_console_is_recorded_as_that_founder(client, db_url):
    sign_in(client)
    h = {"Tailscale-User-Login": SAM}
    csrf = csrf_from(client.get("/settings", headers=h).text)
    r = client.post(
        "/settings",
        data={"csrf": csrf, "key": "core.stale_job_alert_hours", "value": "6"},
        headers=h,
    )
    assert r.status_code == 303
    with _ledger(db_url) as c:
        row = c.execute(
            """SELECT s.value, a.name FROM settings s JOIN actors a ON a.id = s.changed_by
                WHERE s.key = 'core.stale_job_alert_hours'"""
        ).fetchone()
        ev = c.execute(
            """SELECT a.name FROM events e JOIN actors a ON a.id = e.actor_id
                WHERE e.entity_type = 'settings' ORDER BY e.id DESC LIMIT 1"""
        ).fetchone()
    assert (row["value"], row["name"], ev["name"]) == (6, "sam", "sam")
    bad = client.post(
        "/settings",
        data={"csrf": csrf, "key": "core.stale_job_alert_hours", "value": "900"},
        headers=h,
    )
    assert bad.status_code == 303
    page = client.get("/settings", headers=h)
    assert "at most 72" in page.text


def test_turning_a_system_on_and_off(client, db_url):
    sign_in(client)
    h = {"Tailscale-User-Login": SAM}
    page = client.get("/systems", headers=h)
    assert "Health check" in page.text and "off" in page.text
    csrf = csrf_from(page.text)
    assert (
        client.post(
            "/systems/healthcheck", data={"csrf": csrf, "enabled": "on"}, headers=h
        ).status_code
        == 303
    )
    with _ledger(db_url) as c:
        row = c.execute(
            """SELECT s.enabled, a.name FROM systems s JOIN actors a ON a.id = s.enabled_changed_by
                WHERE s.key = 'healthcheck'"""
        ).fetchone()
    assert (row["enabled"], row["name"]) == (True, "sam")
    client.post("/systems/healthcheck", data={"csrf": csrf}, headers=h)
    with _ledger(db_url) as c:
        assert (
            c.execute("SELECT enabled FROM systems WHERE key = 'healthcheck'").fetchone()["enabled"]
            is False
        )


def test_need_states_are_added_and_launched_without_code(client, db_url):
    sign_in(client)
    h = {"Tailscale-User-Login": SAM}
    csrf = csrf_from(client.get("/need-states", headers=h).text)
    client.post(
        "/need-states",
        headers=h,
        data={
            "csrf": csrf,
            "key": "career",
            "label": "Career and leadership",
            "covers": "Promotions, founders",
            "not_covered": "Therapy",
            "active": "on",
        },
    )
    client.post(
        "/need-states/launch",
        headers=h,
        data={"csrf": csrf, "market": "testville", "need_state": "career", "launched": "on"},
    )
    page = client.get("/need-states", headers=h)
    assert "Career and leadership" in page.text and "launched" in page.text
    with _ledger(db_url) as c:
        row = c.execute(
            "SELECT launched FROM market_need_states WHERE need_state_key = 'career'"
        ).fetchone()
    assert row["launched"] is True


def test_the_ledger_refusal_is_shown_not_hidden(client):
    sign_in(client)
    h = {"Tailscale-User-Login": SAM}
    csrf = csrf_from(client.get("/markets", headers=h).text)
    r = client.post(
        "/markets",
        headers={**h, "Referer": "https://testserver/markets"},
        data={"csrf": csrf, "key": "mars", "name": "Mars", "time_zone": "Mars/Olympus"},
    )
    assert r.status_code == 303 and r.headers["location"] == "/markets"
    page = client.get("/markets", headers=h)
    assert "The Ledger refused that" in page.text and "unknown time zone" in page.text


def test_changing_the_password_signs_out_other_sessions(client, console_settings, alerts):
    from starlette.testclient import TestClient

    from review.app import build_app

    sign_in(client)
    with TestClient(
        build_app(console_settings, alert=alerts.append),
        base_url="https://testserver",
        follow_redirects=False,
    ) as other:
        sign_in(other)
        h = {"Tailscale-User-Login": SAM}
        assert other.get("/", headers=h).status_code == 200
        csrf = csrf_from(client.get("/account", headers=h).text)
        r = client.post(
            "/account/password",
            headers=h,
            data={
                "csrf": csrf,
                "current_password": PASSWORD,
                "new_password": "a brand new passphrase",
                "new_password_again": "a brand new passphrase",
            },
        )
        assert r.status_code == 303
        assert other.get("/", headers=h).status_code == 303
        assert client.get("/", headers=h).status_code == 200


def test_history_shows_who_changed_what_but_never_sensitive_values(client, db_url, sentinels):
    sign_in(client)
    h = {"Tailscale-User-Login": SAM}
    with _ledger(db_url) as c:
        c.execute("SELECT set_config('app.actor', 'sam', false)")
        coach = c.execute(
            "INSERT INTO coaches (display_name, email) VALUES ('Coach Q', %s) RETURNING id",
            (sentinels["emails"][0],),
        ).fetchone()["id"]
    page = client.get(f"/history/coaches/{coach}", headers=h)
    assert page.status_code == 200 and "row.insert" in page.text
    assert sentinels["emails"][0] not in page.text and "changed" in page.text
    assert client.get("/history/console_users/x", headers=h).status_code == 400


def test_pages_are_never_cached_or_framed(client):
    sign_in(client)
    r = client.get("/", headers={"Tailscale-User-Login": SAM})
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "server" not in {k.lower() for k in r.headers}


def test_after_sign_in_the_console_never_redirects_off_site(client):
    page = client.get("/signin?next=//evil.example/x", headers={"Tailscale-User-Login": SAM})
    assert 'value="/"' in page.text
    r = client.post(
        "/signin",
        headers={"Tailscale-User-Login": SAM},
        data={"csrf": csrf_from(page.text), "password": PASSWORD, "next": "https://evil.example/"},
    )
    assert r.headers["location"] == "/"


def test_console_traffic_leaks_nothing_into_logs(client, db_url, sentinels):
    from worker import logs

    buf = io.StringIO()
    logs.configure(stream=buf)
    try:
        sign_in(client, password=sentinels["names"][0] + " wrong")
        sign_in(client)
        h = {"Tailscale-User-Login": SAM}
        csrf = csrf_from(client.get("/need-states", headers=h).text)
        client.post(
            "/need-states",
            headers=h,
            data={
                "csrf": csrf,
                "key": "dating",
                "label": sentinels["names"][1],
                "covers": sentinels["transcript_sentences"][0],
            },
        )
        client.get("/need-states", headers=h)
    finally:
        logging.getLogger().handlers.clear()
    text = buf.getvalue()
    assert "console sign-in" in text
    for s in (*sentinels["names"], *sentinels["emails"], *sentinels["transcript_sentences"], SAM):
        assert s not in text


def test_the_console_binds_loopback_only(tmp_path):
    from pydantic import ValidationError

    from review.config import ConsoleSettings

    with pytest.raises(ValidationError, match="loopback"):
        ConsoleSettings(
            console_database_url="postgres://x@127.0.0.1/y",
            private_data_dir=tmp_path,
            review_console_host="0.0.0.0",  # noqa: S104 - the point of the test
        )


def test_admin_refuses_weak_passwords_and_unknown_founders(db_url):
    from review import admin, passwords
    from worker.db import connect

    with connect(db_url) as conn:
        with pytest.raises(passwords.WeakPassword):
            admin.create_user(
                conn, "sam", tailscale_login=SAM, allow_local=False, prompt=lambda _p: "short"
            )
        with pytest.raises(SystemExit, match="No active founder"):
            admin.create_user(
                conn, "mallory", tailscale_login=None, allow_local=True, prompt=lambda _p: PASSWORD
            )
