"""Draw Figure 1 for the IMR document: session procedure and design."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "submission" / "figures" / "imr-figure1.png"

BOXES = [
    ("Consent and briefing\n5 min", 2.05),
    ("Setup and practice\n5 min", 2.05),
    ("Six blocks, 3 to 5 min each\nA / B / C x high / low density\nLatin-square order", 4.15),
    ("Between blocks\nlog export, seen-before?\nabout 1 min", 2.55),
    ("Debrief\n5 min", 1.75),
]
COLORS = ["#dbe5f4", "#dfeee0", "#f6e7cc", "#e8e3f2", "#f5dede"]
GAP = 0.42


def build() -> Path:
    plt.rcParams["font.family"] = "DejaVu Serif"
    fig, ax = plt.subplots(figsize=(12.4, 2.4))
    ax.set_xlim(0, 15.0)
    ax.set_ylim(0, 3.0)
    ax.axis("off")

    x = 0.2
    edges = []
    for (label, width), color in zip(BOXES, COLORS):
        ax.add_patch(FancyBboxPatch(
            (x, 0.6), width, 1.8,
            boxstyle="round,pad=0.02,rounding_size=0.12",
            linewidth=1.1, edgecolor="#4a4f5c", facecolor=color,
        ))
        ax.text(x + width / 2, 1.5, label, ha="center", va="center", fontsize=9.6,
                color="#1d2130", linespacing=1.45)
        edges.append((x, x + width))
        x += width + GAP

    for i in range(len(edges) - 1):
        start = edges[i][1] + 0.04
        end = edges[i + 1][0] - 0.04
        ax.add_patch(FancyArrowPatch((start, 1.5), (end, 1.5), arrowstyle="-|>",
                                     mutation_scale=13, linewidth=1.1, color="#4a4f5c"))
    ax.text(7.5, 0.24, "Total session: about 45 minutes", ha="center", va="center",
            fontsize=9.4, color="#4a4f5c")
    fig.tight_layout(pad=0.2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=260, facecolor="white")
    plt.close(fig)
    return OUT


if __name__ == "__main__":
    print(build())
