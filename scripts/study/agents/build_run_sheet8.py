"""Build the 8-participant, 4-condition (A/B/C/D) run sheet for the AI simulation.

Design: each participant watches all six stimuli once, in a rotated video order.
The condition multiset per participant is {A:2, A:2?} no: base labels per video
(index order) are A,A,B,B,C,D; participant p rotates the label set by
shift = (p-1) % 4, so the multiset becomes {labels[shift]:2, labels[shift+1]:2,
labels[shift+2]:1, labels[shift+3]:1} and across the eight participants every
(video, condition) cell is covered exactly twice, every condition has 12 blocks,
and every video is seen 8 times.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AGENTS = REPO / "data" / "study" / "agents"
STIMULI = REPO / "data" / "study" / "stimuli.json"
OUT = AGENTS / "run-sheet-8.csv"
LABELS = ["A", "B", "C", "D"]
BASE = ["A", "A", "B", "B", "C", "D"]
PARTICIPANTS = 8


def main() -> int:
    manifest = json.loads(STIMULI.read_text(encoding="utf-8"))
    stimuli = manifest["stimuli"]
    rows = []
    for p in range(1, PARTICIPANTS + 1):
        shift = (p - 1) % 4
        order = stimuli[shift:] + stimuli[:shift]
        labels = [LABELS[(LABELS.index(base) + shift) % 4] for base in BASE]
        label_of = {stim["bvid"]: labels[i] for i, stim in enumerate(stimuli)}
        first_position: dict[str, int] = {}
        for block, stim in enumerate(order, start=1):
            condition = label_of[stim["bvid"]]
            first_position.setdefault(condition, block)
            density = stim.get("density") or "high"
            study_id = "P%02d-%d-%s" % (p, block, condition)
            url = "https://www.bilibili.com/video/%s/?dmf_study=%s&dmf_cond=%s&dmf_density=%s" % (
                stim["bvid"], study_id, condition, density)
            rows.append({
                "participant": "P%02d" % p,
                "condition": condition,
                "condition_order": first_position[condition],
                "pair": (stimuli.index(stim) // 2) + 1,
                "block": block,
                "bvid": stim["bvid"],
                "genre": stim.get("genre"),
                "density": density,
                "study_id": study_id,
                "url": url,
            })
    columns = ["participant", "condition", "condition_order", "pair", "block", "bvid", "genre", "density", "study_id", "url"]
    with OUT.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    # balance report
    from collections import Counter
    cells = Counter((row["bvid"], row["condition"]) for row in rows)
    conds = Counter(row["condition"] for row in rows)
    videos = Counter(row["bvid"] for row in rows)
    print("rows:", len(rows), "cells unique:", len(cells), "min/max per cell:", min(cells.values()), max(cells.values()))
    print("conditions:", dict(conds), "videos:", dict(videos))
    for p in range(1, PARTICIPANTS + 1):
        mine = [row for row in rows if row["participant"] == "P%02d" % p]
        print(mine[0]["participant"], [(row["block"], row["condition"], row["bvid"], row["density"]) for row in mine])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
