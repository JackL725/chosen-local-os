"""Load versioned prompt files: prompts/<system>/<name>.vN.md.

A prompt file starts with a header block between `---` lines (purpose, inputs, output
schema, rules), then the prompt text. Only the text after the header goes to the model.
The prompt version recorded in the Ledger is "<system>/<name>.vN".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_NAME = re.compile(r"^(?P<name>[a-z0-9_-]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    version: str
    header: dict[str, str]
    text: str


def load(system: str, name: str, version: int, *, root: Path = PROMPTS_DIR) -> Prompt:
    path = root / system / f"{name}.v{version}.md"
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"{path} has no header block")
    _, header_block, body = raw.split("---\n", 2)
    header: dict[str, str] = {}
    for line in header_block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            header[key.strip()] = value.strip()
    for required in ("purpose", "inputs", "output_schema"):
        if required not in header:
            raise ValueError(f"{path} header is missing '{required}'")
    return Prompt(version=f"{system}/{name}.v{version}", header=header, text=body.strip())
