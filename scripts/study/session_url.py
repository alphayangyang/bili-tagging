"""Build study-session URLs for the examiner (protocol §2.3 / §7.1).

Given a participant number, this prints the three condition URLs with the video
pair the latin square assigns, so the examiner can paste one URL per condition
into Chrome and mark the matching pages. It reads data/study/stimuli.json for
the six pinned videos.

Usage::

    python3 scripts/study/session_url.py --participant 7
    python3 scripts/study/session_url.py --participant 7 --condition C
    python3 scripts/study/session_url.py --sheet data/study/run-sheet.csv   # all participants
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_STIMULI = REPO / "data" / "study" / "stimuli.json"

# protocol §2.3: subject index mod 6 -> {condition: pair} and condition order
# Each participant views all three video pairs, one pair under each condition,
# and each pair contributes both of its videos, so a participant watches all six
# videos once.  The cyclic and anti-cyclic assignment rows below put every video
# under every condition exactly twice across the six participants, and the six
# condition orders balance carry-over.
LATIN = (
    ({"A": 1, "B": 2, "C": 3}, ("A", "B", "C")),
    ({"A": 2, "B": 3, "C": 1}, ("A", "C", "B")),
    ({"A": 3, "B": 1, "C": 2}, ("B", "A", "C")),
    ({"A": 1, "B": 3, "C": 2}, ("B", "C", "A")),
    ({"A": 2, "B": 1, "C": 3}, ("C", "A", "B")),
    ({"A": 3, "B": 2, "C": 1}, ("C", "B", "A")),
)


def load_pairs(path: Path) -> dict[int, tuple[dict, dict]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    stimuli = manifest["stimuli"] if isinstance(manifest, dict) else manifest
    pairs: dict[int, list[dict]] = {}
    for index in range(0, len(stimuli), 2):
        pair = stimuli[index : index + 2]
        if len(pair) == 2:
            pairs[index // 2 + 1] = pair
    return {number: (pair[0], pair[1]) for number, pair in pairs.items()}


def urls_for(participant: int, pairs: dict[int, tuple[dict, dict]], base: str) -> list[dict]:
    assignment, order = LATIN[(participant - 1) % 6]
    rows = []
    for position, condition in enumerate(order, start=1):
        pair = pairs[assignment[condition]]
        for offset, entry in enumerate(pair):
            block = (position - 1) * 2 + offset + 1
            study_id = "P%02d-%d-%s" % (participant, block, condition)
            density = entry.get("density") or "high"
            url = "%s/video/%s/?dmf_study=%s&dmf_cond=%s&dmf_density=%s" % (
                base, entry["bvid"], study_id, condition, density)
            rows.append(
                {
                    "participant": "P%02d" % participant,
                    "condition": condition,
                    "condition_order": position,
                    "pair": assignment[condition],
                    "block": block,
                    "bvid": entry["bvid"],
                    "genre": entry.get("genre"),
                    "density": entry.get("density"),
                    "study_id": study_id,
                    "url": url,
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--participant", type=int, default=0, help="1-based participant number")
    parser.add_argument("--condition", choices=("A", "B", "C"), default="")
    parser.add_argument("--stimuli", default=str(DEFAULT_STIMULI))
    parser.add_argument("--base", default="https://www.bilibili.com", help="site base URL")
    parser.add_argument("--sheet", default="", help="write this CSV path for all participants")
    parser.add_argument("--participants", type=int, default=30, help="rows for --sheet")
    args = parser.parse_args(argv)

    pairs = load_pairs(Path(args.stimuli))
    if len(pairs) != 3:
        print("stimuli.json must contain three video pairs", file=sys.stderr)
        return 2

    if args.sheet:
        target = Path(args.sheet)
        rows = []
        for number in range(1, args.participants + 1):
            rows.extend(urls_for(number, pairs, args.base))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print("wrote %d URLs to %s" % (len(rows), target))
        return 0

    if not args.participant:
        parser.error("--participant or --sheet is required")
    rows = urls_for(args.participant, pairs, args.base)
    if args.condition:
        rows = [row for row in rows if row["condition"] == args.condition]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
