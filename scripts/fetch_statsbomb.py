"""Download StatsBomb Open Data files into data/external/statsbomb/ (git-ignored).

Run: python scripts/fetch_statsbomb.py                 # the match and season in config.yaml
     python scripts/fetch_statsbomb.py --match 8650     # another match of that season
     python scripts/fetch_statsbomb.py --xg             # every match of the xg_fit seasons (~650 MB)

Downloads the season's match list, then events and lineups for the replayed match and for
`statsbomb.mu_matches` other matches of the same season (used only for the league xG rate).
Files that already exist are not downloaded again. Uses the standard library only.

The data is StatsBomb's and is covered by the StatsBomb Public Data User Agreement
(github.com/statsbomb/open-data). Raw files must not be committed or published.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import StatsBomb, XgFit, load  # noqa: E402
from core.statsbomb import RAW_DIR, mu_match_ids  # noqa: E402

USER_AGENT = "pitchside-fetch"


def fetch(sb: StatsBomb, rel: str) -> Path:
    """Download one file from the open-data repository unless it is already on disk."""
    dest = RAW_DIR / rel
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(f"{sb.base_url}/{rel}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as res:
        dest.write_bytes(res.read())
    print(f"downloaded {rel}")
    return dest


def main(match_id: int, sb: StatsBomb) -> None:
    season = fetch(sb, f"matches/{sb.competition_id}/{sb.season_id}.json")
    rows = json.loads(season.read_text(encoding="utf-8"))
    if not any(r["match_id"] == match_id for r in rows):
        raise SystemExit(
            f"match {match_id} is not in competition {sb.competition_id} season {sb.season_id}"
        )
    for mid in [match_id, *mu_match_ids(rows, match_id, sb.mu_matches)]:
        fetch(sb, f"events/{mid}.json")
        fetch(sb, f"lineups/{mid}.json")
    print(f"raw StatsBomb files in {RAW_DIR} (git-ignored; do not commit)")


def fetch_xg_seasons(sb: StatsBomb, fit: XgFit) -> None:
    """Match lists and events (no lineups needed) for every season the xG model is fitted on."""
    rels = []
    for comp, season in fit.seasons:
        rows = json.loads(fetch(sb, f"matches/{comp}/{season}.json").read_text(encoding="utf-8"))
        rels += [f"events/{r['match_id']}.json" for r in rows]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda rel: fetch(sb, rel), rels))
    print(f"{len(rels)} matches for the xG fit in {RAW_DIR} (git-ignored; do not commit)")


if __name__ == "__main__":
    settings = load()
    ap = argparse.ArgumentParser(description="Download StatsBomb Open Data.")
    ap.add_argument("--match", type=int, default=settings.statsbomb.match_id)
    ap.add_argument(
        "--xg", action="store_true", help="download the seasons the xG model is fitted on"
    )
    args = ap.parse_args()
    if args.xg:
        fetch_xg_seasons(settings.statsbomb, settings.xg_fit)
    else:
        main(args.match, settings.statsbomb)
