"""coaches: the roster, vetting files with evidence, and readiness for the Phase 1 gate.

No model and no jobs in Phase 1. Recruiting research and outreach drafts come later as
their own systems. Vetting is human-only (CLAUDE.md hard rule 5): the system records
evidence and who verified it; only a founder verifies an item or activates a coach, and
the Ledger refuses anyone else.
"""

from __future__ import annotations

from systems._contract import ConsoleSection, Manifest, SettingSpec

MANIFEST = Manifest(
    key="coaches",
    title="Coaches",
    description="Coach roster, vetting files with evidence, and launch readiness.",
    version=1,
    data_class="coach",
    settings=(
        SettingSpec(
            "coaches.required_per_need_state",
            "int",
            2,
            "Active coaches needed per launched need-state",
            "The Phase 1 gate: active coaches with open capacity in each launched need-state "
            "of a market before ads run there.",
            minimum=1,
            maximum=20,
        ),
    ),
    console=ConsoleSection(title="Coaches", routes="systems.coaches.console:routes", nav_order=10),
)
