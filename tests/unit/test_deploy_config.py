"""The console's founder-capable login stays out of the worker's reach (review finding 1).

The review console runs as its own Linux user, clconsole. Its database login (cl_console)
lives in a root-only file that systemd reads before dropping privileges. Nothing that runs
as clworker loads it, so neither the file nor the console's process environment is
readable by the worker. These checks keep the deployment files that way.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNITS = ROOT / "deploy" / "systemd"
CONSOLE_ENV = "/etc/chosen-local/console.env"


def _unit(name: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for line in (UNITS / name).read_text().splitlines():
        m = re.match(r"^([A-Za-z]+)=(.*)$", line.strip())
        if m:
            values.setdefault(m.group(1), []).append(m.group(2))
    return values


def test_the_console_runs_as_its_own_user_with_its_own_login_file():
    u = _unit("cl-review.service")
    assert u["User"] == ["clconsole"] and u["Group"] == ["clconsole"]
    assert u["SupplementaryGroups"] == ["clprivate"]
    assert CONSOLE_ENV in u["EnvironmentFile"]


def test_no_clworker_unit_loads_the_console_login():
    for path in sorted(UNITS.glob("*.service")):
        u = _unit(path.name)
        if u.get("User") == ["clworker"]:
            assert CONSOLE_ENV not in u.get("EnvironmentFile", []), path.name
            assert "console.env" not in path.read_text(), path.name


def test_only_the_console_scripts_mention_the_console_login():
    allowed = {"deploy_local.sh", "console_admin.sh", "check_env_examples.py"}
    for path in sorted((ROOT / "scripts").iterdir()):
        if path.is_file() and path.name not in allowed:
            assert "console.env" not in path.read_text(), path.name
    worker_env = (ROOT / "deploy" / "worker.env.example").read_text()
    assert not re.search(r"^CONSOLE_DATABASE_URL=", worker_env, re.M)


def test_the_login_file_is_root_only_and_the_admin_tool_runs_as_clconsole():
    deploy = (ROOT / "scripts" / "deploy_local.sh").read_text()
    assert f"CONSOLE_ENV={CONSOLE_ENV}" in deploy
    assert (
        'install -o root -g root -m 600 "$DEST/deploy/console.env.example" "$CONSOLE_ENV"' in deploy
    )
    admin = (ROOT / "scripts" / "console_admin.sh").read_text()
    assert "--uid=clconsole" in admin and f"EnvironmentFile={CONSOLE_ENV}" in admin
    assert 'if [ "$(id -u)" -ne 0 ]' in admin  # the URL is read by systemd as root


def test_files_in_the_shared_folder_stay_group_readable():
    for name in ("cl-worker.service", "cl-restic.service", "cl-review.service"):
        u = _unit(name)
        assert u.get("UMask") == ["0007"], name
        assert u.get("SupplementaryGroups") == ["clprivate"], name
