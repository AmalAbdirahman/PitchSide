"""Minimal loader for config.yaml (standard library only).

Supports the subset we use: nested mappings by indentation, scalars
(int, float, bool, quoted or bare strings), inline lists `[a, b]`
and block lists of scalars (`- item`). Comments start with `#`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _scalar(raw: str) -> Any:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def _value(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [_scalar(p) for p in raw[1:-1].split(",") if p.strip()]
    return _scalar(raw)


def _strip_comment(line: str) -> str:
    if line.lstrip().startswith("#"):
        return ""
    if " #" in line and "'" not in line and '"' not in line:
        line = line.split(" #", 1)[0]
    return line.rstrip()


def parse(text: str) -> dict[str, Any]:
    """Parse the config subset into nested dicts and lists."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    pending: tuple[int, dict[str, Any], str] | None = None
    for line in (_strip_comment(raw) for raw in text.splitlines()):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        while stack[-1][0] >= indent:
            stack.pop()
        if pending is not None:
            p_indent, p_parent, p_key = pending
            container: Any = [] if body.startswith("- ") else {}
            p_parent[p_key] = container
            stack.append((p_indent, container))
            pending = None
        parent = stack[-1][1]
        if body.startswith("- "):
            parent.append(_value(body[2:]))
            continue
        key, _, rest = body.partition(":")
        if rest.strip():
            parent[key.strip()] = _value(rest)
        else:
            pending = (indent, parent, key.strip())
    return root


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    """Load config.yaml from the repo root (cached)."""
    return parse((ROOT / "config.yaml").read_text(encoding="utf-8-sig"))
