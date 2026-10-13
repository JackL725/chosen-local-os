"""coaches screens in the review console, under /s/coaches/."""

from __future__ import annotations

import re
from typing import Any

from starlette.datastructures import UploadFile
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from review import web
from systems.coaches import evidence, service
from worker import business_settings
from worker.logs import get_logger
from worker.slack import format_category

log = get_logger("review")

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _id(page: web.Page, name: str) -> str:
    value = page.request.path_params[name]
    if not UUID.match(value):
        raise web.UserError("That link is not valid.")
    return value


def _fields(page: web.Page) -> service.CoachFields:
    need_states = page.form.getlist("need_states") if page.form else []
    try:
        return service.coach_fields(page.field, need_states=[str(n) for n in need_states])
    except service.CoachError as exc:
        raise web.UserError(str(exc)) from None


def _lookups(page: web.Page) -> dict[str, Any]:
    return {
        "need_states": page.query(
            "SELECT key, label FROM need_states WHERE active ORDER BY sort_order, key"
        ),
        "markets": page.query("SELECT key, name FROM markets WHERE active ORDER BY name"),
        "sources": service.SOURCES,
    }


def _write(page: web.Page):
    """A founder transaction that turns service errors into a message for the founder."""
    return _Guarded(page)


class _Guarded:
    def __init__(self, page: web.Page) -> None:
        self.page = page
        self._cm = page.write()

    def __enter__(self):
        return self._cm.__enter__()

    def __exit__(self, exc_type, exc, tb):
        result = self._cm.__exit__(exc_type, exc, tb)
        if isinstance(exc, service.CoachError):
            raise web.UserError(str(exc)) from None
        return result


# ------------------------------------------------------------------------- roster
@web.page
def index(page: web.Page) -> Response:
    status = page.request.query_params.get("status", "")
    need_state = page.request.query_params.get("need_state", "")
    market = page.request.query_params.get("market", "")
    if status not in (*service.STATUSES, "all"):
        status = ""
    coaches = page.query(
        """SELECT c.id, c.display_name, c.status, c.market_key, c.price_per_month_cents,
                  c.program_length_months, c.capacity_open, c.capacity_confirmed_at,
                  array_remove(array_agg(DISTINCT cns.need_state_key), NULL) AS need_states,
                  (SELECT count(*) FROM vetting_items v WHERE v.coach_id = c.id
                      AND v.verified_at IS NOT NULL) AS verified,
                  (SELECT count(*) FROM vetting_item_types t
                    WHERE t.standard_id = c.vetting_standard_id AND t.required) AS required
             FROM coaches c LEFT JOIN coach_need_states cns ON cns.coach_id = c.id
            WHERE ((%(status)s = '' AND c.status <> 'removed') OR %(status)s = 'all'
                   OR c.status = %(status)s)
              AND (%(need_state)s = '' OR EXISTS (
                     SELECT 1 FROM coach_need_states x
                      WHERE x.coach_id = c.id AND x.need_state_key = %(need_state)s))
              AND (%(market)s = '' OR c.market_key = %(market)s)
            GROUP BY c.id ORDER BY c.status, c.display_name""",
        {"status": status, "need_state": need_state, "market": market},
    )
    return page.render(
        "coaches/index.html",
        coaches=coaches,
        status=status,
        need_state=need_state,
        market=market,
        statuses=service.STATUSES,
        **_lookups(page),
    )


@web.page
def new(page: web.Page) -> Response:
    return page.render("coaches/edit.html", coach=None, chosen=[], **_lookups(page))


@web.page
def create(page: web.Page) -> Response:
    f = _fields(page)
    with _write(page):
        coach_id = service.create_coach(page.conn, f)
    return page.redirect("coaches:detail", coach_id=coach_id, flash=f"{f.display_name} added.")


# -------------------------------------------------------------------------- coach
@web.page
def detail(page: web.Page) -> Response:
    coach_id = _id(page, "coach_id")
    coach = page.one(
        """SELECT c.*, s.version AS standard_version, s.title AS standard_title,
                  s.status AS standard_status, a.name AS status_changed_by_name
             FROM coaches c
             LEFT JOIN vetting_standards s ON s.id = c.vetting_standard_id
             LEFT JOIN actors a ON a.id = c.status_changed_by
            WHERE c.id = %s""",
        (coach_id,),
    )
    if coach is None:
        raise web.UserError("Unknown coach.")
    chosen = [
        r["need_state_key"]
        for r in page.query(
            "SELECT need_state_key FROM coach_need_states WHERE coach_id = %s", (coach_id,)
        )
    ]
    items = page.query(
        """SELECT v.id, v.item_type, v.verified_at, a.name AS verified_by_name,
                  t.label, t.description, t.evidence_kind, coalesce(t.required, false) AS required,
                  (t.standard_id IS DISTINCT FROM c.vetting_standard_id) AS stale
             FROM vetting_items v
             JOIN coaches c ON c.id = v.coach_id
             LEFT JOIN vetting_item_types t ON t.id = v.item_type_id
             LEFT JOIN actors a ON a.id = v.verified_by
            WHERE v.coach_id = %s
            ORDER BY stale, t.sort_order, v.item_type""",
        (coach_id,),
    )
    files = page.query(
        """SELECT e.id, e.vetting_item_id, e.kind, e.display_name, e.size_bytes, e.note,
                  e.created_at, a.name AS uploaded_by_name
             FROM vetting_evidence e JOIN vetting_items v ON v.id = e.vetting_item_id
             JOIN actors a ON a.id = e.uploaded_by
            WHERE v.coach_id = %s AND e.removed_at IS NULL ORDER BY e.created_at""",
        (coach_id,),
    )
    by_item: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        by_item.setdefault(str(f["vetting_item_id"]), []).append(f)
    standards = page.query(
        "SELECT id, version, title, status FROM vetting_standards WHERE status <> 'retired'"
        " ORDER BY version DESC"
    )
    return page.render(
        "coaches/detail.html",
        coach=coach,
        chosen=chosen,
        items=items,
        evidence=by_item,
        moves=service.TRANSITIONS[coach["status"]],
        standards=standards,
        max_mb=web.state(page.request).settings.console_max_upload_mb,
        **_lookups(page),
    )


@web.page
def update(page: web.Page) -> Response:
    coach_id = _id(page, "coach_id")
    f = _fields(page)
    with _write(page):
        service.update_coach(page.conn, coach_id, f)
    return page.redirect("coaches:detail", coach_id=coach_id, flash="Saved.")


@web.page
def status(page: web.Page) -> Response:
    coach_id = _id(page, "coach_id")
    new_status = page.field("status")
    with _write(page):
        service.set_status(page.conn, coach_id, new_status)
    return page.redirect("coaches:detail", coach_id=coach_id, flash=f"Now {new_status}.")


@web.page
def standard(page: web.Page) -> Response:
    coach_id = _id(page, "coach_id")
    target = page.field("standard_id")
    if not UUID.match(target):
        raise web.UserError("Choose a standard.")
    with _write(page):
        service.move_to_standard(page.conn, coach_id, target)
    return page.redirect("coaches:detail", coach_id=coach_id, flash="Moved to that standard.")


@web.page
def verify(page: web.Page) -> Response:
    coach_id, item_id = _id(page, "coach_id"), _id(page, "item_id")
    with _write(page):
        if page.field("action") == "unverify":
            service.unverify_item(page.conn, coach_id, item_id)
            msg = "Marked not verified."
        else:
            service.verify_item(page.conn, coach_id, item_id, page.founder.actor_id)
            msg = "Verified."
    return page.redirect("coaches:detail", coach_id=coach_id, flash=msg)


@web.page
def note(page: web.Page) -> Response:
    coach_id, item_id = _id(page, "coach_id"), _id(page, "item_id")
    with _write(page):
        service.add_note(page.conn, coach_id, item_id, page.field("note"))
    return page.redirect("coaches:detail", coach_id=coach_id, flash="Note added.")


@web.page
def upload(page: web.Page) -> Response:
    coach_id, item_id = _id(page, "coach_id"), _id(page, "item_id")
    upload_file = page.form.get("file") if page.form else None
    if not isinstance(upload_file, UploadFile) or not upload_file.filename:
        raise web.UserError("Choose a file to upload.")
    settings = web.state(page.request).settings
    max_bytes = settings.console_max_upload_mb * 1024 * 1024
    with _write(page):
        service._item(page.conn, coach_id, item_id)
        try:
            with evidence.store(
                settings.private_data_dir,
                coach_id,
                item_id,
                upload_file.filename,
                upload_file.file,
                max_bytes,
            ) as rec:
                page.conn.execute(
                    """INSERT INTO vetting_evidence (vetting_item_id, kind, relative_path,
                           display_name, content_type, sha256, size_bytes, uploaded_by)
                       VALUES (%s, 'file', %s, %s, %s, %s, %s, %s)""",
                    (
                        item_id,
                        rec["relative_path"],
                        rec["display_name"],
                        rec["content_type"],
                        rec["sha256"],
                        rec["size_bytes"],
                        page.founder.actor_id,
                    ),
                )
        except OSError:
            raise web.UserError("The file could not be saved on the workstation.") from None
    return page.redirect("coaches:detail", coach_id=coach_id, flash="File added.")


@web.page
def download(page: web.Page) -> Response:
    evidence_id = _id(page, "evidence_id")
    row = page.one(
        """SELECT relative_path, display_name, content_type, sha256 FROM vetting_evidence
            WHERE id = %s AND kind = 'file' AND removed_at IS NULL""",
        (evidence_id,),
    )
    if row is None:
        raise web.UserError("That file is not available.")
    try:
        path = evidence.resolve(
            web.state(page.request).settings.private_data_dir, row["relative_path"]
        )
    except service.CoachError as exc:
        raise web.UserError(str(exc)) from None
    if not evidence.matches(path, row["sha256"]):
        # The file on disk is not the one a founder uploaded and verified. Never serve it.
        log.warning("evidence hash mismatch", extra={"status": "refused"})
        try:
            web.state(page.request).alert(
                format_category("evidence_changed_on_disk", {"evidence": evidence_id})
            )
        except ValueError:
            pass
        raise web.UserError(
            "This file has changed on the workstation since it was uploaded, so it is not shown."
            " Slack has been alerted; restore it from the backup (runbook, Coaches and vetting"
            " evidence)."
        )
    inline = path.suffix.lower() in evidence.INLINE
    return FileResponse(
        path,
        media_type=row["content_type"] or "application/octet-stream",
        filename=row["display_name"] or path.name,
        content_disposition_type="inline" if inline else "attachment",
    )


@web.page
def remove(page: web.Page) -> Response:
    coach_id, evidence_id = _id(page, "coach_id"), _id(page, "evidence_id")
    with _write(page):
        service.remove_evidence(page.conn, coach_id, evidence_id)
    return page.redirect("coaches:detail", coach_id=coach_id, flash="Evidence removed.")


# ------------------------------------------------------------------------- import
@web.page
def import_form(page: web.Page) -> Response:
    return page.render("coaches/import.html", rows=None, csv_text="", columns=service.CSV_COLUMNS)


@web.page
def import_preview(page: web.Page) -> Response:
    upload_file = page.form.get("file") if page.form else None
    if isinstance(upload_file, UploadFile) and upload_file.filename:
        raw = upload_file.file.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise web.UserError("The CSV is larger than 1 MB.")
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = page.field("csv_text")
    if not text.strip():
        raise web.UserError("Choose a CSV file.")
    try:
        rows = service.parse_csv(text)
    except service.CoachError as exc:
        raise web.UserError(str(exc)) from None
    if page.field("confirm") == "yes":
        with _write(page):
            n = service.import_rows(page.conn, rows)
        return page.redirect("coaches:index", flash=f"Imported {n} coaches as prospects.")
    return page.render("coaches/import.html", rows=rows, csv_text=text, columns=service.CSV_COLUMNS)


# ---------------------------------------------------------------------- readiness
@web.page
def readiness(page: web.Page) -> Response:
    required = int(business_settings.get(page.conn, "coaches.required_per_need_state"))
    page.conn.commit()
    data = service.readiness(page.conn, required)
    page.conn.commit()
    standard_active = page.one("SELECT version FROM vetting_standards WHERE status = 'active'")
    return page.render(
        "coaches/readiness.html", required=required, standard_active=standard_active, **data
    )


# ---------------------------------------------------------------------- standards
@web.page
def standards(page: web.Page) -> Response:
    rows = page.query(
        """SELECT s.*, a.name AS activated_by_name,
                  (SELECT count(*) FROM vetting_item_types t WHERE t.standard_id = s.id) AS items,
                  (SELECT count(*) FROM coaches c WHERE c.vetting_standard_id = s.id) AS coaches
             FROM vetting_standards s LEFT JOIN actors a ON a.id = s.activated_by
            ORDER BY s.version DESC"""
    )
    return page.render("coaches/standards.html", standards=rows)


@web.page
def standard_new(page: web.Page) -> Response:
    with _write(page):
        sid = service.new_draft(page.conn, page.field("title"))
    return page.redirect("coaches:standard_detail", standard_id=sid, flash="Draft created.")


@web.page
def standard_detail(page: web.Page) -> Response:
    sid = _id(page, "standard_id")
    std = page.one(
        """SELECT s.*, a.name AS activated_by_name FROM vetting_standards s
             LEFT JOIN actors a ON a.id = s.activated_by WHERE s.id = %s""",
        (sid,),
    )
    if std is None:
        raise web.UserError("Unknown standard.")
    types = page.query(
        "SELECT * FROM vetting_item_types WHERE standard_id = %s ORDER BY sort_order, key", (sid,)
    )
    return page.render("coaches/standard.html", std=std, types=types)


@web.page
def standard_item(page: web.Page) -> Response:
    sid = _id(page, "standard_id")
    with _write(page):
        if page.field("delete"):
            if not UUID.match(page.field("delete")):
                raise web.UserError("Unknown item.")
            service.delete_item_type(page.conn, sid, page.field("delete"))
            msg = "Item removed."
        else:
            service.save_item_type(page.conn, sid, page.field)
            msg = "Item saved."
    return page.redirect("coaches:standard_detail", standard_id=sid, flash=msg)


@web.page
def standard_state(page: web.Page) -> Response:
    sid = _id(page, "standard_id")
    action = page.field("action")
    with _write(page):
        if action == "activate":
            service.activate_standard(page.conn, sid)
            msg = "This standard is now active. New vetting uses it."
        elif action == "discard":
            service.discard_draft(page.conn, sid)
            msg = "Draft discarded."
        else:
            raise web.UserError("Unknown action.")
    return page.redirect("coaches:standard_detail", standard_id=sid, flash=msg)


routes = [
    Route("/", index, name="index"),
    Route("/new", new, name="new"),
    Route("/new", create, methods=["POST"], name="create"),
    Route("/import", import_form, name="import"),
    Route("/import", import_preview, methods=["POST"], name="import_preview"),
    Route("/readiness", readiness, name="readiness"),
    Route("/standards", standards, name="standards"),
    Route("/standards", standard_new, methods=["POST"], name="standard_new"),
    Route("/standards/{standard_id}", standard_detail, name="standard_detail"),
    Route("/standards/{standard_id}/items", standard_item, methods=["POST"], name="standard_item"),
    Route(
        "/standards/{standard_id}/state", standard_state, methods=["POST"], name="standard_state"
    ),
    Route("/evidence/{evidence_id}", download, name="download"),
    Route("/{coach_id}", detail, name="detail"),
    Route("/{coach_id}", update, methods=["POST"], name="update"),
    Route("/{coach_id}/status", status, methods=["POST"], name="status"),
    Route("/{coach_id}/standard", standard, methods=["POST"], name="standard"),
    Route("/{coach_id}/items/{item_id}/verify", verify, methods=["POST"], name="verify"),
    Route("/{coach_id}/items/{item_id}/note", note, methods=["POST"], name="note"),
    Route("/{coach_id}/items/{item_id}/upload", upload, methods=["POST"], name="upload"),
    Route("/{coach_id}/evidence/{evidence_id}/remove", remove, methods=["POST"], name="remove"),
]
