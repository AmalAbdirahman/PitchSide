"""The bundle's build date (meta.built), without breaking byte-identical rebuilds.

A rebuild that produces the same bundle keeps the date already on disk; only a bundle whose
content changed gets today's date. So the date says when the published content last changed.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


def _without_date(hero: dict[str, Any]) -> dict[str, Any]:
    meta = {k: v for k, v in hero["meta"].items() if k != "built"}
    return {**hero, "meta": meta}


def previous(path: Path) -> dict[str, Any] | None:
    """The bundle already at path, or None if there is none or it is not valid JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no earlier bundle, or a broken one: treat as new content
        return None
    return data if isinstance(data, dict) else None


def stamp(hero: dict[str, Any], old: dict[str, Any] | None, today: date) -> dict[str, Any]:
    """hero with meta.built set: the old date if nothing else changed, else today."""
    built = today.isoformat()
    if old is not None and "built" in old.get("meta", {}):
        if _without_date(old) == _without_date(hero):
            built = old["meta"]["built"]
    return {**hero, "meta": {**hero["meta"], "built": built}}
