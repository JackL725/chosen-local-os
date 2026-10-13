"""A review console against the test Ledger, with two founder logins:

* sam: Tailscale login sam@tailnet.test (remote sign-in only)
* jack: Tailscale login jack@tailnet.test, and allowed to sign in on the workstation
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

PASSWORD = "correct horse battery"
SAM = "sam@tailnet.test"
JACK = "jack@tailnet.test"


@pytest.fixture(autouse=True)
def _cheap_hashes(monkeypatch):
    from review import passwords

    monkeypatch.setattr(passwords, "DEFAULT_LOG2_N", 10)
    monkeypatch.setattr(passwords, "_DUMMY", None)


@pytest.fixture
def console_settings(db_url: str, tmp_path: Path):
    from review.config import ConsoleSettings

    private = tmp_path / "private"
    private.mkdir()
    return ConsoleSettings(
        console_database_url=db_url,
        private_data_dir=private,
        console_allow_local_signin=True,
    )


@pytest.fixture
def alerts() -> list[str]:
    return []


@pytest.fixture
def founders(db_url: str) -> None:
    from review import admin
    from worker.db import connect

    with connect(db_url) as conn:
        admin.create_user(
            conn, "sam", tailscale_login=SAM, allow_local=False, prompt=lambda _p: PASSWORD
        )
        admin.create_user(
            conn, "jack", tailscale_login=JACK, allow_local=True, prompt=lambda _p: PASSWORD
        )


@pytest.fixture
def client(console_settings, alerts, founders) -> Iterator:
    from starlette.testclient import TestClient

    from review.app import build_app

    app = build_app(console_settings, alert=alerts.append)
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def csrf_from(html: str) -> str:
    import re

    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no csrf field on the page"
    return m.group(1)


def sign_in(client, *, login: str | None = SAM, name: str | None = None, password: str = PASSWORD):
    headers = {"Tailscale-User-Login": login} if login else {}
    page = client.get("/signin", headers=headers)
    data = {"csrf": csrf_from(page.text), "password": password, "next": "/"}
    if name:
        data["name"] = name
    return client.post("/signin", data=data, headers=headers)
