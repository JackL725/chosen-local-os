"""Scaffold a new system: python scripts/new_system.py <key> [options]

    uv run python scripts/new_system.py lead_notes --model --data-class prospect
    uv run python scripts/new_system.py partner_portal --console --tables

Creates, under the repo root (or --root):

    systems/<key>/__init__.py, manifest.py, jobs.py, service.py
    systems/<key>/console.py and templates/<key>/index.html     with --console
    systems/<key>/evals.py, prompts/<key>/example.v1.md,
    evals/synthetic/<key>/cases.jsonl                           with --model
    db/migrations/<timestamp>_<key>_init.sql                    with --tables
    tests/systems/<key>/test_<key>.py

The new system arrives turned off; a founder turns it on in the console. It never
touches worker/ or review/. Replace the example job, prompt and cases with real ones.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
DATA_CLASSES = ("none", "public", "founder_brief", "coach", "prospect")


def _files(key: str, title: str, data_class: str, model: bool, console: bool) -> dict[str, str]:
    cls = "".join(part.capitalize() for part in key.split("_"))
    imports = ["JobSpec", "Manifest"]
    if console:
        imports.append("ConsoleSection")
    if model:
        imports += ["ModelUse", "PromptSpec"]
    model_bits = (
        """
    models=(ModelUse(name="example", profile="local"),),
    prompts=(PromptSpec(name="example", version=1),),
    evals="systems.{key}.evals:score","""
        if model
        else ""
    )
    console_bits = (
        f"""
    console=ConsoleSection(title="{title}", routes="systems.{key}.console:routes"),"""
        if console
        else ""
    )
    files: dict[str, str] = {}
    files[f"systems/{key}/__init__.py"] = f'"""{key}: {title}."""\n'
    files[f"systems/{key}/manifest.py"] = f'''"""{key}: {title}.

Data only: import nothing but systems._contract. See docs/systems.md.
"""

from __future__ import annotations

from systems._contract import {", ".join(sorted(imports))}

MANIFEST = Manifest(
    key="{key}",
    title="{title}",
    description="TODO: one sentence on what this system does.",
    version=1,
    data_class="{data_class}",
    jobs=(
        JobSpec(
            job_type="{key}.example",
            handler="systems.{key}.jobs:example",
            description="TODO: replace this example job.",{
        chr(10) + '            model_use="example",' if model else ""
    }
        ),
    ),{model_bits.format(key=key)}{console_bits}
)
'''
    if model:
        files[
            f"systems/{key}/jobs.py"
        ] = f'''"""{key} job handlers: pure functions of (job, context)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from systems.{key}.manifest import MANIFEST
from worker import prompts
from worker.errors import ModelOutputInvalid
from worker.jobs import JobContext, JobResult
from worker.queue import Job


class {cls}ExampleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    echo: str


def example(job: Job, ctx: JobContext) -> JobResult:
    text = str(job.payload.get("text", ""))
    prompt = prompts.load(MANIFEST.key, "example", MANIFEST.prompt_version("example"))
    result = ctx.llm.complete(
        [{{"role": "system", "content": prompt.text}}, {{"role": "user", "content": text}}],
        {cls}ExampleOutput,
        prompt_version=prompt.version,
    )
    if not result.value.ok or result.value.echo != text:
        raise ModelOutputInvalid()
    return JobResult(
        result_ref={{"ok": True}},
        prompt_version=result.prompt_version,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )
'''
        files[
            f"systems/{key}/evals.py"
        ] = f'''"""Eval scorer for {key}: runs the real handler on each case."""

from __future__ import annotations

import time
from typing import Any

from evals.outcome import Outcome
from systems.{key} import jobs
from worker.errors import error_class
from worker.jobs import JobContext
from worker.queue import Job


def score(case: dict[str, Any], expected: dict[str, Any], ctx: JobContext) -> Outcome:
    job = Job(
        id=case["id"],
        job_type="{key}.example",
        payload=case["input"],
        attempts=1,
        max_attempts=1,
        entity_type=None,
        entity_id=None,
        claimed_at=None,
    )
    started = time.monotonic()
    try:
        jobs.example(job, ctx)
    except Exception as exc:  # noqa: BLE001 - every case is scored
        elapsed = int((time.monotonic() - started) * 1000)
        valid = error_class(exc) != "ModelOutputInvalid"
        return Outcome(case["id"], False, valid, elapsed, error_class(exc))
    elapsed = int((time.monotonic() - started) * 1000)
    return Outcome(case["id"], expected.get("ok") is True, True, elapsed)
'''
        files[f"prompts/{key}/example.v1.md"] = f"""---
purpose: TODO. Example prompt created by scripts/new_system.py; replace it.
inputs: text (TODO: say where it comes from and whether it can contain prospect data)
output_schema: systems.{key}.jobs.{cls}ExampleOutput
rules: TODO: list the CLAUDE.md hard rules that apply.
---
Reply with JSON only, with two fields:
- "ok": true
- "echo": the exact text given by the user.
"""
        files[f"evals/synthetic/{key}/cases.jsonl"] = (
            f'{{"id": "{key}-01", "input": {{"text": "example1"}}, "expected": {{"ok": true}}}}\n'
            f'{{"id": "{key}-02", "input": {{"text": "example2"}}, "expected": {{"ok": true}}}}\n'
        )
    else:
        files[
            f"systems/{key}/jobs.py"
        ] = f'''"""{key} job handlers: pure functions of (job, context)."""

from __future__ import annotations

from worker.jobs import JobContext, JobResult
from worker.queue import Job


def example(job: Job, ctx: JobContext) -> JobResult:
    return JobResult(result_ref={{"ok": True}})
'''
    files[
        f"systems/{key}/service.py"
    ] = f'''"""{key} Ledger writes. Use the caller's connection; never open one.

Every write runs inside the caller's transaction, which has already set app.actor,
so the event trigger records who did it.
"""

from __future__ import annotations
'''
    if console:
        files[f"systems/{key}/console.py"] = f'''"""{key} screens in the review console."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from review import web


async def index(request: Request) -> Response:
    return web.render(request, "{key}/index.html", {{"title": "{title}"}})


routes = [Route("/", index, name="{key}:index")]
'''
        files[f"systems/{key}/templates/{key}/index.html"] = """{% extends "base.html" %}
{% block content %}
<h1>{{ title }}</h1>
<p>Nothing here yet.</p>
{% endblock %}
"""
    test_body = (
        f'''from tests.fakes.fake_llm import FakeLLM


def test_example_job_runs(settings_factory):
    from systems.{key} import jobs
    from worker.jobs import JobContext
    from worker.queue import Job

    settings = settings_factory()
    ctx = JobContext(settings=settings, llm=FakeLLM(), private_data_dir=settings.private_data_dir)
    job = Job(
        id="t1",
        job_type="{key}.example",
        payload={{"text": "abc123"}},
        attempts=1,
        max_attempts=3,
        entity_type=None,
        entity_id=None,
        claimed_at=None,
    )
    assert jobs.example(job, ctx).result_ref == {{"ok": True}}
'''
        if model
        else f'''
def test_example_job_runs(settings_factory):
    from systems.{key} import jobs
    from tests.fakes.fake_llm import FakeLLM
    from worker.jobs import JobContext
    from worker.queue import Job

    settings = settings_factory()
    ctx = JobContext(settings=settings, llm=FakeLLM(), private_data_dir=settings.private_data_dir)
    job = Job(
        id="t1",
        job_type="{key}.example",
        payload={{}},
        attempts=1,
        max_attempts=3,
        entity_type=None,
        entity_id=None,
        claimed_at=None,
    )
    assert jobs.example(job, ctx).result_ref == {{"ok": True}}
'''
    )
    files[f"tests/systems/{key}/__init__.py"] = ""
    files[f"tests/systems/{key}/test_{key}.py"] = f'"""Tests for {key}."""\n\n' + test_body
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/new_system.py")
    parser.add_argument("key", help="lowercase, digits and underscores, e.g. lead_notes")
    parser.add_argument("--title", default=None)
    parser.add_argument("--data-class", choices=DATA_CLASSES, default="none")
    parser.add_argument("--model", action="store_true", help="the system calls a model")
    parser.add_argument("--console", action="store_true", help="the system has screens")
    parser.add_argument("--tables", action="store_true", help="add a migration stub")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    key = args.key
    if not KEY.match(key) or key.startswith("_"):
        print(f"key must match {KEY.pattern}", file=sys.stderr)
        return 2
    root: Path = args.root
    if (root / "systems" / key).exists():
        print(f"systems/{key} already exists", file=sys.stderr)
        return 2
    title = args.title or key.replace("_", " ").capitalize()
    files = _files(key, title, args.data_class, args.model, args.console)
    if args.tables:
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        files[f"db/migrations/{stamp}_{key}_init.sql"] = f"""-- migrate:up
-- {key}: its tables. Every table gets created_at, updated_at, an events trigger
-- (see the DO block at the end of the first migration) and explicit grants.

-- migrate:down
"""
    tests_pkg = root / "tests" / "systems" / "__init__.py"
    if not tests_pkg.exists():
        files["tests/systems/__init__.py"] = ""
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"created {rel}")
    print(f"\nNext: fill in systems/{key}/manifest.py, then run")
    print("  uv run python scripts/check_systems.py && uv run pytest -q")
    print("The system arrives turned off; turn it on in the console under Systems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
