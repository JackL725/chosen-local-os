"""Founder logins for the review console. Jack runs this on the workstation as clworker:

    sudo -u clworker /opt/chosen-local/scripts/console_admin.sh create-user jack --allow-local
    sudo -u clworker /opt/chosen-local/scripts/console_admin.sh create-user sam \
        --tailscale-login sam@example.com
    ... set-password sam | unlock sam | sign-out-everywhere sam | list

Passwords are typed at a prompt, twice, and never passed on the command line or logged.
Connects with CONSOLE_DATABASE_URL from console.env (the console's own login) as the
review_console actor.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable

import psycopg

from review import passwords
from review.config import get_settings
from worker.db import connect, transaction

ACTOR = "review_console"


def _ask_password(prompt: Callable[[str], str] = getpass.getpass) -> str:
    first = prompt("New console password (12+ characters): ")
    passwords.check_strength(first)
    if prompt("Again: ") != first:
        raise SystemExit("The two passwords are different. Nothing changed.")
    return first


def _founder_id(conn: psycopg.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT id FROM actors WHERE name = %s AND kind = 'founder' AND active", (name,)
    ).fetchone()
    conn.commit()
    if row is None:
        raise SystemExit(
            f"No active founder named {name!r}. Founders come from the business profile."
        )
    return str(row["id"])


def create_user(
    conn,
    name: str,
    *,
    tailscale_login: str | None,
    allow_local: bool,
    prompt: Callable[[str], str] = getpass.getpass,
) -> None:
    actor_id = _founder_id(conn, name)
    pw_hash = passwords.hash_password(_ask_password(prompt))
    with transaction(conn, actor=ACTOR):
        conn.execute(
            """INSERT INTO console_users
                   (actor_id, tailscale_login, password_hash, allow_local_signin)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (actor_id) DO UPDATE SET tailscale_login = EXCLUDED.tailscale_login,
                   password_hash = EXCLUDED.password_hash, password_changed_at = now(),
                   allow_local_signin = EXCLUDED.allow_local_signin, active = true,
                   failed_signins = 0, locked_until = NULL""",
            (actor_id, tailscale_login or None, pw_hash, allow_local),
        )
    print(f"Console login for {name} is ready.")


def set_password(conn, name: str, *, prompt: Callable[[str], str] = getpass.getpass) -> None:
    actor_id = _founder_id(conn, name)
    pw_hash = passwords.hash_password(_ask_password(prompt))
    with transaction(conn, actor=ACTOR):
        cur = conn.execute(
            """UPDATE console_users SET password_hash = %s, password_changed_at = now(),
                      failed_signins = 0, locked_until = NULL WHERE actor_id = %s""",
            (pw_hash, actor_id),
        )
        if cur.rowcount != 1:
            raise SystemExit(f"{name} has no console login yet; use create-user.")
        conn.execute(
            """UPDATE console_sessions SET revoked_at = now() WHERE revoked_at IS NULL
                  AND console_user_id = (SELECT id FROM console_users WHERE actor_id = %s)""",
            (actor_id,),
        )
    print(f"Password for {name} changed; their sessions were signed out.")


def unlock(conn, name: str) -> None:
    actor_id = _founder_id(conn, name)
    with transaction(conn, actor=ACTOR):
        conn.execute(
            "UPDATE console_users SET failed_signins = 0, locked_until = NULL WHERE actor_id = %s",
            (actor_id,),
        )
    print(f"{name} is unlocked.")


def sign_out_everywhere(conn, name: str) -> None:
    actor_id = _founder_id(conn, name)
    with transaction(conn, actor=ACTOR):
        cur = conn.execute(
            """UPDATE console_sessions SET revoked_at = now() WHERE revoked_at IS NULL
                  AND console_user_id = (SELECT id FROM console_users WHERE actor_id = %s)""",
            (actor_id,),
        )
    print(f"Signed out {cur.rowcount} session(s) for {name}.")


def list_users(conn) -> None:
    rows = conn.execute(
        """SELECT a.name, u.tailscale_login IS NOT NULL AS has_tailscale, u.allow_local_signin,
                  u.active, u.locked_until > now() AS locked
             FROM console_users u JOIN actors a ON a.id = u.actor_id ORDER BY a.name"""
    ).fetchall()
    conn.commit()
    for r in rows:
        flags = [k for k in ("has_tailscale", "allow_local_signin", "locked") if r[k]]
        print(f"{r['name']:<12} {'active' if r['active'] else 'inactive':<9} {' '.join(flags)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m review.admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="create or reset a founder's console login")
    c.add_argument("name")
    c.add_argument("--tailscale-login", help="the founder's Tailscale login (an email)")
    c.add_argument(
        "--allow-local",
        action="store_true",
        help="may sign in on the workstation itself (also needs CONSOLE_ALLOW_LOCAL_SIGNIN)",
    )
    for cmd in ("set-password", "unlock", "sign-out-everywhere"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    try:
        with connect(get_settings().console_database_url) as conn:
            if args.cmd == "create-user":
                if not args.tailscale_login and not args.allow_local:
                    raise SystemExit("Give --tailscale-login, --allow-local, or both.")
                create_user(
                    conn,
                    args.name,
                    tailscale_login=args.tailscale_login,
                    allow_local=args.allow_local,
                )
            elif args.cmd == "set-password":
                set_password(conn, args.name)
            elif args.cmd == "unlock":
                unlock(conn, args.name)
            elif args.cmd == "sign-out-everywhere":
                sign_out_everywhere(conn, args.name)
            else:
                list_users(conn)
    except passwords.WeakPassword as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
