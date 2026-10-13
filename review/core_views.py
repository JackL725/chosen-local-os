"""The console's core screens: home, systems, settings, need-states, markets, account,
a record's event timeline and a job's status. Systems add their own under /s/<key>/."""

from __future__ import annotations

import re
from typing import Any

from starlette.responses import Response

import systems
from review import auth, web
from review.passwords import WeakPassword
from worker import business_settings
from worker.config import MODEL_PROFILES

KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
# Records whose history the timeline may show (table names, as events.entity_type).
TIMELINE_TYPES = frozenset(
    {
        "coaches",
        "vetting_items",
        "vetting_evidence",
        "vetting_standards",
        "vetting_item_types",
        "need_states",
        "market_need_states",
        "markets",
        "settings",
        "systems",
        "jobs",
        "job",
    }
)


# --------------------------------------------------------------------------- home
@web.page
def home(page: web.Page) -> Response:
    queue = page.one(
        """SELECT
             count(*) FILTER (WHERE status = 'pending')                         AS pending,
             count(*) FILTER (WHERE status = 'running')                         AS running,
             count(*) FILTER (WHERE status = 'failed'
                              AND finished_at > now() - interval '24 hours')    AS failed_24h,
             (extract(epoch FROM now() - min(run_after) FILTER (
                 WHERE status = 'pending' AND run_after <= now())) / 60)::int   AS oldest_min
           FROM jobs"""
    )
    heartbeat = page.one(
        """SELECT worker_id, git_sha,
                  (extract(epoch FROM now() - last_seen_at) / 60)::int AS age_min
             FROM worker_heartbeats ORDER BY last_seen_at DESC LIMIT 1"""
    )
    failed = page.query(
        """SELECT id, job_type, error_class, finished_at FROM jobs
            WHERE status = 'failed' ORDER BY finished_at DESC LIMIT 10"""
    )
    threshold = business_settings.get(page.conn, "core.stale_job_alert_hours")
    page.conn.commit()
    off = page.query("SELECT key, title FROM systems WHERE NOT (enabled OR required) ORDER BY key")
    return page.render(
        "home.html",
        queue=queue,
        heartbeat=heartbeat,
        failed=failed,
        threshold_hours=threshold,
        off=off,
    )


# ------------------------------------------------------------------------ systems
@web.page
def systems_index(page: web.Page) -> Response:
    rows = {r["key"]: r for r in page.query("SELECT * FROM systems ORDER BY key")}
    items: list[dict[str, Any]] = []
    for key, m in systems.all_manifests().items():
        row = rows.get(key)
        items.append(
            {
                "manifest": m,
                "registered": row is not None,
                "enabled": bool(row and (row["enabled"] or row["required"])),
                "changed_at": row["enabled_changed_at"] if row else None,
                "models": [
                    (u.name, u.profile, MODEL_PROFILES.get(u.profile, "?")) for u in m.models
                ],
            }
        )
    return page.render("systems.html", items=items)


@web.page
def systems_toggle(page: web.Page) -> Response:
    key = page.request.path_params["key"]
    if not KEY.match(key):
        raise web.UserError("Unknown system.")
    turn_on = page.field("enabled") == "on"
    with page.write():
        cur = page.conn.execute(
            "UPDATE systems SET enabled = %s WHERE key = %s RETURNING title", (turn_on, key)
        )
        row = cur.fetchone()
    if row is None:
        raise web.UserError("That system has not registered yet. Start the worker once.")
    state = "on" if turn_on else "off"
    return page.redirect("systems", flash=f"{row['title']} is {state}.")


# ----------------------------------------------------------------------- settings
def _groups() -> list[tuple[str, list[Any]]]:
    titles = {"core": "Core", "console": "Console"}
    titles.update({k: m.title for k, m in systems.all_manifests().items()})
    groups: dict[str, list[Any]] = {}
    for spec in business_settings.all_specs().values():
        groups.setdefault(spec.key.split(".")[0], []).append(spec)
    return [(titles.get(prefix, prefix), specs) for prefix, specs in groups.items()]


@web.page
def settings_index(page: web.Page) -> Response:
    values = business_settings.current(page.conn)
    page.conn.commit()
    return page.render("settings.html", groups=_groups(), values=values)


@web.page
def settings_save(page: web.Page) -> Response:
    key = page.field("key")
    try:
        spec = business_settings.spec(key)
        with page.write():
            value = business_settings.put(page.conn, key, page.field("value"))
    except business_settings.InvalidSetting as exc:
        raise web.UserError(str(exc)) from None
    return page.redirect("settings", flash=f"{spec.label}: saved ({value}).")


# -------------------------------------------------------------------- need-states
@web.page
def need_states(page: web.Page) -> Response:
    states = page.query("SELECT * FROM need_states ORDER BY sort_order, key")
    markets = page.query("SELECT * FROM markets ORDER BY active DESC, name")
    launched = {
        (r["market_key"], r["need_state_key"])
        for r in page.query(
            "SELECT market_key, need_state_key FROM market_need_states WHERE launched"
        )
    }
    counts = {
        r["need_state_key"]: r["n"]
        for r in page.query(
            """SELECT cn.need_state_key, count(*) AS n FROM coach_need_states cn
                 JOIN coaches c ON c.id = cn.coach_id WHERE c.status <> 'removed'
                GROUP BY 1"""
        )
    }
    return page.render(
        "need_states.html", states=states, markets=markets, launched=launched, counts=counts
    )


@web.page
def need_state_save(page: web.Page) -> Response:
    key = page.field("key").lower()
    label = page.field("label")
    if not re.match(r"^[a-z][a-z0-9_]*$", key) or not label:
        raise web.UserError(
            "A need-state needs a short key (lowercase letters, digits, _) and a label."
        )
    values = (
        label,
        page.field("description") or None,
        page.field("covers") or None,
        page.field("not_covered") or None,
        int(page.field("sort_order") or 100),
        page.field("active", "on") == "on",
    )
    with page.write():
        page.conn.execute(
            """INSERT INTO need_states
                   (key, label, description, covers, not_covered, sort_order, active)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET label = EXCLUDED.label,
                   description = EXCLUDED.description, covers = EXCLUDED.covers,
                   not_covered = EXCLUDED.not_covered, sort_order = EXCLUDED.sort_order,
                   active = EXCLUDED.active""",
            (key, *values),
        )
    return page.redirect("need_states", flash=f"Need-state {label} saved.")


@web.page
def need_state_launch(page: web.Page) -> Response:
    market, key = page.field("market"), page.field("need_state")
    launched = page.field("launched") == "on"
    with page.write():
        page.conn.execute(
            """INSERT INTO market_need_states (market_key, need_state_key, launched)
               VALUES (%s, %s, %s)
               ON CONFLICT (market_key, need_state_key)
               DO UPDATE SET launched = EXCLUDED.launched""",
            (market, key, launched),
        )
    word = "launched in" if launched else "no longer launched in"
    return page.redirect("need_states", flash=f"{key} {word} {market}.")


# ------------------------------------------------------------------------ markets
@web.page
def markets(page: web.Page) -> Response:
    rows = page.query(
        """SELECT m.*, (SELECT count(*) FROM coaches c WHERE c.market_key = m.key) AS coaches
             FROM markets m ORDER BY m.active DESC, m.name"""
    )
    return page.render("markets.html", markets=rows)


@web.page
def market_save(page: web.Page) -> Response:
    key, name, tz = page.field("key").lower(), page.field("name"), page.field("time_zone")
    if not re.match(r"^[a-z][a-z0-9_]*$", key) or not name or not tz:
        raise web.UserError("A market needs a key (lowercase), a name and a time zone.")
    with page.write():
        page.conn.execute(
            """INSERT INTO markets (key, name, time_zone, active) VALUES (%s, %s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name,
                   time_zone = EXCLUDED.time_zone, active = EXCLUDED.active""",
            (key, name, tz, page.field("active", "on") == "on"),
        )
    return page.redirect("markets", flash=f"Market {name} saved.")


# ------------------------------------------------------------------------ account
@web.page
def account(page: web.Page) -> Response:
    sessions = page.query(
        """SELECT id, local, created_at, last_seen_at, expires_at FROM console_sessions
            WHERE console_user_id = %s AND revoked_at IS NULL AND expires_at > now()
            ORDER BY last_seen_at DESC""",
        (page.founder.console_user_id,),
    )
    user = page.one(
        """SELECT tailscale_login, allow_local_signin, password_changed_at
             FROM console_users WHERE id = %s""",
        (page.founder.console_user_id,),
    )
    return page.render("account.html", sessions=sessions, user=user)


@web.page
def account_password(page: web.Page) -> Response:
    new, again = page.field("new_password"), page.field("new_password_again")
    if new != again:
        raise web.UserError("The two new passwords are different.")
    try:
        auth.change_password(page.conn, page.founder, page.field("current_password"), new)
    except (auth.SignInRefused, WeakPassword) as exc:
        raise web.UserError(str(exc)) from None
    return page.redirect("account", flash="Password changed. Your other sessions were signed out.")


@web.page
def account_revoke(page: web.Page) -> Response:
    sid = page.field("session_id")
    auth.sign_out(page.conn, page.founder, session_id=sid)
    return page.redirect("account", flash="That session was signed out.")


# ----------------------------------------------------------------------- timeline
@web.page
def timeline(page: web.Page) -> Response:
    entity_type = page.request.path_params["entity_type"]
    entity_id = page.request.path_params["entity_id"]
    if entity_type not in TIMELINE_TYPES or not re.match(r"^[A-Za-z0-9_.:-]{1,80}$", entity_id):
        raise web.UserError("There is no history for that.")
    events = page.query(
        """SELECT e.occurred_at, e.event_type, a.name AS actor, a.kind AS actor_kind,
                  e.payload, e.prompt_version, e.model, e.job_id
             FROM events e JOIN actors a ON a.id = e.actor_id
            WHERE e.entity_type = %s AND e.entity_id = %s
            ORDER BY e.id DESC LIMIT 200""",
        (entity_type, entity_id),
    )
    return page.render("timeline.html", events=events, entity_type=entity_type, entity_id=entity_id)


# --------------------------------------------------------------------------- jobs
@web.page
def job(page: web.Page) -> Response:
    job_id = page.request.path_params["job_id"]
    if not re.match(r"^[0-9a-f-]{36}$", job_id):
        raise web.UserError("Unknown job.")
    row = page.one(
        """SELECT id, job_type, status, attempts, max_attempts, claimed_by, claimed_at, run_after,
                  error_class, error_detail, finished_at, created_at, entity_type, entity_id
             FROM jobs WHERE id = %s""",
        (job_id,),
    )
    if row is None:
        raise web.UserError("Unknown job.")
    return page.render("job.html", job=row)
