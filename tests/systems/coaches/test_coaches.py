"""The coaches system through the console (docs/phase-1-design.md section 6)."""

from __future__ import annotations

import io
import logging
import re

import pytest

from tests.review.conftest import SAM, csrf_from, sign_in

pytestmark = pytest.mark.db
H = {"Tailscale-User-Login": SAM}


def _post(client, path, data, *, files=None, referer=None):
    page = client.get(referer or path.rsplit("/", 1)[0] or "/", headers=H)
    data = {"csrf": csrf_from(page.text), **data}
    headers = {**H, "Referer": f"https://testserver{referer}"} if referer else H
    return client.post(path, data=data, files=files, headers=headers)


def _new_coach(client, **fields) -> str:
    sign_in(client)
    data = {
        "display_name": "Coach Alex",
        "price_per_month": "2,500",
        "program_length_months": "3",
        "capacity_open": "2",
        "need_states": ["fitness"],
    }
    data.update(fields)
    page = client.get("/s/coaches/new", headers=H)
    r = client.post("/s/coaches/new", headers=H, data={"csrf": csrf_from(page.text), **data})
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[1]


def _flash(client, path) -> str:
    return client.get(path, headers=H).text


def _items(ledger, coach) -> dict[str, str]:
    rows = ledger.execute("SELECT id, item_type FROM vetting_items WHERE coach_id = %s", (coach,))
    return {r["item_type"]: str(r["id"]) for r in rows.fetchall()}


def test_a_coach_is_added_vetted_and_activated_only_by_founders(
    client, ledger, seeded, console_settings
):
    coach = _new_coach(client)
    detail = f"/s/coaches/{coach}"
    row = ledger.execute("SELECT * FROM coaches WHERE id = %s", (coach,)).fetchone()
    assert (row["status"], row["price_per_month_cents"], row["market_key"]) == (
        "prospect",
        250000,
        "testville",
    )

    assert (
        _post(client, f"{detail}/status", {"status": "vetting"}, referer=detail).status_code == 303
    )
    items = _items(ledger, coach)
    assert set(items) == {"interview", "reference", "agreement"}

    # Verifying needs evidence of the right kind.
    _post(client, f"{detail}/items/{items['agreement']}/verify", {}, referer=detail)
    assert "Add a file" in _flash(client, detail)
    _post(
        client,
        f"{detail}/items/{items['agreement']}/note",
        {"note": "Signed by email"},
        referer=detail,
    )
    _post(client, f"{detail}/items/{items['agreement']}/verify", {}, referer=detail)
    assert "Add a file" in _flash(client, detail)

    pdf = b"%PDF-1.4 signed agreement"
    r = _post(
        client,
        f"{detail}/items/{items['agreement']}/upload",
        {},
        referer=detail,
        files={"file": ("Agreement - Alex.pdf", io.BytesIO(pdf), "application/pdf")},
    )
    assert r.status_code == 303
    ev = ledger.execute(
        "SELECT * FROM vetting_evidence WHERE kind = 'file' AND vetting_item_id = %s",
        (items["agreement"],),
    ).fetchone()
    stored = console_settings.private_data_dir / ev["relative_path"]
    assert stored.read_bytes() == pdf and "Alex" not in ev["relative_path"]
    assert ev["display_name"] == "Agreement - Alex.pdf" and ev["size_bytes"] == len(pdf)

    for key in ("interview", "reference"):
        _post(client, f"{detail}/items/{items[key]}/note", {"note": f"{key} done"}, referer=detail)
    for key in items:
        _post(client, f"{detail}/items/{items[key]}/verify", {}, referer=detail)
    verified = ledger.execute(
        """SELECT a.name, count(*) AS n FROM vetting_items v JOIN actors a ON a.id = v.verified_by
            WHERE v.coach_id = %s GROUP BY a.name""",
        (coach,),
    ).fetchall()
    assert [(r["name"], r["n"]) for r in verified] == [("sam", 3)]

    # Under a draft standard the Ledger refuses activation, and the console says why.
    _post(client, f"{detail}/status", {"status": "active"}, referer=detail)
    assert "standard is not active" in _flash(client, detail)
    _post(
        client,
        f"/s/coaches/standards/{seeded}/state",
        {"action": "activate"},
        referer=f"/s/coaches/standards/{seeded}",
    )
    _post(client, f"{detail}/status", {"status": "active"}, referer=detail)
    row = ledger.execute(
        """SELECT c.status, a.name FROM coaches c JOIN actors a ON a.id = c.status_changed_by
            WHERE c.id = %s""",
        (coach,),
    ).fetchone()
    assert (row["status"], row["name"]) == ("active", "sam")
    events = ledger.execute(
        """SELECT DISTINCT a.name FROM events e JOIN actors a ON a.id = e.actor_id
            WHERE e.entity_type = 'coaches' AND e.entity_id = %s""",
        (coach,),
    ).fetchall()
    assert {e["name"] for e in events} == {"sam"}

    # An active coach's file cannot be un-verified.
    _post(
        client,
        f"{detail}/items/{items['interview']}/verify",
        {"action": "unverify"},
        referer=detail,
    )
    assert "Pause the coach" in _flash(client, detail)


def test_evidence_downloads_only_for_a_signed_in_founder(client, ledger, seeded, console_settings):
    coach = _new_coach(client)
    detail = f"/s/coaches/{coach}"
    _post(client, f"{detail}/status", {"status": "vetting"}, referer=detail)
    item = _items(ledger, coach)["agreement"]
    _post(
        client,
        f"{detail}/items/{item}/upload",
        {},
        referer=detail,
        files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
    )
    ev = ledger.execute("SELECT id FROM vetting_evidence WHERE kind = 'file'").fetchone()["id"]
    r = client.get(f"/s/coaches/evidence/{ev}", headers=H)
    assert r.status_code == 200 and r.content == b"%PDF-1.4 x"
    assert r.headers["cache-control"] == "no-store"
    assert client.get(f"/s/coaches/evidence/{ev}").status_code == 303  # no Tailscale identity


def test_uploads_refuse_unknown_types_and_leave_no_orphans(
    client, ledger, seeded, console_settings
):
    coach = _new_coach(client)
    detail = f"/s/coaches/{coach}"
    _post(client, f"{detail}/status", {"status": "vetting"}, referer=detail)
    item = _items(ledger, coach)["interview"]
    _post(
        client,
        f"{detail}/items/{item}/upload",
        {},
        referer=detail,
        files={"file": ("run.sh", io.BytesIO(b"#!/bin/sh"), "text/x-sh")},
    )
    assert "file type is not accepted" in _flash(client, detail)
    files = list((console_settings.private_data_dir).rglob("*"))
    assert [f for f in files if f.is_file()] == []


def test_stored_paths_cannot_escape_the_private_folder(tmp_path):
    from systems.coaches import evidence
    from systems.coaches.service import CoachError

    (tmp_path / "coaches").mkdir()
    (tmp_path / "secret.txt").write_text("x")
    with pytest.raises(CoachError):
        evidence.resolve(tmp_path, "coaches/../secret.txt")
    with pytest.raises(CoachError):
        evidence.resolve(tmp_path, "/etc/passwd")


def test_csv_import_previews_then_imports_prospects(client, ledger, seeded):
    sign_in(client)
    csv_text = (
        "display_name,email,price_per_month,need_states,serves_remote\n"
        "Coach Blake,blake@example.test,3000,fitness,yes\n"
        "Coach Casey,not-an-email,1500,fitness,no\n"
    )
    page = client.get("/s/coaches/import", headers=H)
    r = client.post(
        "/s/coaches/import",
        headers=H,
        data={"csrf": csrf_from(page.text)},
        files={"file": ("coaches.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert r.status_code == 200 and "1 with errors" in r.text and "not an email" in r.text
    assert ledger.execute("SELECT count(*) AS n FROM coaches").fetchone()["n"] == 0

    good = csv_text.replace("not-an-email", "casey@example.test")
    r = client.post(
        "/s/coaches/import",
        headers=H,
        data={"csrf": csrf_from(page.text)},
        files={"file": ("coaches.csv", io.BytesIO(good.encode()), "text/csv")},
    )
    assert "all valid" in r.text
    text = re.search(r'<textarea name="csv_text" hidden>(.*?)</textarea>', r.text, re.S).group(1)
    import html

    r = client.post(
        "/s/coaches/import",
        headers=H,
        data={"csrf": csrf_from(page.text), "confirm": "yes", "csv_text": html.unescape(text)},
    )
    assert r.status_code == 303
    rows = ledger.execute(
        "SELECT display_name, status, serves_remote FROM coaches ORDER BY display_name"
    ).fetchall()
    assert [(r["display_name"], r["status"], r["serves_remote"]) for r in rows] == [
        ("Coach Blake", "prospect", True),
        ("Coach Casey", "prospect", False),
    ]


def test_readiness_counts_active_coaches_with_capacity_per_launched_need_state(
    client, ledger, seeded
):
    sign_in(client)
    page = client.get("/s/coaches/readiness", headers=H).text
    assert "0 / 2" in page and "No vetting standard is active" in page
    ledger.execute("SELECT set_config('app.actor', 'sam', false)")
    ledger.execute("UPDATE vetting_standards SET status = 'active'")
    for name, capacity in (("A", 1), ("B", 0), ("C", 2)):
        cid = ledger.execute(
            "INSERT INTO coaches (display_name, capacity_open, status) VALUES (%s, %s, 'vetting') RETURNING id",
            (name, capacity),
        ).fetchone()["id"]
        ledger.execute(
            "INSERT INTO coach_need_states (coach_id, need_state_key) VALUES (%s, 'fitness')",
            (cid,),
        )
        for key in ("interview", "reference", "agreement"):
            ledger.execute(
                """INSERT INTO vetting_items (coach_id, item_type, verified_by, verified_at)
                   VALUES (%s, %s, (SELECT id FROM actors WHERE name = 'sam'), now())""",
                (cid, key),
            )
        ledger.execute("UPDATE coaches SET status = 'active' WHERE id = %s", (cid,))
    page = client.get("/s/coaches/readiness", headers=H).text
    assert "2 / 2" in page and "Gate met." in page


def test_a_new_standard_carries_items_over_by_key(client, ledger, seeded):
    coach = _new_coach(client)
    detail = f"/s/coaches/{coach}"
    _post(client, f"{detail}/status", {"status": "vetting"}, referer=detail)
    items = _items(ledger, coach)
    _post(client, f"{detail}/items/{items['interview']}/note", {"note": "met"}, referer=detail)
    _post(client, f"{detail}/items/{items['interview']}/verify", {}, referer=detail)
    _post(client, "/s/coaches/standards", {"title": "Stricter"}, referer="/s/coaches/standards")
    new = ledger.execute("SELECT id FROM vetting_standards WHERE version = 2").fetchone()["id"]
    assert (
        ledger.execute(
            "SELECT count(*) AS n FROM vetting_item_types WHERE standard_id = %s", (new,)
        ).fetchone()["n"]
        == 0
    )  # no active standard to copy from yet
    std = f"/s/coaches/standards/{new}"
    _post(
        client,
        f"{std}/items",
        {"key": "interview", "label": "Interview", "evidence_kind": "any"},
        referer=std,
    )
    _post(
        client,
        f"{std}/items",
        {"key": "background_check", "label": "Background check", "evidence_kind": "file"},
        referer=std,
    )
    _post(client, f"{detail}/standard", {"standard_id": str(new)}, referer=detail)
    rows = ledger.execute(
        """SELECT v.item_type, v.verified_at IS NOT NULL AS verified, t.standard_id = %s AS current
             FROM vetting_items v JOIN vetting_item_types t ON t.id = v.item_type_id
            WHERE v.coach_id = %s ORDER BY v.item_type""",
        (new, coach),
    ).fetchall()
    got = {r["item_type"]: (r["verified"], r["current"]) for r in rows}
    assert got["interview"] == (True, True) and got["background_check"] == (False, True)
    assert got["reference"] == (False, False)  # stays, marked as from the earlier standard


def test_coach_screens_are_off_when_the_system_is_off(client, ledger, seeded):
    sign_in(client)
    assert client.get("/s/coaches/", headers=H).status_code == 200
    ledger.execute("SELECT set_config('app.actor', 'sam', false)")
    ledger.execute("UPDATE systems SET enabled = false WHERE key = 'coaches'")
    r = client.get("/s/coaches/", headers=H)
    assert r.status_code == 404 and "turned off" in r.text
    assert "/s/coaches/" not in client.get("/", headers=H).text  # gone from the navigation


def test_coach_details_never_reach_logs_or_events(client, ledger, seeded, sentinels):
    from worker import logs

    name, file_name = sentinels["names"]
    email = sentinels["emails"][0]
    notes, note = sentinels["transcript_sentences"]
    buf = io.StringIO()
    logs.configure(stream=buf)
    try:
        coach = _new_coach(client, display_name=f"Coach {name}", email=email, notes=notes)
        detail = f"/s/coaches/{coach}"
        _post(client, f"{detail}/status", {"status": "vetting"}, referer=detail)
        item = _items(ledger, coach)["reference"]
        _post(client, f"{detail}/items/{item}/note", {"note": note}, referer=detail)
        _post(
            client,
            f"{detail}/items/{item}/upload",
            {},
            referer=detail,
            files={"file": (f"{file_name}.pdf", io.BytesIO(b"%PDF x"), "application/pdf")},
        )
    finally:
        logging.getLogger().handlers.clear()
    events = str(
        ledger.execute(
            "SELECT payload FROM events WHERE entity_type IN ('coaches', 'vetting_evidence')"
        ).fetchall()
    )
    assert ledger.execute("SELECT count(*) AS n FROM vetting_evidence").fetchone()["n"] == 2
    for s in (email, notes, note, file_name):
        assert s not in buf.getvalue() and s not in events
    assert name not in buf.getvalue()
