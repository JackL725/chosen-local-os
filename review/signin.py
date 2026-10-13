"""Sign-in and sign-out. The only screens that work without a session."""

from __future__ import annotations

import hmac
import secrets
import urllib.parse

from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from review import auth, web
from worker.logs import get_logger
from worker.slack import format_category

log = get_logger("review")

PRE_CSRF_COOKIE = "cl_signin_csrf"


def _safe_next(value: str | None) -> str:
    """Only same-site paths: never redirect to another host after sign-in."""
    if not value:
        return "/"
    path = urllib.parse.unquote(value)
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


def _form_page(request: Request, *, error: str | None = None, status: int = 200) -> Response:
    st = web.state(request)
    token = secrets.token_urlsafe(24)
    login = web.tailscale_login(request)
    local_ok = login is None and st.settings.console_allow_local_signin
    resp = st.templates.TemplateResponse(
        request,
        "signin.html",
        {
            "request": request,
            "tailscale_login": login,
            "local_ok": local_ok,
            "csrf": token,
            "error": error,
            "next": _safe_next(request.query_params.get("next")),
        },
        status_code=status,
    )
    resp.set_cookie(
        PRE_CSRF_COOKIE, token, max_age=900, path="/", secure=True, httponly=True, samesite="strict"
    )
    return resp


async def signin(request: Request) -> Response:
    if request.method == "GET":
        return _form_page(request)
    if request.headers.get("sec-fetch-site", "") == "cross-site":
        return _form_page(request, error="Refused: this form came from another site.", status=403)
    form = await request.form(max_files=0, max_fields=10, max_part_size=4096)
    cookie = request.cookies.get(PRE_CSRF_COOKIE, "")
    sent = str(form.get("csrf", ""))
    if not cookie or not hmac.compare_digest(cookie, sent):
        return _form_page(request, error="The form expired. Try again.", status=403)
    st = web.state(request)
    login = web.tailscale_login(request)
    allow_local = login is None and bool(st.settings.console_allow_local_signin)
    name = str(form.get("name", "")) or None
    password = str(form.get("password", ""))
    nxt = _safe_next(str(form.get("next", "")))

    def attempt() -> auth.SignInResult:
        with st.pool.connection() as conn:
            conn.row_factory = dict_row
            return auth.sign_in(
                conn, tailscale_login=login, name=name, password=password, allow_local=allow_local
            )

    try:
        result = await run_in_threadpool(attempt)
    except auth.SignInRefused as exc:
        if exc.locked_user_id:
            try:
                st.alert(
                    format_category("console_signin_locked", {"console_user": exc.locked_user_id})
                )
            except ValueError:
                pass
        log.warning("console sign-in refused", extra={"status": "refused"})
        return _form_page(request, error=str(exc), status=401)
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE, result.token, path="/", secure=True, httponly=True, samesite="strict"
    )
    resp.delete_cookie(PRE_CSRF_COOKIE, path="/")
    log.info("console sign-in", extra={"status": "ok"})
    return resp


@web.page
def signout(page: web.Page) -> Response:
    auth.sign_out(page.conn, page.founder)
    resp = RedirectResponse(page.path("signin"), status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp
