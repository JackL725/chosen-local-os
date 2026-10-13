"""coaches: Ledger writes. Every function runs on the caller's connection, inside a
transaction the caller opened as the acting founder (review.web.Page.write), so the
Ledger's founder rules and event trigger always apply.

The Ledger itself enforces the rules that matter most (who may verify, when a coach may
become active); this module adds the workflow: creating a coach's checklist when vetting
starts, carrying items over when a coach moves to another standard, and refusing to
verify an item that has no evidence.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

STATUSES = ("prospect", "vetting", "active", "paused", "removed")
# Allowed moves in the roster. The Ledger adds its own checks on top (activation).
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "prospect": ("vetting", "removed"),
    "vetting": ("active", "prospect", "removed"),
    "active": ("paused", "removed"),
    "paused": ("active", "removed"),
    "removed": ("prospect",),
}
SOURCES = ("research", "referral", "inbound")
CSV_COLUMNS = (
    "display_name",
    "legal_name",
    "email",
    "phone",
    "website",
    "price_per_month",
    "program_length_months",
    "capacity_open",
    "postal_code",
    "home_area",
    "serves_remote",
    "need_states",
    "market",
    "source",
    "notes",
)
MAX_IMPORT_ROWS = 500


class CoachError(ValueError):
    """Something the founder entered is not valid. The message is shown to them."""


# ------------------------------------------------------------------ field parsing
def parse_money_cents(raw: str) -> int | None:
    text = raw.strip().replace(",", "").replace("$", "")
    if not text:
        return None
    if not re.fullmatch(r"\d{1,7}(\.\d{1,2})?", text):
        raise CoachError(f"Price {raw!r} is not an amount in dollars.")
    return round(float(text) * 100)


def parse_int(raw: str, what: str, *, minimum: int = 0, maximum: int = 10_000) -> int | None:
    text = raw.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{1,6}", text) or not (minimum <= int(text) <= maximum):
        raise CoachError(f"{what} must be a whole number from {minimum} to {maximum}.")
    return int(text)


def parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "y", "yes", "true", "on")


@dataclass
class CoachFields:
    display_name: str
    legal_name: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    price_per_month_cents: int | None = None
    program_length_months: int | None = None
    capacity_open: int | None = None
    postal_code: str | None = None
    home_area: str | None = None
    serves_remote: bool = False
    source: str | None = None
    notes: str | None = None
    market_key: str | None = None
    need_states: list[str] = field(default_factory=list)


def coach_fields(get: Any, *, need_states: list[str] | None = None) -> CoachFields:
    """Validate a coach form (or CSV row). `get(name)` returns a stripped string."""
    name = get("display_name")
    if not name:
        raise CoachError("A coach needs a display name.")
    if len(name) > 120:
        raise CoachError("The display name is too long.")
    email = get("email") or None
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise CoachError(f"{email!r} is not an email address.")
    website = get("website") or None
    if website and not re.match(r"^https?://", website):
        website = "https://" + website
    source = get("source") or None
    if source and source not in SOURCES:
        raise CoachError(f"Source must be one of {', '.join(SOURCES)}.")
    postal = get("postal_code") or None
    if postal and not re.fullmatch(r"[A-Za-z0-9 -]{3,10}", postal):
        raise CoachError(f"{postal!r} is not a postal code.")
    return CoachFields(
        display_name=name,
        legal_name=get("legal_name") or None,
        email=email,
        phone=get("phone") or None,
        website=website,
        price_per_month_cents=parse_money_cents(get("price_per_month")),
        program_length_months=parse_int(
            get("program_length_months"), "Program length", minimum=1, maximum=60
        ),
        capacity_open=parse_int(get("capacity_open"), "Open capacity", maximum=500),
        postal_code=postal,
        home_area=get("home_area") or None,
        serves_remote=parse_bool(get("serves_remote")),
        source=source,
        notes=get("notes") or None,
        market_key=get("market") or None,
        need_states=need_states if need_states is not None else [],
    )


# ------------------------------------------------------------------------ writes
_COLUMNS = (
    "display_name",
    "legal_name",
    "email",
    "phone",
    "website",
    "price_per_month_cents",
    "program_length_months",
    "capacity_open",
    "postal_code",
    "home_area",
    "serves_remote",
    "source",
    "notes",
)


def create_coach(conn: psycopg.Connection, f: CoachFields) -> str:
    cols = [*_COLUMNS, "market_key"]
    values = [getattr(f, c) for c in _COLUMNS] + [f.market_key]
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("INSERT INTO coaches ({}) VALUES ({}) RETURNING id").format(
                sql.SQL(", ").join(map(sql.Identifier, cols)),
                sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            ),
            values,
        )
        coach_id = str(cur.fetchone()["id"])
        if f.capacity_open is not None:
            cur.execute(
                "UPDATE coaches SET capacity_confirmed_at = now() WHERE id = %s", (coach_id,)
            )
    set_need_states(conn, coach_id, f.need_states)
    return coach_id


def update_coach(conn: psycopg.Connection, coach_id: str, f: CoachFields) -> None:
    sets = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder()) for c in _COLUMNS
    )
    values = [getattr(f, c) for c in _COLUMNS]
    with conn.cursor() as cur:
        cur.execute("SELECT capacity_open FROM coaches WHERE id = %s", (coach_id,))
        before = cur.fetchone()
        if before is None:
            raise CoachError("Unknown coach.")
        cur.execute(
            sql.SQL("UPDATE coaches SET {} WHERE id = %s").format(sets), [*values, coach_id]
        )
        if f.market_key:
            cur.execute(
                "UPDATE coaches SET market_key = %s WHERE id = %s", (f.market_key, coach_id)
            )
        if f.capacity_open is not None:
            # Saving the capacity, even unchanged, is the founder confirming it.
            cur.execute(
                "UPDATE coaches SET capacity_confirmed_at = now() WHERE id = %s", (coach_id,)
            )
    set_need_states(conn, coach_id, f.need_states)


def set_need_states(conn: psycopg.Connection, coach_id: str, keys: list[str]) -> None:
    wanted = sorted(set(keys))
    with conn.cursor() as cur:
        if wanted:
            cur.execute("SELECT key FROM need_states WHERE key = ANY(%s) AND active", (wanted,))
            known = {r["key"] for r in cur.fetchall()}
            unknown = sorted(set(wanted) - known)
            if unknown:
                raise CoachError(f"Unknown or inactive need-states: {', '.join(unknown)}.")
        cur.execute(
            "DELETE FROM coach_need_states WHERE coach_id = %s AND NOT (need_state_key = ANY(%s))",
            (coach_id, wanted),
        )
        for key in wanted:
            cur.execute(
                """INSERT INTO coach_need_states (coach_id, need_state_key) VALUES (%s, %s)
                   ON CONFLICT (coach_id, need_state_key) DO NOTHING""",
                (coach_id, key),
            )


def _checklist(conn: psycopg.Connection, coach_id: str) -> None:
    """Make sure the coach has one vetting item per item type of their standard.
    Items already there (by key) are pointed at the standard's type, so verification
    and evidence carry over when a coach moves to a new standard."""
    with conn.cursor() as cur:
        cur.execute("SELECT vetting_standard_id FROM coaches WHERE id = %s", (coach_id,))
        std = cur.fetchone()["vetting_standard_id"]
        if std is None:
            raise CoachError("There is no vetting standard yet. Add one under Coaches → Standards.")
        cur.execute(
            """UPDATE vetting_items v SET item_type_id = t.id
                 FROM vetting_item_types t
                WHERE v.coach_id = %s AND t.standard_id = %s AND t.key = v.item_type
                  AND v.item_type_id IS DISTINCT FROM t.id""",
            (coach_id, std),
        )
        cur.execute(
            """INSERT INTO vetting_items (coach_id, item_type_id)
               SELECT %s, t.id FROM vetting_item_types t
                WHERE t.standard_id = %s
                  AND NOT EXISTS (SELECT 1 FROM vetting_items v
                                   WHERE v.coach_id = %s AND v.item_type = t.key)""",
            (coach_id, std, coach_id),
        )


def set_status(conn: psycopg.Connection, coach_id: str, new: str) -> None:
    if new not in STATUSES:
        raise CoachError("Unknown status.")
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM coaches WHERE id = %s", (coach_id,))
        row = cur.fetchone()
        if row is None:
            raise CoachError("Unknown coach.")
        if new not in TRANSITIONS[row["status"]]:
            raise CoachError(f"A coach cannot go from {row['status']} to {new}.")
        if new == "vetting":
            cur.execute(
                "SELECT 1 FROM vetting_standards WHERE status IN ('draft', 'active') LIMIT 1"
            )
            if cur.fetchone() is None:
                raise CoachError("There is no vetting standard yet. Add one under Standards.")
        cur.execute("UPDATE coaches SET status = %s WHERE id = %s", (new, coach_id))
    if new == "vetting":
        _checklist(conn, coach_id)


def move_to_standard(conn: psycopg.Connection, coach_id: str, standard_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM vetting_standards WHERE id = %s", (standard_id,))
        std = cur.fetchone()
        if std is None or std["status"] == "retired":
            raise CoachError("Choose a draft or active standard.")
        cur.execute("SELECT status FROM coaches WHERE id = %s", (coach_id,))
        coach = cur.fetchone()
        if coach is None or coach["status"] not in ("vetting", "paused", "prospect"):
            raise CoachError("Only a coach who is not active can move to another standard.")
        cur.execute(
            "UPDATE coaches SET vetting_standard_id = %s WHERE id = %s", (standard_id, coach_id)
        )
    _checklist(conn, coach_id)


def _item(conn: psycopg.Connection, coach_id: str, item_id: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT v.*, t.evidence_kind, t.label, c.status AS coach_status
                 FROM vetting_items v
                 JOIN coaches c ON c.id = v.coach_id
                 LEFT JOIN vetting_item_types t ON t.id = v.item_type_id
                WHERE v.id = %s AND v.coach_id = %s""",
            (item_id, coach_id),
        )
        row = cur.fetchone()
    if row is None:
        raise CoachError("Unknown vetting item.")
    return row


def verify_item(
    conn: psycopg.Connection, coach_id: str, item_id: str, founder_actor_id: str
) -> None:
    item = _item(conn, coach_id, item_id)
    kinds = {"file": ("file",), "note": ("note",), "any": ("file", "note")}[
        item["evidence_kind"] or "any"
    ]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM vetting_evidence
                WHERE vetting_item_id = %s AND removed_at IS NULL AND kind = ANY(%s)""",
            (item_id, list(kinds)),
        )
        if cur.fetchone()["n"] == 0:
            need = (
                "a file" if kinds == ("file",) else ("a note" if kinds == ("note",) else "evidence")
            )
            raise CoachError(f"Add {need} for “{item['label']}” before verifying it.")
        cur.execute(
            "UPDATE vetting_items SET verified_by = %s, verified_at = now() WHERE id = %s",
            (founder_actor_id, item_id),
        )


def unverify_item(conn: psycopg.Connection, coach_id: str, item_id: str) -> None:
    item = _item(conn, coach_id, item_id)
    if item["coach_status"] == "active":
        raise CoachError("Pause the coach before un-verifying part of their vetting file.")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE vetting_items SET verified_by = NULL, verified_at = NULL WHERE id = %s",
            (item_id,),
        )


def add_note(conn: psycopg.Connection, coach_id: str, item_id: str, note: str) -> None:
    _item(conn, coach_id, item_id)
    if not note.strip():
        raise CoachError("The note is empty.")
    if len(note) > 20_000:
        raise CoachError("The note is too long; attach it as a file instead.")
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO vetting_evidence (vetting_item_id, kind, note, uploaded_by)
               VALUES (%s, 'note', %s,
                       (SELECT id FROM actors WHERE name = current_setting('app.actor')))""",
            (item_id, note.strip()),
        )


def remove_evidence(conn: psycopg.Connection, coach_id: str, evidence_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE vetting_evidence e SET removed_at = now()
                 FROM vetting_items v
                WHERE e.id = %s AND e.vetting_item_id = v.id AND v.coach_id = %s
                  AND e.removed_at IS NULL""",
            (evidence_id, coach_id),
        )
        if cur.rowcount != 1:
            raise CoachError("Unknown evidence.")


# ---------------------------------------------------------------- vetting standards
def new_draft(conn: psycopg.Connection, title: str) -> str:
    """A new draft standard, starting from the active one's items (or empty)."""
    with conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(version), 0) + 1 AS v FROM vetting_standards")
        version = cur.fetchone()["v"]
        cur.execute(
            "INSERT INTO vetting_standards (version, title) VALUES (%s, %s) RETURNING id",
            (version, title.strip() or f"Standard {version}"),
        )
        new_id = str(cur.fetchone()["id"])
        cur.execute(
            """INSERT INTO vetting_item_types
                   (standard_id, key, label, description, evidence_kind, required, sort_order)
               SELECT %s, key, label, description, evidence_kind, required, sort_order
                 FROM vetting_item_types
                WHERE standard_id = (SELECT id FROM vetting_standards WHERE status = 'active')""",
            (new_id,),
        )
    return new_id


def save_item_type(conn: psycopg.Connection, standard_id: str, get: Any) -> None:
    key = get("key").lower()
    label = get("label")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not label:
        raise CoachError("An item needs a key (lowercase letters, digits, _) and a label.")
    kind = get("evidence_kind") or "any"
    if kind not in ("file", "note", "any"):
        raise CoachError("Evidence must be file, note or any.")
    order = parse_int(get("sort_order") or "100", "Order", maximum=1000) or 100
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO vetting_item_types
                   (standard_id, key, label, description, evidence_kind, required, sort_order)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (standard_id, key) DO UPDATE SET label = EXCLUDED.label,
                   description = EXCLUDED.description, evidence_kind = EXCLUDED.evidence_kind,
                   required = EXCLUDED.required, sort_order = EXCLUDED.sort_order""",
            (
                standard_id,
                key,
                label,
                get("description") or None,
                kind,
                get("required", "on") == "on",
                order,
            ),
        )


def delete_item_type(conn: psycopg.Connection, standard_id: str, type_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM vetting_items WHERE item_type_id = %s", (type_id,))
        if cur.fetchone()["n"]:
            raise CoachError(
                "Coaches are already being vetted on this item; keep it or start a new draft."
            )
        cur.execute(
            "DELETE FROM vetting_item_types WHERE id = %s AND standard_id = %s",
            (type_id, standard_id),
        )


def activate_standard(conn: psycopg.Connection, standard_id: str) -> None:
    """Make a draft the active standard; the previous active one is retired.
    Active coaches are untouched; coaches still in vetting stay on their standard
    until a founder moves them."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE vetting_standards SET status = 'retired' WHERE status = 'active' AND id <> %s",
            (standard_id,),
        )
        cur.execute(
            "UPDATE vetting_standards SET status = 'active' WHERE id = %s AND status = 'draft'",
            (standard_id,),
        )
        if cur.rowcount != 1:
            raise CoachError("Only a draft standard can be activated.")


def discard_draft(conn: psycopg.Connection, standard_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM coaches WHERE vetting_standard_id = %s", (standard_id,)
        )
        if cur.fetchone()["n"]:
            raise CoachError("Coaches are being vetted under this draft; move them first.")
        cur.execute(
            "UPDATE vetting_standards SET status = 'retired' WHERE id = %s AND status = 'draft'",
            (standard_id,),
        )


# ----------------------------------------------------------------------- CSV import
@dataclass
class ImportRow:
    line: int
    fields: CoachFields | None
    error: str | None


def parse_csv(text: str) -> list[ImportRow]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    if "display_name" not in headers:
        raise CoachError("The CSV needs a header row with at least a display_name column.")
    unknown = sorted(set(headers) - set(CSV_COLUMNS))
    if unknown:
        raise CoachError(
            f"Unknown columns: {', '.join(unknown)}. Allowed: {', '.join(CSV_COLUMNS)}."
        )
    rows: list[ImportRow] = []
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_IMPORT_ROWS:
            raise CoachError(f"At most {MAX_IMPORT_ROWS} coaches per import.")
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not any(row.values()):
            continue
        try:
            keys = [k.strip() for k in re.split(r"[;|]", row.get("need_states", "")) if k.strip()]
            f = coach_fields(lambda name, d="", row=row: row.get(name, d), need_states=keys)
            rows.append(ImportRow(n, f, None))
        except CoachError as exc:
            rows.append(ImportRow(n, None, str(exc)))
    return rows


def import_rows(conn: psycopg.Connection, rows: list[ImportRow]) -> int:
    bad = [r for r in rows if r.error]
    if bad:
        raise CoachError("Fix the rows with errors first.")
    for r in rows:
        assert r.fields is not None
        create_coach(conn, r.fields)
    return len(rows)


# ------------------------------------------------------------------------ readiness
def readiness(conn: psycopg.Connection, required: int) -> dict[str, Any]:
    """Per market and launched need-state: active coaches with open capacity against
    the number the gate needs, and who is still in vetting."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.key AS market_key, m.name AS market, ns.key AS need_state_key,
                      ns.label AS need_state,
                      count(DISTINCT c.id) FILTER (
                          WHERE c.status = 'active' AND coalesce(c.capacity_open, 0) > 0) AS ready,
                      count(DISTINCT c.id) FILTER (WHERE c.status = 'active') AS active,
                      count(DISTINCT c.id) FILTER (WHERE c.status = 'vetting') AS vetting
                 FROM market_need_states mns
                 JOIN markets m ON m.key = mns.market_key AND m.active
                 JOIN need_states ns ON ns.key = mns.need_state_key AND ns.active
                 LEFT JOIN coach_need_states cns ON cns.need_state_key = ns.key
                 LEFT JOIN coaches c ON c.id = cns.coach_id AND c.market_key = m.key
                WHERE mns.launched
                GROUP BY m.key, m.name, ns.key, ns.label, ns.sort_order
                ORDER BY m.name, ns.sort_order, ns.key"""
        )
        cells = cur.fetchall()
        cur.execute(
            """SELECT c.id, c.display_name, c.market_key, s.status AS standard_status,
                      array_remove(array_agg(DISTINCT cns.need_state_key), NULL) AS need_states,
                      array_remove(array_agg(t.label ORDER BY t.sort_order)
                          FILTER (WHERE t.required AND v.verified_at IS NULL), NULL) AS missing
                 FROM coaches c
                 LEFT JOIN vetting_standards s ON s.id = c.vetting_standard_id
                 LEFT JOIN coach_need_states cns ON cns.coach_id = c.id
                 LEFT JOIN vetting_item_types t ON t.standard_id = c.vetting_standard_id
                 LEFT JOIN vetting_items v ON v.coach_id = c.id AND v.item_type_id = t.id
                WHERE c.status = 'vetting'
                GROUP BY c.id, c.display_name, c.market_key, s.status
                ORDER BY c.display_name"""
        )
        vetting = cur.fetchall()
    for cell in cells:
        cell["required"] = required
        cell["met"] = cell["ready"] >= required
    return {
        "cells": cells,
        "vetting": vetting,
        "all_met": bool(cells) and all(c["met"] for c in cells),
    }
