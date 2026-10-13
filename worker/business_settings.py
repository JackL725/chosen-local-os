"""Business settings: values founders change in the console.

Definitions (type, default, bounds) live in code, the core's here and each system's in
its manifest. Values live in the Ledger's `settings` table; a missing row means the
default. Only founders write settings (the database refuses anyone else).
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

import systems
from systems._contract import SettingSpec

CORE_SETTINGS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "core.business_name",
        "text",
        "",
        "Business name",
        "Shown in the console. Set by the bootstrap from the business profile.",
    ),
    SettingSpec(
        "core.stale_job_alert_hours",
        "number",
        4,
        "Stale-job alert (hours)",
        "Slack is alerted when the oldest waiting job is older than this (decisions item 24).",
        minimum=0.25,
        maximum=72,
    ),
    SettingSpec(
        "core.stale_job_alert_repeat_minutes",
        "int",
        240,
        "Repeat the stale-job alert every (minutes)",
        minimum=15,
        maximum=1440,
    ),
    SettingSpec(
        "console.session_idle_hours",
        "int",
        12,
        "Sign out after idle (hours)",
        minimum=1,
        maximum=72,
    ),
    SettingSpec(
        "console.max_failed_signins",
        "int",
        5,
        "Wrong passwords before an account locks",
        minimum=3,
        maximum=20,
    ),
    SettingSpec(
        "console.lockout_minutes",
        "int",
        15,
        "Lockout after too many wrong passwords (minutes)",
        minimum=1,
        maximum=1440,
    ),
)


class InvalidSetting(ValueError):
    pass


def all_specs() -> dict[str, SettingSpec]:
    specs = {s.key: s for s in CORE_SETTINGS}
    for manifest in systems.all_manifests().values():
        for s in manifest.settings:
            specs[s.key] = s
    return specs


def spec(key: str) -> SettingSpec:
    try:
        return all_specs()[key]
    except KeyError:
        raise InvalidSetting(f"unknown setting {key}") from None


def parse(s: SettingSpec, raw: Any) -> Any:
    """Turn a form value (or a JSON value) into a valid value for the setting."""
    try:
        if s.type == "bool":
            value: Any = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "on")
        elif s.type == "int":
            value = int(str(raw).strip())
        elif s.type == "number":
            value = float(str(raw).strip())
            if value.is_integer():
                value = int(value)
        elif s.type == "choice":
            value = str(raw)
            if value not in s.choices:
                raise InvalidSetting(f"{s.label}: choose one of {', '.join(s.choices)}")
        else:
            value = str(raw).strip()
            if len(value) > 500:
                raise InvalidSetting(f"{s.label}: 500 characters at most")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, InvalidSetting):
            raise
        raise InvalidSetting(f"{s.label}: not a valid {s.type}") from None
    if s.type in ("int", "number"):
        if s.minimum is not None and value < s.minimum:
            raise InvalidSetting(f"{s.label}: at least {s.minimum:g}")
        if s.maximum is not None and value > s.maximum:
            raise InvalidSetting(f"{s.label}: at most {s.maximum:g}")
    return value


def get(conn: psycopg.Connection, key: str) -> Any:
    s = spec(key)
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cur.fetchone()
    return s.default if row is None else row["value"]


def current(conn: psycopg.Connection) -> dict[str, Any]:
    """Every defined setting with its current value."""
    with conn.cursor() as cur:
        cur.execute("SELECT key, value FROM settings")
        stored = {r["key"]: r["value"] for r in cur.fetchall()}
    return {k: stored.get(k, s.default) for k, s in all_specs().items()}


def put(conn: psycopg.Connection, key: str, raw: Any) -> Any:
    """Validate and store. Runs inside the caller's transaction (a founder's)."""
    value = parse(spec(key), raw)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO settings (key, value) VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                WHERE settings.value IS DISTINCT FROM EXCLUDED.value""",
            (key, Jsonb(value)),
        )
    return value
