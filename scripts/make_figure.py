"""Draw Figure 1 (research design map) for the pilot-study submission."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "submission" / "figures" / "figure1-design.png"

BOX_W = 2.80
BOX_H = 1.92
XS = [0.10, 3.25, 6.40, 9.55]
Y = 2.50

PHASES = [
    ("Phase 0", "Taxonomy & instrument",
     "seven social-function tags\nrelevance vector, guideline\nKrippendorff\u2019s alpha, 240 comments",
     "#dbe7f5"),
    ("Phase 1", "Controlled experiment",
     "type \u00d7 volume, within-subject\nfour conditions, balanced order\ndiscomfort, control, FOMO, recall",
     "#dcefdc"),
    ("Phase 2", "System",
     "offline tagger (LLM + rules)\nbrowser interceptor of the stream\npolicy: show / hide / fold",
     "#f7e9d2"),
    ("Phase 3", "Evaluation",
     "tag accuracy and latency\nfield study, two weeks, N=10\neffort and experience scales",
     "#ecdff2"),
]

BOTTOM = [
    ("Pilot study (completed)",
     "1,407 real danmaku tagged offline at 0.53 s per comment\n"
     "dominant tags: humour 64%, emotional resonance 17%, information 14%\n"
     "29-comment audit: 89.7% dominant-tag agreement, MAE 0.05\n"
     "interception verified: 40 unit assertions, fail-open path"),
    ("Feedback loop",
     "corrections and field logs refine tag thresholds,\n"
     "rules and explanation wording within the deployment"),
]


def build_figure() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12.6, 4.75))
    ax.set_xlim(0, 12.6)
    ax.set_ylim(0, 4.72)
    ax.axis("off")

    for x, (num, name, body, color) in zip(XS, PHASES):
        ax.add_patch(FancyBboxPatch((x, Y), BOX_W, BOX_H,
                                    boxstyle="round,pad=0.02,rounding_size=0.07",
                                    linewidth=1.1, edgecolor="#4d4d4d", facecolor=color))
        ax.text(x + BOX_W / 2, Y + BOX_H - 0.10, num, ha="center", va="top",
                fontsize=9.6, fontweight="bold", family="serif")
        ax.text(x + BOX_W / 2, Y + BOX_H - 0.40, name, ha="center", va="top",
                fontsize=9.6, fontweight="bold", family="serif")
        ax.text(x + BOX_W / 2, Y + BOX_H - 0.78, body, ha="center", va="top",
                fontsize=8.0, family="serif", linespacing=1.45)

    for x in XS[:-1]:
        ax.add_patch(FancyArrowPatch((x + BOX_W + 0.03, Y + BOX_H / 2),
                                     (x + BOX_W + 0.31, Y + BOX_H / 2),
                                     arrowstyle="-|>", mutation_scale=13,
                                     linewidth=1.2, color="#4d4d4d"))

    pilot_x, pilot_w = 0.10, 5.85
    ax.add_patch(FancyBboxPatch((pilot_x, 0.30), pilot_w, 1.62,
                                boxstyle="round,pad=0.02,rounding_size=0.07",
                                linewidth=1.0, edgecolor="#777777", facecolor="#f4f4f4",
                                linestyle="--"))
    ax.text(pilot_x + pilot_w / 2, 1.78, BOTTOM[0][0], ha="center", va="top",
            fontsize=9.6, fontweight="bold", family="serif")
    ax.text(pilot_x + pilot_w / 2, 1.52, BOTTOM[0][1], ha="center", va="top",
            fontsize=7.7, family="serif", linespacing=1.5)

    fb_x, fb_w = 6.55, 5.85
    ax.add_patch(FancyBboxPatch((fb_x, 0.30), fb_w, 1.62,
                                boxstyle="round,pad=0.02,rounding_size=0.07",
                                linewidth=1.0, edgecolor="#777777", facecolor="#f4f4f4",
                                linestyle="--"))
    ax.text(fb_x + fb_w / 2, 1.78, BOTTOM[1][0], ha="center", va="top",
            fontsize=9.6, fontweight="bold", family="serif")
    ax.text(fb_x + fb_w / 2, 1.46, BOTTOM[1][1], ha="center", va="top",
            fontsize=8.0, family="serif", linespacing=1.45)

    ax.add_patch(FancyArrowPatch((pilot_x + pilot_w / 2, 1.96),
                                 (pilot_x + pilot_w / 2, 2.47),
                                 arrowstyle="-|>", mutation_scale=13,
                                 linewidth=1.1, color="#777777", linestyle="--"))
    ax.add_patch(FancyArrowPatch((9.55 + BOX_W / 2, 2.47),
                                 (9.55 + BOX_W / 2, 1.96),
                                 arrowstyle="-|>", mutation_scale=13,
                                 linewidth=1.1, color="#777777", linestyle="--"))

    fig.savefig(OUT, dpi=260, bbox_inches="tight")
    plt.close(fig)
    return OUT


if __name__ == "__main__":
    print(build_figure())
