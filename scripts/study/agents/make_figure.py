"""Figure 2 for the IMR document: simulated condition means.

Reads the assembled questionnaire CSV with the same loader the analysis uses so
the plotted values match data/study/analysis.md.  Error bars are participant-level
percentile bootstrap 95% intervals.

Usage::

    python3 scripts/study/agents/make_figure.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
ANALYZE = REPO / "scripts" / "study" / "analyze.py"
OUT = REPO / "submission" / "figures" / "imr-figure2-simulated.png"

MEASURES = (("discomfort", "Discomfort", "higher is worse"), ("control", "Control", ""), ("overall", "Overall", ""))
CONDITIONS = (
    ("A", "A  all-on", "#b9c9e2"),
    ("B", "B  keyword", "#cfe3cf"),
    ("C", "C  prototype", "#f6d9a8"),
    ("D", "D  sham", "#e2cfe6"),
)


def load_analyze():
    spec = importlib.util.spec_from_file_location("study_analyze", ANALYZE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["study_analyze"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    analyze = load_analyze()
    rows, problems = analyze.load_questionnaire(analyze.DEFAULT_QUESTIONNAIRE)
    if problems:
        print("input problems:", problems[:3])
    rows, _cleaning = analyze.clean_data(rows, [], analyze.load_video_durations())

    means: dict[str, dict[str, float]] = {}
    cis: dict[str, dict[str, list[float]]] = {}
    for outcome, _label, _hint in MEASURES:
        scores = analyze._participant_condition_scores(rows, outcome)
        means[outcome] = {}
        cis[outcome] = {}
        for condition, _name, _color in CONDITIONS:
            values = [by_cond[condition] for by_cond in scores.values() if condition in by_cond]
            means[outcome][condition] = round(sum(values) / len(values), 3) if values else 0.0
            cis[outcome][condition] = analyze.bootstrap_ci(values, samples=5000) or [means[outcome][condition]] * 2

    plt.rcParams.update({"font.family": "DejaVu Serif", "font.size": 9})
    fig, ax = plt.subplots(figsize=(6.4, 3.0), dpi=260)
    width = 0.19
    positions = range(len(MEASURES))
    for offset, (condition, name, color) in enumerate(CONDITIONS):
        shift = offset - (len(CONDITIONS) - 1) / 2
        xs = [p + shift * width for p in positions]
        ys = [means[outcome][condition] for outcome, _l, _h in MEASURES]
        low = [y - cis[outcome][condition][0] for (outcome, _l, _h), y in zip(MEASURES, ys)]
        high = [cis[outcome][condition][1] - y for (outcome, _l, _h), y in zip(MEASURES, ys)]
        bars = ax.bar(xs, ys, width=width, color=color, edgecolor="#4a4a4a", linewidth=0.6,
                      label=name, yerr=[low, high], capsize=2.2,
                      error_kw={"elinewidth": 0.7, "ecolor": "#4a4a4a"})
        del bars

    ax.set_xticks(list(positions))
    ax.set_xticklabels([label for _o, label, _h in MEASURES])
    ax.set_ylabel("Mean rating (1-7)")
    ax.set_ylim(1, 7)
    ax.set_yticks([1, 2, 3, 4, 5, 6, 7])
    ax.grid(axis="y", color="#e4e4e4", linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.16), fontsize=7.0)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=260, bbox_inches="tight", facecolor="white")
    print("wrote", OUT, "size", OUT.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
