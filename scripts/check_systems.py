"""Check every system against the contract. Exit 1 and list the problems if any.

Usage: uv run python scripts/check_systems.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from systems._check import problems


def main() -> int:
    found = problems()
    for p in found:
        print(p)
    if not found:
        print("systems: ok")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
