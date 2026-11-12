"""Write fact.schema.json from the Pydantic models in core/facts.py.

Run: python scripts/write_schema.py
tests/test_models.py fails if the committed file and the models disagree.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ROOT  # noqa: E402
from core.facts import fact_schema  # noqa: E402


def main() -> None:
    path = ROOT / "fact.schema.json"
    path.write_text(json.dumps(fact_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
