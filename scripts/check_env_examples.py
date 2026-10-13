"""Fail if an env example file looks like it holds a real secret.

Variables whose names suggest secrets (KEY, TOKEN, SECRET, PASSWORD, WEBHOOK) must be
empty in the example files. The dev DATABASE_URL in .env.example is allowed because it
points at the throwaway local Postgres from deploy/compose.dev.yml.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    ROOT / ".env.example",
    ROOT / "deploy" / "worker.env.example",
    ROOT / "deploy" / "console.env.example",
    ROOT / "deploy" / "vps.env.example",
]
SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|WEBHOOK)", re.IGNORECASE)


def main() -> int:
    problems = []
    for path in FILES:
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            value = value.split("#", 1)[0].strip()
            if SECRETISH.search(name) and value:
                problems.append(f"{path.relative_to(ROOT)}:{n}: {name.strip()} must be empty here")
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
