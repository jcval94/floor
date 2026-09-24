from __future__ import annotations

from pathlib import Path
from typing import Any


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw.strip('"').strip("'")


def load_simple_yaml(path: Path) -> dict:
    """Load the small mapping-only YAML subset used by FLOOR configuration.

    FLOOR deliberately keeps these operational config files free of lists and
    advanced YAML features so runtime code does not depend on a YAML package.
    """

    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value.strip() == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = _parse_scalar(value)
    return root
