"""Check every system against the contract (systems/_contract.py, docs/systems.md).

    python scripts/check_systems.py        # CI, pre-commit, and tests/unit/test_system_contract.py

Returns a list of problems; empty means every system is well formed. The checks:

* manifests are data only (they import nothing but the contract);
* job types are unique and namespaced by the system key; handlers, eval scorers and
  console routes resolve and live inside the system's own package;
* every model use names a known profile, and systems handling coach or prospect data
  use local profiles only and are not on the cloud allowlist;
* every prompt in use exists and has a header; a system that calls a model has eval cases;
* settings are namespaced by the system key and their defaults match their type;
* n8n workflows named by a manifest exist and are prefixed by the system key;
* system code does not open its own database connection or configure logging (it uses
  the core's helpers, so the actor, the event trigger and log scrubbing always apply).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import systems
from systems._contract import CLOUD_ALLOWED, KEY, TARGET, Manifest, SettingSpec

ROOT = Path(__file__).resolve().parent.parent

_SETTING_TYPES = {"int", "number", "bool", "text", "choice"}
_DATA_CLASSES = {"none", "public", "founder_brief", "coach", "prospect"}
_FORBIDDEN_CALLS = {
    ("psycopg", "connect"): "opens its own database connection",
    ("logging", "basicConfig"): "configures logging",
}


def _default_matches(spec: SettingSpec) -> bool:
    d = spec.default
    if spec.type == "bool":
        return isinstance(d, bool)
    if spec.type == "int":
        return isinstance(d, int) and not isinstance(d, bool)
    if spec.type == "number":
        return isinstance(d, int | float) and not isinstance(d, bool)
    if spec.type == "text":
        return isinstance(d, str)
    if spec.type == "choice":
        return isinstance(d, str) and d in spec.choices
    return False


def cloud_allowlist(root: Path = ROOT) -> frozenset[str]:
    """Read cloud_llm.ALLOWED_CALLERS from its source, without importing the module.

    Importing worker.cloud_llm here would put this checker inside the cloud boundary
    (tests/unit/test_import_boundary.py).
    """
    tree = ast.parse((root / "worker" / "cloud_llm.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and target.id == "ALLOWED_CALLERS" and value is not None:
            if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "frozenset":
                return frozenset(ast.literal_eval(value.args[0])) if value.args else frozenset()
            return frozenset(ast.literal_eval(value))
    raise ValueError("worker/cloud_llm.py defines no ALLOWED_CALLERS")


def _check_manifest(m: Manifest, root: Path) -> list[str]:
    from worker import prompts
    from worker.config import MODEL_PROFILES

    out: list[str] = []
    k = m.key
    own = f"systems.{k}."
    if not KEY.match(k):
        out.append(f"{k}: key must match {KEY.pattern}")
    if not m.title.strip() or not m.description.strip():
        out.append(f"{k}: title and description are required")
    if m.version < 1:
        out.append(f"{k}: version starts at 1")
    if m.data_class not in _DATA_CLASSES:
        out.append(f"{k}: unknown data_class {m.data_class!r}")

    uses = {u.name for u in m.models}
    for spec in m.jobs:
        if spec.job_type != k and not spec.job_type.startswith(k + "."):
            out.append(f"{k}: job type {spec.job_type!r} must be {k!r} or start with {k + '.'!r}")
        if not TARGET.match(spec.handler) or not spec.handler.startswith(own):
            out.append(f"{k}: handler {spec.handler!r} must be '{own}<module>:<function>'")
        else:
            try:
                if not callable(systems.load_target(spec.handler)):
                    out.append(f"{k}: handler {spec.handler!r} is not callable")
            except (ImportError, AttributeError) as exc:
                out.append(f"{k}: handler {spec.handler!r} does not resolve ({type(exc).__name__})")
        if spec.model_use is not None and spec.model_use not in uses:
            out.append(f"{k}: job {spec.job_type!r} uses undeclared model use {spec.model_use!r}")

    for use in m.models:
        kind = MODEL_PROFILES.get(use.profile)
        if kind is None:
            out.append(f"{k}: model use {use.name!r} names unknown profile {use.profile!r}")
        elif kind != "local" and m.data_class not in CLOUD_ALLOWED:
            out.append(f"{k}: handles {m.data_class} data, so model use {use.name!r} must be local")

    if m.data_class not in CLOUD_ALLOWED:
        on_list = sorted(c for c in cloud_allowlist(ROOT) if c.startswith(own))
        if on_list:
            out.append(f"{k}: handles {m.data_class} data but {on_list} are on the cloud allowlist")

    for p in m.prompts:
        try:
            prompts.load(k, p.name, p.version, root=root / "prompts")
        except (OSError, ValueError) as exc:
            out.append(f"{k}: prompt {p.name}.v{p.version} ({type(exc).__name__})")

    if m.models:
        cases = root / "evals" / "synthetic" / k / "cases.jsonl"
        if not m.evals:
            out.append(f"{k}: calls a model, so it must name an eval scorer")
        if not cases.is_file() or not cases.read_text(encoding="utf-8").strip():
            out.append(f"{k}: calls a model, so evals/synthetic/{k}/cases.jsonl must have cases")
        else:
            for n, line in enumerate(cases.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    try:
                        row = json.loads(line)
                        assert {"id", "input"} <= set(row)
                    except (ValueError, AssertionError):
                        out.append(f"{k}: eval case line {n} needs JSON with id and input")
    if m.evals:
        if not TARGET.match(m.evals) or not m.evals.startswith(own):
            out.append(f"{k}: evals {m.evals!r} must be '{own}<module>:<function>'")
        else:
            try:
                systems.load_target(m.evals)
            except (ImportError, AttributeError) as exc:
                out.append(f"{k}: evals {m.evals!r} does not resolve ({type(exc).__name__})")

    seen: set[str] = set()
    for s in m.settings:
        if not s.key.startswith(k + ".") or s.key in seen:
            out.append(f"{k}: setting {s.key!r} must be unique and start with {k + '.'!r}")
        seen.add(s.key)
        if s.type not in _SETTING_TYPES:
            out.append(f"{k}: setting {s.key!r} has unknown type {s.type!r}")
        elif not _default_matches(s):
            out.append(f"{k}: setting {s.key!r} default does not match its type")

    if m.console is not None:
        if not TARGET.match(m.console.routes) or not m.console.routes.startswith(own):
            out.append(f"{k}: console routes {m.console.routes!r} must be '{own}console:routes'")
        else:
            try:
                routes = systems.load_target(m.console.routes)
                if not isinstance(routes, list | tuple):
                    out.append(f"{k}: console routes must be a list")
            except (ImportError, AttributeError) as exc:
                out.append(f"{k}: console routes do not resolve ({type(exc).__name__})")

    for stem in m.n8n_workflows:
        if not stem.startswith(f"{k}--"):
            out.append(f"{k}: n8n workflow {stem!r} must be named '{k}--<name>'")
        if not (root / "n8n" / "workflows" / f"{stem}.json").is_file():
            out.append(f"{k}: n8n workflow {stem}.json is missing")
    return out


def _check_code(key: str, folder: Path) -> list[str]:
    out: list[str] = []
    for path in sorted(folder.rglob("*.py")):
        rel = path.relative_to(folder.parent.parent)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.name == "manifest.py" and path.parent == folder:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    out.append(f"{key}: {rel} is data only and may not import modules")
                elif isinstance(node, ast.ImportFrom) and node.module not in (
                    "__future__",
                    "systems._contract",
                ):
                    out.append(f"{key}: {rel} may import only from systems._contract")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "worker.db":
                if any(a.name == "connect" for a in node.names):
                    out.append(f"{key}: {rel} opens its own database connection")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = node.func.value
                if isinstance(base, ast.Name):
                    why = _FORBIDDEN_CALLS.get((base.id, node.func.attr))
                    if why:
                        out.append(f"{key}: {rel} {why}")
                    if base.id == "logging" and node.func.attr.endswith("Handler"):
                        out.append(f"{key}: {rel} configures logging")
                if node.func.attr == "addHandler":
                    out.append(f"{key}: {rel} configures logging")
    return out


def problems(root: Path = ROOT) -> list[str]:
    systems.reset()
    try:
        manifests = systems.all_manifests()
        systems.job_types()
    except (ImportError, TypeError, ValueError) as exc:
        return [f"registry: {exc}"]
    found: list[str] = []
    for key, manifest in manifests.items():
        found += _check_manifest(manifest, root)
        found += _check_code(key, root / "systems" / key)
    return found
