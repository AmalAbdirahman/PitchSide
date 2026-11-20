"""Ask PitchSide live mode (R6): a local server for typed questions, on this machine only.

Runs the R5 Analyst (Microsoft Foundry, with the keys in your local .env) and the same code checks
on one question at a time, and answers only on 127.0.0.1. The public site calls it only after you
click the panel's "Live (local)" tab. Synthetic match only; live answers are not cached.

Run:  pip install -e ".[agents]"
      python scripts/ask_server.py               # then: python -m http.server 8000 --directory docs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ask import FoundryAnalyst  # noqa: E402
from app.ask_live import Gate, LiveService, make_server  # noqa: E402
from app.build_bundle import load_match  # noqa: E402
from core.config import ROOT, load  # noqa: E402
from core.facts import Fact  # noqa: E402

HOST = "127.0.0.1"


def main() -> None:
    s = load()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--port", type=int, default=s.ask.live.port, help="must match docs/js/ask_live.js"
    )
    args = ap.parse_args()
    match, _ = load_match()
    hero = json.loads((ROOT / "docs" / "data" / "hero.json").read_text(encoding="utf-8"))
    facts = [Fact.model_validate(f) for f in hero["facts"]]
    service = LiveService(match, facts, s, FoundryAnalyst(s), Gate(s.ask.live.timeout_s))
    srv = make_server(service, s.ask.live, HOST, args.port)
    print(f"Ask PitchSide live mode on http://{HOST}:{args.port} (Ctrl+C to stop)")
    print("Allowed pages: " + ", ".join(s.ask.live.allowed_origins))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
