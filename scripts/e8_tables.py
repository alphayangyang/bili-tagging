"""Markdown tables for the e8 boundary report, generated from result + detail JSON."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "experiments" / "results"
BACKENDS = ("deepseek", "qwen35-local")
STYLES = ("v2", "v3", "v4")


def load_runs(backend: str) -> dict:
    path = RESULTS / ("e8_%s.json" % backend)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["runs"]


def load_detail(backend: str) -> dict | None:
    path = RESULTS / ("e8_detail_%s.json" % backend)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def boundary_totals(detail: dict, style: str) -> tuple[int, int, int]:
    """Return (dominant hits, must hits, total) over all probes."""
    hits = must = total = 0
    for bucket in detail["sets"]["boundary"]["pairs"].values():
        hits += bucket.get(style, 0)
        must += bucket.get("must_" + style, 0)
        total += bucket["n"]
    return hits, must, total


def summary_table() -> str:
    lines = [
        "| backend | prompt | audit (29) | indomain (131) | boundary dominant (46) | boundary must-hit (46) | info recall (indomain) | ms/item (indomain) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for backend in BACKENDS:
        runs = load_runs(backend)
        detail = load_detail(backend)
        for style in STYLES:
            audit = runs.get(style + "/audit")
            indomain = runs.get(style + "/indomain")
            if not (audit and indomain):
                continue
            cells = [backend, style, "%.3f" % audit["agreement"], "%.3f" % indomain["agreement"]]
            if detail:
                hits, must, total = boundary_totals(detail, style)
                cells.append("%d/%d = %.3f" % (hits, total, hits / total))
                cells.append("%d/%d = %.3f" % (must, total, must / total))
            else:
                cells.extend(["-", "-"])
            recall = (indomain.get("per_label_recall") or {}).get("information")
            cells.append("%.3f" % recall if recall is not None else "-")
            ms = indomain.get("ms_per_item")
            cells.append("%.0f" % ms if ms else "cached")
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def pair_table() -> str:
    header = "| boundary pair | n | deepseek v2 | v3 | v4 | qwen v2 | v3 | v4 |"
    lines = [header, "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    details = {backend: load_detail(backend) for backend in BACKENDS}
    pairs: list[str] = []
    for detail in details.values():
        if detail:
            for pair in detail["sets"]["boundary"]["pairs"]:
                if pair not in pairs:
                    pairs.append(pair)
    for pair in pairs:
        cells = [pair]
        n = 0
        for detail in details.values():
            if detail:
                n = detail["sets"]["boundary"]["pairs"][pair]["n"]
        cells.append(str(n))
        for backend in BACKENDS:
            detail = details[backend]
            for style in STYLES:
                bucket = detail["sets"]["boundary"]["pairs"].get(pair) if detail else None
                cells.append("%d/%d" % (bucket[style], bucket["n"]) if bucket else "-")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary_table())
    print()
    print(pair_table())
