"""The review console: a Starlette app on 127.0.0.1, published to the founders'
devices by Tailscale Serve on the Windows host (deploy/README.md).

    uv run python -m review                 # dev; production: the cl-review systemd unit

It shows call content, safety flags and drafts from later phases, so: loopback only, a
founder signed in on every screen but /healthz and /signin, no per-request logs, nothing
cached, and nothing loaded from another site.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import jinja2
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import systems
from review import core_views, signin, web
from review.config import ConsoleSettings, get_settings
from worker import logs
from worker.db import connect
from worker.slack import SlackWebhook
from worker.system_sync import sync_systems

log = logs.get_logger("review")

ROOT = Path(__file__).resolve().parent.parent
CORE_TEMPLATES = Path(__file__).resolve().parent / "templates"

SECURITY_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (
        b"content-security-policy",
        b"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        b"form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    ),
]


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [h for h in message.get("headers", []) if h[0] != b"cache-control"]
                message["headers"] = headers + SECURITY_HEADERS
            await send(message)

        await self.app(scope, receive, with_headers)


async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True, "service": "review-console"})


def _templates() -> Jinja2Templates:
    dirs = [CORE_TEMPLATES]
    for key in systems.all_manifests():
        d = ROOT / "systems" / key / "templates"
        if d.is_dir():
            dirs.append(d)
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader([str(d) for d in dirs]),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
    )
    templates = Jinja2Templates(env=env)

    @jinja2.pass_context
    def path_for(context: dict, name: str, /, **params: object) -> str:
        return web.path_for(context["request"], name, **params)

    @jinja2.pass_context
    def when(context: dict, value: object, fmt: str = "%b %d %H:%M") -> str:
        """A timestamp in the business's time zone (the single active market's)."""
        if value is None:
            return ""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        if not isinstance(value, datetime):
            return str(value)
        tz = context.get("tz") or "UTC"
        return value.astimezone(ZoneInfo(tz)).strftime(fmt)

    env.globals["url_for"] = path_for
    env.globals["path_for"] = path_for
    env.filters["when"] = when
    return templates


def _routes() -> tuple[list[Route | Mount], list[dict[str, str]]]:
    routes: list[Route | Mount] = [
        Route("/healthz", healthz, name="healthz"),
        Route("/signin", signin.signin, methods=["GET", "POST"], name="signin"),
        Route("/signout", signin.signout, methods=["POST"], name="signout"),
        Route("/", core_views.home, name="home"),
        Route("/systems", core_views.systems_index, name="systems"),
        Route("/systems/{key}", core_views.systems_toggle, methods=["POST"], name="systems_toggle"),
        Route("/settings", core_views.settings_index, name="settings"),
        Route("/settings", core_views.settings_save, methods=["POST"], name="settings_save"),
        Route("/need-states", core_views.need_states, name="need_states"),
        Route("/need-states", core_views.need_state_save, methods=["POST"], name="need_state_save"),
        Route(
            "/need-states/launch",
            core_views.need_state_launch,
            methods=["POST"],
            name="need_state_launch",
        ),
        Route("/markets", core_views.markets, name="markets"),
        Route("/markets", core_views.market_save, methods=["POST"], name="market_save"),
        Route("/account", core_views.account, name="account"),
        Route(
            "/account/password",
            core_views.account_password,
            methods=["POST"],
            name="account_password",
        ),
        Route(
            "/account/revoke", core_views.account_revoke, methods=["POST"], name="account_revoke"
        ),
        Route("/history/{entity_type}/{entity_id}", core_views.timeline, name="timeline"),
        Route("/jobs/{job_id}", core_views.job, name="job"),
    ]
    nav: list[dict[str, str]] = []
    for key, m in systems.all_manifests().items():
        if m.console is None:
            continue
        sub = systems.load_target(m.console.routes)
        routes.append(Mount(f"/s/{key}", routes=list(sub), name=key))
        nav.append(
            {
                "system": key,
                "title": m.console.title,
                "path": f"/s/{key}/",
                "order": str(m.console.nav_order),
            }
        )
    nav.sort(key=lambda n: (int(n["order"]), n["title"]))
    return routes, nav


def build_app(
    settings: ConsoleSettings | None = None,
    *,
    alert: Callable[[str], None] | None = None,
) -> Starlette:
    settings = settings or get_settings()
    pool = ConnectionPool(
        settings.console_database_url,
        min_size=1,
        max_size=4,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    routes, nav = _routes()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        with connect(settings.console_database_url) as conn:
            sync_systems(conn, actor=signin.auth.SYSTEM_ACTOR)
        pool.open(wait=True, timeout=30)
        try:
            yield
        finally:
            pool.close()

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        max_body_size=(settings.console_max_upload_mb + 1) * 1024 * 1024,
    )
    app.state.console = web.ConsoleState(
        pool=pool,
        templates=_templates(),
        settings=settings,
        alert=alert or SlackWebhook(settings.slack_alert_webhook_url),
        nav=nav,
    )
    app.add_middleware(SecurityHeaders)
    return app


def serve() -> None:
    import uvicorn

    logs.configure()
    settings = get_settings()
    app = build_app(settings)
    log.info("review console listening", extra={"port": settings.review_console_port})
    uvicorn.run(
        app,
        host=settings.review_console_host,
        port=settings.review_console_port,
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
    )
