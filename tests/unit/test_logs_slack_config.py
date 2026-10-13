from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from worker import errors, logs, prompts
from worker.config import Settings
from worker.slack import SlackWebhook, format_category, format_job_failed

JOB = "3f1c2b7a-9d4e-4c1a-8b2f-1e2d3c4b5a69"


def _capture() -> io.StringIO:
    stream = io.StringIO()
    logs.configure(level=logging.DEBUG, stream=stream)
    return stream


def test_log_filter_drops_unknown_fields_and_exception_text(sentinels):
    stream = _capture()
    log = logging.getLogger("t")
    name = sentinels["names"][0]
    try:
        raise ValueError(sentinels["transcript_sentences"][0])
    except ValueError:
        log.exception("handler blew up", extra={"job_id": JOB, "first_name": name})
    log.info("job claimed", extra={"job_type": name})  # an allowed key with a non-ID value
    out = stream.getvalue()
    lines = [json.loads(line) for line in out.splitlines()]
    assert lines[0]["job_id"] == JOB
    assert lines[0]["error_class"] == "ValueError"
    assert "first_name" not in lines[0]
    assert lines[1]["job_type"] == "[redacted]"
    for s in (*sentinels["names"], *sentinels["transcript_sentences"]):
        assert s not in out


def test_log_message_scrubs_emails_and_phones_but_keeps_uuids(sentinels):
    stream = _capture()
    phone, email = sentinels["phones"][1], sentinels["emails"][0]
    logging.getLogger("t").warning(f"oops {phone} {email} job {JOB}")
    out = stream.getvalue()
    assert phone not in out and email not in out
    assert JOB in out


def test_library_loggers_are_scrubbed_too(sentinels):
    stream = _capture()
    logging.getLogger("some.library").warning("sent to %s", sentinels["emails"][0])
    assert sentinels["emails"][0] not in stream.getvalue()


def test_safe_message_never_uses_exception_text(sentinels):
    secret = sentinels["transcript_sentences"][1]
    for exc in (ValueError(secret), errors.ModelOutputInvalid(secret), ConnectionError(secret)):
        assert secret not in errors.safe_message(exc)
    assert errors.is_retryable(ConnectionError("x"))
    assert errors.is_retryable(errors.LocalModelUnavailable())
    assert not errors.is_retryable(errors.ModelOutputInvalid())
    assert not errors.is_retryable(KeyError("x"))


def test_slack_messages_carry_ids_only(sentinels):
    text = format_job_failed(JOB, "healthcheck", "ModelOutputInvalid", "https://console.ts.net")
    assert JOB in text and "https://console.ts.net/jobs/" in text
    for bad in (*sentinels["names"], *sentinels["phones"], *sentinels["transcript_sentences"]):
        with pytest.raises(ValueError):
            format_job_failed(JOB, bad, "X", "")
        with pytest.raises(ValueError):
            format_category("stale_queue", {"job": bad})


def test_slack_webhook_posts_text():
    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    SlackWebhook("https://hooks.example/abc", transport=httpx.MockTransport(handler))("hello")
    assert posted == [{"text": "hello"}]


def test_settings_reject_non_local_model_and_need_private_dir(monkeypatch):
    monkeypatch.delenv("PRIVATE_DATA_DIR", raising=False)
    with pytest.raises(ValidationError):
        Settings(database_url="postgres://x@127.0.0.1/y")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgres://x@127.0.0.1/y",
            private_data_dir=Path("evals/synthetic"),
            local_llm_base_url="https://api.openai.com/v1",
        )
    s = Settings(database_url="postgres://x@127.0.0.1/y", private_data_dir=Path("evals/synthetic"))
    assert s.local_llm_thinking is False


def test_every_prompt_file_has_a_header_and_manifest_versions_exist():
    root = prompts.PROMPTS_DIR
    files = sorted(root.glob("*/*.v*.md"))
    assert files
    for path in files:
        system = path.parent.name
        name, _, version = path.name.removesuffix(".md").rpartition(".v")
        prompts.load(system, name, int(version))
    import systems

    for key, manifest in systems.all_manifests().items():
        for spec in manifest.prompts:
            loaded = prompts.load(key, spec.name, spec.version)
            assert loaded.version == f"{key}/{spec.name}.v{spec.version}"
