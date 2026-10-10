"""Slack alerts through an incoming webhook. IDs, a category and a console link only.

Every value that goes into a message is checked against an identifier pattern, so a
name, phone number or sentence cannot reach Slack even by mistake. Success is HTTP
200 (docs.slack.dev, incoming webhooks; checked 2026-10-10).
"""

from __future__ import annotations

import re
from collections.abc import Callable

import httpx

from worker.logs import get_logger

log = get_logger(__name__)

_IDENT = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

AlertSink = Callable[[str], None]


def _check(value: str, what: str) -> str:
    if not _IDENT.match(value):
        raise ValueError(f"{what} is not identifier-shaped and cannot go to Slack")
    return value


def format_job_failed(job_id: str, job_type: str, error_class: str, console_base_url: str) -> str:
    _check(job_id, "job_id")
    _check(job_type, "job_type")
    _check(error_class, "error_class")
    text = f":rotating_light: job failed | job {job_id} | type {job_type} | {error_class}"
    if console_base_url:
        text += f" | {console_base_url.rstrip('/')}/jobs/{job_id}"
    return text


def format_category(category: str, ids: dict[str, str]) -> str:
    _check(category, "category")
    parts = [f"{_check(k, 'key')} {_check(v, 'id')}" for k, v in ids.items()]
    return " | ".join([f":warning: {category}", *parts])


class SlackWebhook:
    def __init__(self, url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._url = url
        self._http = httpx.Client(timeout=10.0, transport=transport)

    def __call__(self, text: str) -> None:
        if not self._url:
            log.warning("slack alert not sent: no webhook configured")
            return
        try:
            resp = self._http.post(self._url, json={"text": text})
        except httpx.HTTPError:
            log.error("slack alert failed", extra={"error_class": "TransportError"})
            return
        if resp.status_code != 200:
            log.error("slack alert rejected", extra={"http_status": resp.status_code})
