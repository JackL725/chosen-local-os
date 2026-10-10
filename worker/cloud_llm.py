"""The single gateway to cloud models. Phase 0: a stub that only enforces the rules.

Hard rule 3 (CLAUDE.md): anything derived from a call, a transcript, an extraction or
intake answers may use only local models. This module is the one place a cloud SDK or
endpoint may appear, and:

  * only modules in ALLOWED_CALLERS may import it (tests/unit/test_import_boundary.py
    walks every import, including transitive ones);
  * every call must pass `contains_prospect_data=False` explicitly. Anything else,
    including a missing argument, None or a truthy value, is refused.

Planned allowlist (added when those systems are built): coach research from public
sources, ad-script drafting from founder-written briefs. Code that dispatches to those
modules must import them lazily (by name), so the dispatcher itself never reaches
this module.
"""

from __future__ import annotations

import inspect

from worker.errors import CallerNotAllowed, ProspectDataRefused

ALLOWED_CALLERS: frozenset[str] = frozenset()


def _calling_module() -> str:
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back else None
        return caller.f_globals.get("__name__", "") if caller else ""
    finally:
        del frame


def complete(
    *,
    contains_prospect_data: bool,
    system: str,
    prompt: str,
    prompt_version: str,
) -> str:
    """Send a prompt to a cloud model. Refuses unless every rule holds."""
    if contains_prospect_data is not False:
        raise ProspectDataRefused()
    if _calling_module() not in ALLOWED_CALLERS:
        raise CallerNotAllowed()
    raise NotImplementedError("No cloud model is wired up in Phase 0.")
