"""The core holds no business data (docs/phase-1-design.md section 3.6).

Business name, founders, markets and need-states come from the Ledger (seeded from a
business profile in deploy/business/), so another business can run the same code. This
test reads every profile and fails if any of those values appears as a string in core or
system code, or in a console template. Docstrings and comments are exempt.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = ("worker", "review", "systems")


def _forbidden() -> dict[str, re.Pattern[str]]:
    words: set[str] = set()
    for path in sorted((ROOT / "deploy" / "business").glob("*.toml")):
        p = tomllib.loads(path.read_text(encoding="utf-8"))
        words.add(p["business"]["name"])
        words.update(f["name"] for f in p.get("founders", []))
        for m in p.get("markets", []):
            words.update({m["key"], m["name"]})
        for n in p.get("need_states", []):
            words.update({n["key"], n["label"]})
    return {w: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])", re.I) for w in words}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip
    ]


def test_profiles_exist():
    assert list((ROOT / "deploy" / "business").glob("*.toml"))


def test_no_business_data_in_core_or_system_code():
    patterns = _forbidden()
    found: list[str] = []
    for d in CODE:
        for path in sorted((ROOT / d).rglob("*.py")):
            for s in _strings(path):
                found += [
                    f"{path.relative_to(ROOT)}: {w!r}" for w, p in patterns.items() if p.search(s)
                ]
        for path in sorted((ROOT / d).rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            found += [
                f"{path.relative_to(ROOT)}: {w!r}" for w, p in patterns.items() if p.search(text)
            ]
    assert sorted(set(found)) == []
