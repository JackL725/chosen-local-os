"""The cloud boundary (CLAUDE.md hard rule 3, .claude/rules/worker.md).

Walks every Python module in the repo's code folders, follows imports transitively,
and fails if:
  1. a module outside cloud_llm.ALLOWED_CALLERS reaches worker.cloud_llm, directly or
     through another module;
  2. any module other than worker.cloud_llm imports a cloud model SDK;
  3. any module other than worker.cloud_llm mentions a cloud model API host;
  4. any module other than the approved few imports an HTTP client (httpx, requests,
     urllib.request, http.client, aiohttp), which is how a non-local model endpoint
     would otherwise be called.
The checker is also run against a fake tree with planted violations, so a broken
checker fails too.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from worker import cloud_llm

ROOT = Path(__file__).resolve().parents[2]
CODE_DIRS = ("worker", "review", "evals", "scripts", "systems")
CLOUD_MODULE = "worker.cloud_llm"
CLOUD_SDKS = (
    "anthropic",
    "openai",
    "google.generativeai",
    "google.genai",
    "vertexai",
    "cohere",
    "mistralai",
    "groq",
    "together",
    "litellm",
    "boto3",
    "langchain",
)
CLOUD_HOSTS = (
    "api.anthropic.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api.mistral.ai",
    "api.groq.com",
    "openrouter.ai",
    "api.together.xyz",
    "api.cohere.ai",
    "api.cohere.com",
    "bedrock-runtime",
)
HTTP_CLIENTS = ("httpx", "requests", "urllib.request", "http.client", "aiohttp", "urllib3")
# local_llm: the local model server (base URL checked to be 127.0.0.1).
# slack: incoming webhook alerts, IDs only. cloud_llm: the boundary itself.
HTTP_ALLOWED = frozenset({"worker.local_llm", "worker.slack", CLOUD_MODULE})


@dataclass
class Module:
    name: str
    imports: set[str] = field(default_factory=set)
    strings: list[str] = field(default_factory=list)


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def scan(root: Path, dirs: tuple[str, ...]) -> dict[str, Module]:
    modules: dict[str, Module] = {}
    for d in dirs:
        base = root / d
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            name = _module_name(root, path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            mod = Module(name)
            package = name if path.name == "__init__.py" else name.rpartition(".")[0]
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mod.imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        base_pkg = package.split(".")
                        base_pkg = base_pkg[: len(base_pkg) - (node.level - 1)]
                        target = ".".join([*base_pkg, node.module] if node.module else base_pkg)
                    else:
                        target = node.module or ""
                    mod.imports.add(target)
                    mod.imports.update(f"{target}.{alias.name}" for alias in node.names)
                elif isinstance(node, ast.Call):
                    # importlib.import_module("x") / __import__("x") with a literal name
                    fn = node.func
                    fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    if (
                        fname in ("import_module", "__import__")
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    ):
                        mod.imports.add(node.args[0].value)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    mod.strings.append(node.value)
            modules[name] = mod
    return modules


def _matches(name: str, prefixes: tuple[str, ...] | frozenset[str]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def reaches(modules: dict[str, Module], start: str, target: str) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for imp in modules.get(current, Module(current)).imports:
            if imp == target or imp.startswith(target + "."):
                return True
            # resolve "pkg.mod.attr" to the longest known module prefix
            candidate = imp
            while candidate and candidate not in modules:
                candidate = candidate.rpartition(".")[0]
            if candidate:
                stack.append(candidate)
    return False


def violations(modules: dict[str, Module], allowed_callers: frozenset[str]) -> list[str]:
    found: list[str] = []
    for name, mod in sorted(modules.items()):
        if name == CLOUD_MODULE:
            continue
        if name not in allowed_callers and reaches(modules, name, CLOUD_MODULE):
            found.append(f"{name} reaches {CLOUD_MODULE} but is not on its allowlist")
        for imp in mod.imports:
            if _matches(imp, CLOUD_SDKS):
                found.append(f"{name} imports cloud SDK {imp}")
            if _matches(imp, HTTP_CLIENTS) and name not in HTTP_ALLOWED:
                found.append(f"{name} imports HTTP client {imp}")
        for s in mod.strings:
            if any(host in s for host in CLOUD_HOSTS):
                found.append(f"{name} mentions a cloud model host")
    return sorted(set(found))


def test_repository_respects_the_cloud_boundary():
    modules = scan(ROOT, CODE_DIRS)
    assert "worker.local_llm" in modules and CLOUD_MODULE in modules
    assert violations(modules, cloud_llm.ALLOWED_CALLERS) == []


def test_job_registry_does_not_import_handlers_eagerly():
    modules = scan(ROOT, CODE_DIRS)
    assert not any(i.startswith("worker.jobs.") for i in modules["worker.jobs"].imports)
    # The system registry reads manifests only; handlers are imported by name.
    for name in ("systems", "systems._contract"):
        eager = [
            i
            for i in modules[name].imports
            if i.startswith("systems.")
            and i != "systems._contract"
            and not i.startswith("systems._contract.")
        ]
        assert eager == [], (name, eager)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_checker_catches_planted_violations(tmp_path: Path):
    _write(tmp_path, "worker/__init__.py", "")
    _write(tmp_path, "worker/cloud_llm.py", "import anthropic\n")
    _write(tmp_path, "worker/helper.py", "from worker import cloud_llm\n")
    _write(tmp_path, "worker/jobs/__init__.py", "")
    _write(tmp_path, "worker/jobs/calls.py", "from worker.helper import x\n")  # transitive
    _write(tmp_path, "worker/jobs/rel.py", "from .. import cloud_llm\n")  # relative
    _write(
        tmp_path,
        "worker/jobs/lazy.py",
        "import importlib\nimportlib.import_module('worker.cloud_llm')\n",
    )
    _write(tmp_path, "worker/sneaky.py", "import openai\n")
    _write(
        tmp_path,
        "worker/direct_http.py",
        "import httpx\nURL = 'https://api.openai.com/v1/chat/completions'\n",
    )
    _write(tmp_path, "worker/fine.py", "import json\n")
    modules = scan(tmp_path, ("worker",))
    found = "\n".join(violations(modules, frozenset()))
    assert "worker.helper reaches worker.cloud_llm" in found
    assert "worker.jobs.calls reaches worker.cloud_llm" in found
    assert "worker.jobs.rel reaches worker.cloud_llm" in found
    assert "worker.jobs.lazy reaches worker.cloud_llm" in found
    assert "worker.sneaky imports cloud SDK openai" in found
    assert "worker.direct_http imports HTTP client httpx" in found
    assert "worker.direct_http mentions a cloud model host" in found
    assert "worker.fine" not in found
    assert "worker.cloud_llm imports" not in found


def test_allowlisted_module_may_import_cloud_llm(tmp_path: Path):
    _write(tmp_path, "worker/__init__.py", "")
    _write(tmp_path, "worker/cloud_llm.py", "")
    _write(tmp_path, "worker/coach_research.py", "from worker import cloud_llm\n")
    modules = scan(tmp_path, ("worker",))
    assert violations(modules, frozenset({"worker.coach_research"})) == []
