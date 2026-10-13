"""The system contract: what a system's manifest declares.

A system is a folder `systems/<key>/` with a `manifest.py` that defines `MANIFEST`.
Manifest modules hold data only: they import nothing but this module (and
`__future__`), so discovering systems never imports a handler, a model client or a
cloud SDK. Handlers, console routes and eval scorers are named as "module:attribute"
strings and imported on first use.

See docs/systems.md for how to add a system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
TARGET = re.compile(r"^systems\.[a-z][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*$")

# What kind of data a system handles decides which model profiles it may use.
#   none           synthetic or generated values only (healthcheck)
#   public         public information (a coach's own website)
#   founder_brief  text a founder wrote for the purpose (a creative brief)
#   coach          the coach roster and vetting files
#   prospect       anything from intake, calls, transcripts or extractions
DataClass = Literal["none", "public", "founder_brief", "coach", "prospect"]
CLOUD_ALLOWED: frozenset[str] = frozenset({"none", "public", "founder_brief"})

SettingType = Literal["int", "number", "bool", "text", "choice"]


@dataclass(frozen=True)
class JobSpec:
    """A job type this system handles. `job_type` is the key or starts with "<key>."."""

    job_type: str
    handler: str  # "systems.<key>.jobs:function"
    description: str = ""
    lease_s: int | None = None  # None: the worker default
    model_use: str | None = None  # name of a ModelUse below, if the handler calls a model


@dataclass(frozen=True)
class ModelUse:
    """One thing the system asks a model to do, bound to a profile in worker/config.py."""

    name: str
    profile: str = "local"


@dataclass(frozen=True)
class PromptSpec:
    """A prompt in use: prompts/<key>/<name>.v<version>.md.

    Switch `version` only when the new file's evals hold or improve
    (.claude/rules/prompts.md).
    """

    name: str
    version: int


@dataclass(frozen=True)
class SettingSpec:
    """A value a founder can change in the console. Key is "<system key>.<name>"."""

    key: str
    type: SettingType
    default: Any
    label: str
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConsoleSection:
    """The system's screens in the review console."""

    title: str
    routes: str  # "systems.<key>.console:routes", a list of Starlette routes
    nav_order: int = 100


@dataclass(frozen=True)
class Manifest:
    key: str
    title: str
    description: str
    version: int
    data_class: DataClass
    # Required systems are always on: the console cannot turn them off.
    required: bool = False
    jobs: tuple[JobSpec, ...] = ()
    models: tuple[ModelUse, ...] = ()
    prompts: tuple[PromptSpec, ...] = ()
    settings: tuple[SettingSpec, ...] = ()
    console: ConsoleSection | None = None
    evals: str | None = None  # "systems.<key>.evals:score"
    n8n_workflows: tuple[str, ...] = ()  # file stems in n8n/workflows/
    extra: dict[str, Any] = field(default_factory=dict)

    def prompt_version(self, name: str) -> int:
        for p in self.prompts:
            if p.name == name:
                return p.version
        raise KeyError(f"{self.key} declares no prompt named {name!r}")

    def model_use(self, name: str) -> ModelUse:
        for m in self.models:
            if m.name == name:
                return m
        raise KeyError(f"{self.key} declares no model use named {name!r}")

    def setting(self, name: str) -> SettingSpec:
        key = name if name.startswith(self.key + ".") else f"{self.key}.{name}"
        for s in self.settings:
            if s.key == key:
                return s
        raise KeyError(f"{self.key} declares no setting {key!r}")
