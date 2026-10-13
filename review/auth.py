"""Founder sign-in and sessions for the review console.

Who is signing in is established two ways (docs/phase-1-design.md section 5):

* Remote, through Tailscale Serve: Serve adds `Tailscale-User-Login` and strips any copy
  a client sends. The header picks the founder, and the founder's password is still
  required. Serve sends no identity header for tagged devices, so:
* Local, on the workstation itself (no header): only founders flagged for it, and only
  when CONSOLE_ALLOW_LOCAL_SIGNIN is on, sign in by name and password (decision 11:
  Jack only).

The console listens on loopback only, so the header cannot be forged from the network;
a local process could forge it, which is why the password is always required.

Sessions are Ledger rows keyed by the SHA-256 of a random token held in an HttpOnly
cookie. A session made through Serve is bound to that Tailscale login and is refused
without it; a local session is refused if a header appears.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from review import passwords
from worker import business_settings
from worker.db import transaction

TAILSCALE_LOGIN_HEADER = "tailscale-user-login"
SESSION_COOKIE = "cl_session"
SYSTEM_ACTOR = "review_console"


@dataclass(frozen=True)
class Founder:
    actor_id: str
    name: str
    console_user_id: str
    session_id: str
    local: bool


class SignInRefused(Exception):
    """A sign-in failed. The message is safe to show and never says which part was wrong."""

    def __init__(
        self,
        message: str = "That did not work. Check the name and password.",
        *,
        locked_user_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.locked_user_id = locked_user_id


@dataclass(frozen=True)
class SignInResult:
    founder: Founder
    token: str
    locked_out: bool = False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def csrf_for(session_token: str) -> str:
    """The CSRF token for a session, derived from its cookie token (never stored)."""
    return hmac.new(session_token.encode("ascii"), b"cl-console-csrf", hashlib.sha256).hexdigest()


def _emit(conn: psycopg.Connection, actor: str, entity_id: str, event: str, payload: dict) -> None:
    with transaction(conn, actor=actor), conn.cursor() as cur:
        cur.execute(
            "SELECT ledger_emit('console_user', %s, %s, %s)", (entity_id, event, Jsonb(payload))
        )


def sign_in(
    conn: psycopg.Connection,
    *,
    tailscale_login: str | None,
    name: str | None,
    password: str,
    allow_local: bool,
    now: datetime | None = None,
) -> SignInResult:
    """Check a founder's credentials and open a session. Raises SignInRefused."""
    now = now or datetime.now(UTC)
    settings = business_settings.current(conn)
    conn.commit()
    with conn.cursor() as cur:
        if tailscale_login:
            cur.execute(
                """SELECT u.*, a.name AS actor_name FROM console_users u
                     JOIN actors a ON a.id = u.actor_id
                    WHERE u.tailscale_login = %s
                      AND u.active AND a.active AND a.kind = 'founder'""",
                (tailscale_login,),
            )
        elif allow_local and name:
            cur.execute(
                """SELECT u.*, a.name AS actor_name FROM console_users u
                     JOIN actors a ON a.id = u.actor_id
                    WHERE a.name = %s AND u.allow_local_signin
                      AND u.active AND a.active AND a.kind = 'founder'""",
                (name.strip().lower(),),
            )
        else:
            cur.execute("SELECT NULL WHERE false")
        user = cur.fetchone()
    conn.commit()
    if user is None:
        passwords.dummy_verify(password)
        raise SignInRefused()
    if tailscale_login and name and name.strip().lower() != user["actor_name"]:
        passwords.dummy_verify(password)
        raise SignInRefused()
    if user["locked_until"] is not None and user["locked_until"] > now:
        raise SignInRefused("This account is locked for a few minutes after too many tries.")

    uid = str(user["id"])
    if not passwords.verify(password, user["password_hash"]):
        failed = user["failed_signins"] + 1
        locked = failed >= int(settings["console.max_failed_signins"])
        lock_until = now + timedelta(minutes=int(settings["console.lockout_minutes"]))
        with transaction(conn, actor=SYSTEM_ACTOR), conn.cursor() as cur:
            cur.execute(
                """UPDATE console_users SET failed_signins = %s, locked_until = %s WHERE id = %s""",
                (0 if locked else failed, lock_until if locked else None, uid),
            )
        _emit(conn, SYSTEM_ACTOR, uid, "console.signin_failed", {"locked": locked})
        if locked:
            raise SignInRefused(
                "This account is now locked for a few minutes after too many tries.",
                locked_user_id=uid,
            )
        raise SignInRefused()

    token = secrets.token_urlsafe(32)
    idle = timedelta(hours=int(settings["console.session_idle_hours"]))
    local = not tailscale_login
    with transaction(conn, actor=SYSTEM_ACTOR), conn.cursor() as cur:
        cur.execute(
            "UPDATE console_users SET failed_signins = 0, locked_until = NULL WHERE id = %s"
            " AND (failed_signins <> 0 OR locked_until IS NOT NULL)",
            (uid,),
        )
        cur.execute(
            """INSERT INTO console_sessions (console_user_id, token_sha256, local, expires_at)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (uid, token_hash(token), local, now + idle),
        )
        session_id = str(cur.fetchone()["id"])
    _emit(conn, user["actor_name"], uid, "console.signed_in", {"local": local})
    founder = Founder(str(user["actor_id"]), user["actor_name"], uid, session_id, local)
    return SignInResult(founder=founder, token=token)


def lookup(
    conn: psycopg.Connection,
    token: str | None,
    *,
    tailscale_login: str | None,
    now: datetime | None = None,
) -> Founder | None:
    """The founder behind a session cookie, or None. Extends the idle timeout."""
    if not token:
        return None
    now = now or datetime.now(UTC)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.id AS session_id, s.local, s.last_seen_at, u.id AS user_id,
                      u.tailscale_login, a.id AS actor_id, a.name
                 FROM console_sessions s
                 JOIN console_users u ON u.id = s.console_user_id
                 JOIN actors a ON a.id = u.actor_id
                WHERE s.token_sha256 = %s AND s.revoked_at IS NULL AND s.expires_at > %s
                  AND u.active AND a.active AND a.kind = 'founder'""",
            (token_hash(token), now),
        )
        row = cur.fetchone()
    conn.commit()
    if row is None:
        return None
    if row["local"]:
        if tailscale_login:
            return None
    elif tailscale_login != row["tailscale_login"]:
        return None
    if now - row["last_seen_at"] > timedelta(minutes=1):
        settings = business_settings.current(conn)
        idle = timedelta(hours=int(settings["console.session_idle_hours"]))
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE console_sessions SET last_seen_at = %s, expires_at = %s WHERE id = %s",
                (now, now + idle, row["session_id"]),
            )
        conn.commit()
    return Founder(
        str(row["actor_id"]), row["name"], str(row["user_id"]), str(row["session_id"]), row["local"]
    )


def sign_out(conn: psycopg.Connection, founder: Founder, *, session_id: str | None = None) -> None:
    sid = session_id or founder.session_id
    with transaction(conn, actor=founder.name), conn.cursor() as cur:
        cur.execute(
            """UPDATE console_sessions SET revoked_at = now()
                WHERE id = %s AND console_user_id = %s AND revoked_at IS NULL""",
            (sid, founder.console_user_id),
        )
        cur.execute(
            "SELECT ledger_emit('console_user', %s, 'console.signed_out', %s)",
            (founder.console_user_id, Jsonb({"other_session": sid != founder.session_id})),
        )


def change_password(conn: psycopg.Connection, founder: Founder, current: str, new: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT password_hash FROM console_users WHERE id = %s", (founder.console_user_id,)
        )
        row = cur.fetchone()
    conn.commit()
    if row is None or not passwords.verify(current, row["password_hash"]):
        raise SignInRefused("The current password is not right.")
    new_hash = passwords.hash_password(new)
    with transaction(conn, actor=founder.name), conn.cursor() as cur:
        cur.execute(
            """UPDATE console_users SET password_hash = %s, password_changed_at = now()
                WHERE id = %s""",
            (new_hash, founder.console_user_id),
        )
        # Every other session ends when the password changes.
        cur.execute(
            """UPDATE console_sessions SET revoked_at = now()
                WHERE console_user_id = %s AND id <> %s AND revoked_at IS NULL""",
            (founder.console_user_id, founder.session_id),
        )
