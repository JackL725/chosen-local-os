"""What a console screen uses: the signed-in founder, the Ledger, forms and templates.

A screen is a plain function decorated with `@web.page`. It receives a `Page` and
returns a response:

    @web.page
    def index(page: web.Page) -> Response:
        coaches = page.conn.execute("SELECT ...").fetchall()
        return page.render("coaches/index.html", coaches=coaches)

    @web.page
    def save(page: web.Page) -> Response:
        with page.write():            # a transaction acting as the signed-in founder
            page.conn.execute("UPDATE ...", (page.form["x"],))
        return page.redirect("coaches:index", flash="Saved.")

The decorator signs the founder in from the session cookie (and the Tailscale header),
checks CSRF on every POST, reads the form, runs the function in a worker thread with a
pooled connection, turns the Ledger's refusals into a readable message, and never logs
request data. Screens of a system that is turned off answer 404.
"""

from __future__ import annotations

import functools
import re
import urllib.parse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.templating import Jinja2Templates

from review import auth
from worker.db import transaction
from worker.logs import get_logger

log = get_logger("review")

FLASH_COOKIE = "cl_flash"
_SYSTEM_PATH = re.compile(r"^/s/([a-z][a-z0-9_]*)(/|$)")
# Postgres errors raised by the Ledger's own rules; their message is ours and safe to show.
LEDGER_REFUSALS = (
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
    psycopg.errors.UniqueViolation,
    psycopg.errors.NotNullViolation,
    psycopg.errors.ForeignKeyViolation,
    psycopg.errors.RaiseException,
)


class UserError(Exception):
    """A problem with what the founder entered. The message is shown to them."""


@dataclass
class ConsoleState:
    """Set on app.state by review.app.build_app."""

    pool: ConnectionPool
    templates: Jinja2Templates
    settings: Any
    alert: Callable[[str], None]
    nav: list[dict[str, str]] = field(default_factory=list)


def state(request: Request) -> ConsoleState:
    return request.app.state.console


def path_for(request: Request, name: str, **params: Any) -> str:
    """A relative URL path. Never absolute: Tailscale Serve may rewrite the Host header."""
    return str(request.app.url_path_for(name, **params))


@dataclass
class Page:
    request: Request
    founder: auth.Founder
    conn: psycopg.Connection
    form: FormData | None
    csrf: str
    flash: str | None = None
    flash_kind: str = "ok"
    _set_flash: tuple[str, str] | None = None

    @contextmanager
    def write(self, **context: Any) -> Iterator[psycopg.Connection]:
        """A transaction acting as the signed-in founder (the Ledger records who)."""
        with transaction(self.conn, actor=self.founder.name, **context):
            yield self.conn

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        self.conn.commit()
        return rows

    def one(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def field(self, name: str, default: str = "") -> str:
        if self.form is None:
            return default
        value = self.form.get(name, default)
        return value.strip() if isinstance(value, str) else default

    def path(self, name: str, **params: Any) -> str:
        return path_for(self.request, name, **params)

    def render(self, template: str, *, status_code: int = 200, **context: Any) -> Response:
        st = state(self.request)
        context.update(
            request=self.request,
            founder=self.founder,
            csrf=self.csrf,
            nav=_nav(self),
            business_name=_business_name(self),
            tz=_time_zone(self),
            flash=self.flash,
            flash_kind=self.flash_kind,
        )
        resp = st.templates.TemplateResponse(
            self.request, template, context, status_code=status_code
        )
        if self.flash is not None:
            resp.delete_cookie(FLASH_COOKIE, path="/")
        return resp

    def redirect(
        self, name: str, *, flash: str | None = None, error: bool = False, **params: Any
    ) -> Response:
        target = name if name.startswith("/") else self.path(name, **params)
        resp = RedirectResponse(target, status_code=303)
        if flash:
            _flash_cookie(resp, flash, "error" if error else "ok")
        return resp


def _flash_cookie(resp: Response, text: str, kind: str) -> None:
    value = urllib.parse.quote(f"{kind}:{text[:300]}", safe="")
    resp.set_cookie(
        FLASH_COOKIE, value, max_age=60, path="/", secure=True, httponly=True, samesite="strict"
    )


def _read_flash(request: Request) -> tuple[str | None, str]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None, "ok"
    kind, _, text = urllib.parse.unquote(raw).partition(":")
    return (text or None), ("error" if kind == "error" else "ok")


def _nav(page: Page) -> list[dict[str, str]]:
    enabled = {r["key"] for r in page.query("SELECT key FROM systems WHERE enabled OR required")}
    return [item for item in state(page.request).nav if item["system"] in enabled]


def _business_name(page: Page) -> str:
    row = page.one("SELECT value #>> '{}' AS v FROM settings WHERE key = 'core.business_name'")
    return (row or {}).get("v") or "Review console"


def _time_zone(page: Page) -> str:
    rows = page.query("SELECT time_zone FROM markets WHERE active ORDER BY key LIMIT 2")
    return rows[0]["time_zone"] if len(rows) == 1 else "UTC"


def tailscale_login(request: Request) -> str | None:
    value = request.headers.get(auth.TAILSCALE_LOGIN_HEADER, "").strip()
    return value or None


def _cross_site(request: Request) -> bool:
    return request.headers.get("sec-fetch-site", "") == "cross-site"


def page(fn: Callable[[Page], Response]) -> Callable[[Request], Any]:
    """Make `fn(page) -> Response` a signed-in console screen (see the module docstring)."""

    @functools.wraps(fn)
    async def endpoint(request: Request) -> Response:
        st = state(request)
        form: FormData | None = None
        if request.method == "POST":
            if _cross_site(request):
                return _plain(request, 403, "Refused: this form came from another site.")
            try:
                form = await request.form(max_files=10, max_fields=200, max_part_size=1024 * 1024)
            except Exception as exc:  # noqa: BLE001 - malformed or oversized forms
                log.warning("form rejected", extra={"error_class": type(exc).__name__})
                return _plain(request, 400, "That form could not be read. Is the file too large?")
        flash, flash_kind = _read_flash(request)
        try:
            return await run_in_threadpool(_run, fn, request, st, form, flash, flash_kind)
        finally:
            if form is not None:
                await form.close()

    return endpoint


def _run(
    fn: Callable[[Page], Response],
    request: Request,
    st: ConsoleState,
    form: FormData | None,
    flash: str | None,
    flash_kind: str,
) -> Response:
    token = request.cookies.get(auth.SESSION_COOKIE)
    with st.pool.connection() as conn:
        conn.row_factory = dict_row
        founder = auth.lookup(conn, token, tailscale_login=tailscale_login(request))
        if founder is None or token is None:
            if request.method == "POST":
                return _plain(request, 403, "Your session has ended. Sign in again.")
            nxt = urllib.parse.quote(request.url.path, safe="/")
            return RedirectResponse(f"{path_for(request, 'signin')}?next={nxt}", status_code=303)
        csrf = auth.csrf_for(token)
        if form is not None and not _same(str(form.get("csrf", "")), csrf):
            return _plain(request, 403, "This form has expired. Go back, reload and try again.")
        m = _SYSTEM_PATH.match(request.url.path)
        if m:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT enabled OR required AS is_on FROM systems WHERE key = %s",
                    (m.group(1),),
                )
                row = cur.fetchone()
            conn.commit()
            if not row or not row["is_on"]:
                return _plain(request, 404, "That system is turned off. Turn it on under Systems.")
        p = Page(request, founder, conn, form, csrf, flash, flash_kind)
        try:
            return fn(p)
        except UserError as exc:
            conn.rollback()
            return _back(p, str(exc))
        except LEDGER_REFUSALS as exc:
            conn.rollback()
            message = exc.diag.message_primary or "The Ledger refused that change."
            return _back(p, f"The Ledger refused that: {message}")
        except Exception as exc:  # noqa: BLE001 - never log request data or tracebacks
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.rollback()
            log.error("console page failed", extra={"error_class": type(exc).__name__})
            return _plain(request, 500, "Something went wrong. Nothing was saved.")


def _back(p: Page, message: str) -> Response:
    """After a refused POST, go back to the page the form was on, with the message."""
    if p.request.method != "POST":
        return p.render("error.html", status_code=400, message=message)
    target = p.request.url.path
    referer = p.request.headers.get("referer", "")
    if referer:
        ref = urllib.parse.urlsplit(referer)
        if ref.path.startswith("/"):
            target = ref.path + (f"?{ref.query}" if ref.query else "")
    resp = RedirectResponse(target, status_code=303)
    _flash_cookie(resp, message, "error")
    return resp


def _same(a: str, b: str) -> bool:
    import hmac

    return bool(a) and hmac.compare_digest(a, b)


def _plain(request: Request, status: int, message: str) -> Response:
    try:
        st = state(request)
        return st.templates.TemplateResponse(
            request,
            "plain.html",
            {"message": message, "status": status, "request": request},
            status_code=status,
        )
    except Exception:  # noqa: BLE001 - last resort
        return HTMLResponse(message, status_code=status)
