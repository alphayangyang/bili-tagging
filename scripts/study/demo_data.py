"""Generate a synthetic end-to-end dataset to exercise the S2 analysis pipeline.

The real study needs 24 human participants. Until those sessions run, this
script fabricates a clearly-labelled demo dataset with the exact shapes the
protocol prescribes (one session JSON per participant x condition and one
questionnaire CSV row per participant x video block) so that
scripts/study/analyze.py can be validated and the result tables inspected.

Usage::

    python3 scripts/study/demo_data.py --out data/study/demo --participants 24
    python3 scripts/study/analyze.py --raw-dir data/study/demo/raw \
        --questionnaire data/study/demo/questionnaire.csv \
        --out data/study/demo/analysis.json \
        --markdown data/study/demo/analysis.md --quiet

The demo numbers are random noise: they prove the code path, not a hypothesis.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SCALE_ITEMS = {
    "discomfort": 5,
    "coview": 3,
    "control": 3,
    "nasa": 6,
    "comp": 5,
    "explain": 2,
    "overall": 2,
}
CONDITIONS = ("A", "B", "C")


# protocol §2.3: subject index mod 6 -> condition assignment + video pair
LATIN = (
    ({"A": 1, "B": 2, "C": 3}, ("A", "B", "C")),
    ({"A": 1, "B": 3, "C": 2}, ("A", "C", "B")),
    ({"A": 2, "B": 1, "C": 3}, ("B", "A", "C")),
    ({"A": 2, "B": 1, "C": 3}, ("B", "C", "A")),
    ({"A": 1, "B": 2, "C": 3}, ("C", "A", "B")),
    ({"A": 1, "B": 2, "C": 3}, ("C", "B", "A")),
)


def latin_blocks(index: int) -> dict[str, list[tuple[int, int]]]:
    """Map condition -> two (pair_index, density_rank) blocks for one participant."""
    pairs, order = LATIN[index % 6]
    return {condition: [(pairs[condition], 0), (pairs[condition], 1)] for condition in order}


def build_questionnaire(participants: int, stimuli: list[dict], seed: int):
    rng = random.Random(seed)
    rows = []
    for number in range(1, participants + 1):
        participant = "P%03d" % number
        for condition in CONDITIONS:
            for pair_index, density_rank in latin_blocks(number - 1)[condition]:
                genre = "information" if density_rank == 0 else "entertainment"
                candidates = [entry for entry in stimuli if entry["genre"] == genre]
                entry = candidates[(pair_index - 1) % len(candidates)]
                means = {"A": 4.2, "B": 3.6, "C": 2.9}
                base = means[condition] + rng.gauss(0, 0.5)
                row = {
                    "participant": participant,
                    "condition": condition,
                    "video": entry["bvid"],
                    "genre": genre,
                    "density": entry.get("density") or "high",
                    "block_order": "%s%d" % (condition, density_rank),
                    "familiar": "0",
                }
                for scale, count in SCALE_ITEMS.items():
                    offset = {"discomfort": 0.0, "coview": 0.3, "control": 1.6,
                              "nasa": 0.4, "comp": 3.4, "explain": 3.2,
                              "overall": 2.8}[scale]
                    for item in range(1, count + 1):
                        value = base - 2.0 + offset + rng.gauss(0, 0.4)
                        row["%s_%d" % (scale, item)] = round(min(7.0, max(1.0, value)), 1)
                row["fomo"] = round(min(7.0, max(1.0, base + rng.gauss(0, 0.6))), 1)
                row["recall"] = "记得 %d 个点" % rng.randint(1, 5)
                row["notes"] = ""
                rows.append(row)
    return rows


def build_sessions(participants: int, stimuli: list[dict], seed: int, out: Path) -> int:
    rng = random.Random(seed + 1)
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for number in range(1, participants + 1):
        participant = "P%03d" % number
        for condition in CONDITIONS:
            events = []
            for pair_index, density_rank in latin_blocks(number - 1)[condition]:
                genre = "information" if density_rank == 0 else "entertainment"
                entry = [item for item in stimuli if item["genre"] == genre][(pair_index - 1) % 3]
                for segment in range(3):
                    events.append({
                        "type": "segment",
                        "bvid": entry["bvid"],
                        "segment": segment,
                        "ts": 1000 * segment,
                        "shown": rng.randint(5, 40),
                        "hidden": rng.randint(0, 30) if condition == "C" else 0,
                        "collapsed": rng.randint(0, 20) if condition == "C" else 0,
                        "by_label": {"attack": rng.randint(0, 5)},
                        "latency_ms": round(rng.uniform(0.2, 12.0), 2),
                        "online": segment + 1,
                        "applied": condition == "C",
                    })
            payload = {
                "schema": 1,
                "study_id": "%s-%s" % (participant, condition),
                "participant": participant,
                "condition": condition,
                "started_at": "2026-11-20T09:00:00Z",
                "ended_at": "2026-11-20T09:%02d:00Z" % rng.randint(10, 50),
                "counts": {"shown": 100, "hidden": 20, "collapsed": 10},
                "events": events,
                "script_version": "0.4.0-demo",
                "prefs": {"min_confidence": 0.3},
                "ua": "demo",
            }
            target = out / ("dmf-study-%s-%s.json" % (participant, condition))
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO / "data" / "study" / "demo"))
    parser.add_argument("--participants", type=int, default=24)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--stimuli", default=str(REPO / "data" / "study" / "stimuli.json"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.stimuli).read_text(encoding="utf-8"))
    stimuli = manifest["stimuli"] if isinstance(manifest, dict) else manifest
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = build_questionnaire(args.participants, stimuli, args.seed)
    fields = ["participant", "condition", "video", "genre", "density", "block_order", "familiar",
              "fomo", "recall", "notes"]
    for scale, count in SCALE_ITEMS.items():
        fields.extend("%s_%d" % (scale, item) for item in range(1, count + 1))
    questionnaire = out / "questionnaire.csv"
    with questionnaire.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    sessions = build_sessions(args.participants, stimuli, args.seed, out / "raw")
    if not args.quiet:
        print(json.dumps({"questionnaire": str(questionnaire), "rows": len(rows),
                          "sessions": sessions, "raw_dir": str(out / "raw")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
