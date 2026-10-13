"""The system registry.

Every sub-package of `systems/` whose name does not start with "_" is a system, and its
`manifest.py` defines `MANIFEST`. The registry reads manifests only; handlers, console
routes and eval scorers are imported by name on first use. That keeps this module (and
`worker.jobs`, which uses it) outside the cloud boundary: a system on the cloud
allowlist is never reachable from the core by a static import.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from systems._contract import JobSpec, Manifest

__all__ = [
    "Manifest",
    "all_manifests",
    "get",
    "job_spec",
    "job_types",
    "load_target",
    "reset",
]


@lru_cache(maxsize=1)
def _discover() -> dict[str, Manifest]:
    found: dict[str, Manifest] = {}
    for info in pkgutil.iter_modules(__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}.manifest")
        manifest = getattr(module, "MANIFEST", None)
        if not isinstance(manifest, Manifest):
            raise TypeError(f"systems/{info.name}/manifest.py must define MANIFEST = Manifest(...)")
        if manifest.key != info.name:
            raise ValueError(f"systems/{info.name}: manifest key is {manifest.key!r}")
        found[manifest.key] = manifest
    return dict(sorted(found.items()))


def reset() -> None:
    """Forget discovered systems (tests that add a system on disk call this)."""
    _discover.cache_clear()
    _job_index.cache_clear()


def all_manifests() -> dict[str, Manifest]:
    return dict(_discover())


def get(key: str) -> Manifest:
    try:
        return _discover()[key]
    except KeyError:
        raise KeyError(f"no system named {key!r}") from None


@lru_cache(maxsize=1)
def _job_index() -> dict[str, tuple[Manifest, JobSpec]]:
    index: dict[str, tuple[Manifest, JobSpec]] = {}
    for manifest in _discover().values():
        for spec in manifest.jobs:
            if spec.job_type in index:
                other = index[spec.job_type][0].key
                raise ValueError(
                    f"job type {spec.job_type!r} is declared by {other} and {manifest.key}"
                )
            index[spec.job_type] = (manifest, spec)
    return index


def job_spec(job_type: str) -> tuple[Manifest, JobSpec] | None:
    return _job_index().get(job_type)


def job_types() -> dict[str, str]:
    """job_type -> system key, for every job type any system declares."""
    return {jt: m.key for jt, (m, _) in _job_index().items()}


def load_target(target: str) -> Callable[..., Any] | Any:
    """Import "package.module:attribute" and return the attribute."""
    module_name, _, attr = target.partition(":")
    return getattr(importlib.import_module(module_name), attr)
