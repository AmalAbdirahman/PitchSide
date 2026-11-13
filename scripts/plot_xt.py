"""Draw the xT figures for docs/methods/xt.md from data/xt_fit.json (no refit, no network).

Run: python -m pip install -e ".[data]"   (once; matplotlib)
     python scripts/plot_xt.py

Writes docs/methods/xt_surface.png (the fitted surface as a heatmap with each cell's value) and
docs/methods/xt_convergence.png (largest change in any cell per value-iteration step, log scale).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib  # noqa: E402

matplotlib.use("Agg")  # files only, no window
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import PowerNorm  # noqa: E402

from core.config import ROOT  # noqa: E402

RECORD = ROOT / "data" / "xt_fit.json"
OUT = ROOT / "docs" / "methods"
# No Software tag: matplotlib writes its web address there, and nothing in docs/ names a URL.
NO_METADATA: dict[str, str | None] = {"Software": None}


def surface_png(surface: list[list[float]], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 6.6), dpi=110)
    top = max(max(r) for r in surface)
    # Square-root colour scale, so differences in midfield stay visible next to the goal cells.
    img = ax.imshow(surface, cmap="Blues", norm=PowerNorm(0.5, vmin=0, vmax=top), origin="upper")
    for r, row in enumerate(surface):
        for c, v in enumerate(row):
            ink = "white" if (v / top) ** 0.5 > 0.55 else "#0f172a"
            ax.text(c, r, f"{v:.3f}", ha="center", va="center", fontsize=7, color=ink)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("attacking left to right  →  goal")
    ax.set_title("Fitted xT surface (16 x 12 cells)")
    fig.colorbar(img, ax=ax, label="xT (square-root colour scale)", shrink=0.8)
    fig.tight_layout()
    fig.savefig(path, metadata=NO_METADATA)
    plt.close(fig)


def convergence_png(deltas: list[float], tol: float, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=110)
    ax.semilogy(range(1, len(deltas) + 1), deltas, color="#1e3a8a")
    ax.axhline(tol, color="#b91c1c", linestyle="--", linewidth=1, label=f"stop at {tol:g}")
    ax.set_xlabel("iteration")
    ax.set_ylabel("largest change in any cell")
    ax.set_title("Value iteration convergence")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, metadata=NO_METADATA)
    plt.close(fig)


def main() -> None:
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    surface_png(record["surface"], OUT / "xt_surface.png")
    convergence_png(record["max_change"], record["tol"], OUT / "xt_convergence.png")
    print(f"wrote {OUT / 'xt_surface.png'} and {OUT / 'xt_convergence.png'}")


if __name__ == "__main__":
    main()
