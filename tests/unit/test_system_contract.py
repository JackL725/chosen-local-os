"""The system contract (systems/_contract.py, docs/systems.md).

* Every system in the repo meets the contract.
* The checker catches each kind of violation it claims to (planted in a scratch tree).
* A system made with scripts/new_system.py runs its job and its evals with no edit to
  worker/ or review/ (run in a copy of the repo, so this checkout is never touched).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import systems
from systems import _check
from systems._contract import (
    ConsoleSection,
    JobSpec,
    Manifest,
    ModelUse,
    PromptSpec,
    SettingSpec,
)

ROOT = Path(__file__).resolve().parents[2]


def test_every_system_meets_the_contract():
    assert _check.problems() == []


def test_registry_finds_healthcheck_and_resolves_its_handler():
    from worker.jobs import handled_job_types, resolve

    assert "healthcheck" in systems.all_manifests()
    assert systems.job_types()["healthcheck"] == "healthcheck"
    assert callable(resolve("healthcheck", enable_test_jobs=False))
    assert "test_sleep" in handled_job_types(enable_test_jobs=True)
    assert "test_sleep" not in handled_job_types(enable_test_jobs=False)


def _manifest(**overrides) -> Manifest:
    values = dict(
        key="demo",
        title="Demo",
        description="A demo.",
        version=1,
        data_class="none",
    )
    values.update(overrides)
    return Manifest(**values)


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (
            _manifest(jobs=(JobSpec("other.thing", "systems.demo.jobs:x"),)),
            "must be 'demo' or start with 'demo.'",
        ),
        (
            _manifest(jobs=(JobSpec("demo.x", "systems.healthcheck.jobs:handle"),)),
            "must be 'systems.demo.<module>:<function>'",
        ),
        (
            _manifest(jobs=(JobSpec("demo.x", "systems.demo.jobs:x", model_use="nope"),)),
            "undeclared model use 'nope'",
        ),
        (_manifest(models=(ModelUse("m", profile="mystery"),)), "unknown profile 'mystery'"),
        (_manifest(prompts=(PromptSpec("missing", 1),)), "prompt missing.v1"),
        (_manifest(models=(ModelUse("m"),)), "must name an eval scorer"),
        (
            _manifest(settings=(SettingSpec("other.limit", "int", 3, "Limit"),)),
            "must be unique and start with 'demo.'",
        ),
        (
            _manifest(settings=(SettingSpec("demo.limit", "int", "three", "Limit"),)),
            "default does not match its type",
        ),
        (
            _manifest(settings=(SettingSpec("demo.mode", "choice", "c", "Mode", choices=("a",)),)),
            "default does not match its type",
        ),
        (
            _manifest(console=ConsoleSection("Demo", "systems.healthcheck.console:routes")),
            "must be 'systems.demo.console:routes'",
        ),
        (_manifest(n8n_workflows=("intake",)), "must be named 'demo--<name>'"),
        (_manifest(n8n_workflows=("demo--intake",)), "demo--intake.json is missing"),
    ],
)
def test_checker_catches_manifest_violations(tmp_path: Path, manifest: Manifest, expected: str):
    found = "\n".join(_check._check_manifest(manifest, tmp_path))
    assert expected in found, found


def test_checker_keeps_coach_and_prospect_systems_off_cloud_models(monkeypatch):
    from worker import config

    monkeypatch.setitem(config.MODEL_PROFILES, "cloud", "cloud")
    for data_class in ("coach", "prospect"):
        m = _manifest(data_class=data_class, models=(ModelUse("m", profile="cloud"),))
        assert "must be local" in "\n".join(_check._check_manifest(m, ROOT))
    ok = _manifest(data_class="public", models=(ModelUse("m", profile="cloud"),))
    assert "must be local" not in "\n".join(_check._check_manifest(ok, ROOT))


def test_checker_reads_the_cloud_allowlist_without_importing_it(tmp_path: Path):
    from worker import cloud_llm

    assert _check.cloud_allowlist() == cloud_llm.ALLOWED_CALLERS
    (tmp_path / "worker").mkdir()
    (tmp_path / "worker" / "cloud_llm.py").write_text(
        'ALLOWED_CALLERS: frozenset[str] = frozenset({"systems.demo.jobs"})\n'
    )
    assert _check.cloud_allowlist(tmp_path) == {"systems.demo.jobs"}


def test_checker_catches_code_violations(tmp_path: Path):
    folder = tmp_path / "systems" / "demo"
    folder.mkdir(parents=True)
    (folder / "manifest.py").write_text("import os\nfrom worker.config import X\n")
    (folder / "service.py").write_text(
        textwrap.dedent(
            """
            import logging
            import psycopg
            from worker.db import connect

            def bad():
                psycopg.connect("x")
                logging.basicConfig()
                logging.getLogger().addHandler(logging.StreamHandler())
            """
        )
    )
    (folder / "fine.py").write_text(
        "import psycopg\nfrom psycopg import Connection, sql\nfrom worker.db import transaction\n"
    )
    (folder / "sneaky.py").write_text(
        textwrap.dedent(
            """
            import psycopg as pg
            import psycopg_pool
            from psycopg import connect

            def bad():
                pg.connect("x")
                pg.Connection.connect("x")
            """
        )
    )
    found = "\n".join(_check._check_code("demo", folder))
    assert "manifest.py is data only" in found
    assert "manifest.py may import only from systems._contract" in found
    assert found.count("service.py opens its own database connection") == 2
    assert found.count("sneaky.py opens its own database connection") == 4
    (folder / "sneakier.py").write_text("from psycopg import Connection\nConnection.connect('x')\n")
    found = "\n".join(_check._check_code("demo", folder))
    assert "sneakier.py opens its own database connection" in found
    assert found.count("configures logging") >= 2
    assert "fine.py" not in found


def _tree_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            h.update(str(path.relative_to(folder)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def _copy_repo(dest: Path) -> None:
    ignore = shutil.ignore_patterns(".git", ".venv", "__pycache__", ".ruff_cache", ".pytest_cache")
    for name in (
        "worker",
        "review",
        "systems",
        "evals",
        "prompts",
        "scripts",
        "tests",
        "n8n",
        "db",
    ):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, dest / name, ignore=ignore)
    shutil.copy2(ROOT / "pyproject.toml", dest / "pyproject.toml")


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "PSYCOPG_IMPL")}
    env["PYTHONPATH"] = os.pathsep.join([str(cwd), *sys.path[1:]])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120
    )


def test_scaffolded_system_runs_its_job_and_evals_without_touching_the_core(tmp_path: Path):
    _copy_repo(tmp_path)
    core_before = {d: _tree_hash(tmp_path / d) for d in ("worker", "review")}

    made = _run(tmp_path, "scripts/new_system.py", "demo_notes", "--model", "--tables", "--console")
    assert made.returncode == 0, made.stderr

    check = _run(tmp_path, "scripts/check_systems.py")
    assert check.returncode == 0, check.stdout + check.stderr

    probe = textwrap.dedent(
        """
        import io
        from pathlib import Path
        from evals import run as evals_run
        from tests.fakes.fake_llm import FakeLLM
        from worker.config import Settings
        from worker.jobs import JobContext, resolve
        from worker.queue import Job

        settings = Settings(database_url="postgres://x@127.0.0.1/none_test",
                            private_data_dir=Path("evals/synthetic"))
        ctx = JobContext(settings=settings, llm=FakeLLM(), private_data_dir=Path("evals/synthetic"))
        handler = resolve("demo_notes.example", enable_test_jobs=False)
        job = Job(id="j1", job_type="demo_notes.example", payload={"text": "hello42"},
                  attempts=1, max_attempts=3, entity_type=None, entity_id=None, claimed_at=None)
        assert handler(job, ctx).result_ref == {"ok": True}
        result = evals_run.run("demo_notes", Path("evals/synthetic"), FakeLLM(), settings,
                               aggregate_only=True, out=io.StringIO())
        assert result["examples"] == 2 and result["accuracy"] == 1.0, result
        from review.app import _routes
        routes, nav = _routes()
        assert any(getattr(r, "path", "") == "/s/demo_notes" for r in routes)
        assert [n["system"] for n in nav] == ["demo_notes"] or "demo_notes" in [n["system"] for n in nav]
        print("probe ok")
        """
    )
    ran = _run(tmp_path, "-c", probe)
    assert ran.returncode == 0 and "probe ok" in ran.stdout, ran.stdout + ran.stderr

    tests = _run(
        tmp_path, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/systems/demo_notes"
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr

    assert {d: _tree_hash(tmp_path / d) for d in ("worker", "review")} == core_before
    assert list((tmp_path / "db" / "migrations").glob("*_demo_notes_init.sql"))
