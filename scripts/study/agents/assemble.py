"""Assemble AI-participant outputs into the study analysis inputs (v2, blind, 4 conditions).

Reads data/study/agents/out/P01..P08.json (eight simulated participants, six
blocks each), data/study/agents/run-sheet-8.csv (the true condition of every
block), data/study/agents/materials-index.json (what each block actually showed)
and data/study/preflight/ (measured segment latencies), then writes:

* data/study/questionnaire.csv - one row per block, protocol §7.3 columns;
* data/study/raw/dmf-study-<pid>-<block>-<cond>.json - one session log per block
  so scripts/study/analyze.py can pair self-report with system counters.

The participants were blind: their block files never named the condition, so the
condition is joined back in from the run sheet here. The session logs are
reconstructions (the agents read offline instead of playing the video), so the
counters come from the policy pipeline that generated the material and the
latencies are the measured preflight values.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AGENTS = REPO / "data" / "study" / "agents"
PREFLIGHT = REPO / "data" / "study" / "preflight"
RAW = REPO / "data" / "study" / "raw"
CSV_PATH = REPO / "data" / "study" / "questionnaire.csv"
RUN_SHEET = AGENTS / "run-sheet-8.csv"

PARTICIPANTS = tuple("P%02d" % index for index in range(1, 9))
CONDITION_NAMES = {"A": "all_on", "B": "keyword", "C": "prototype", "D": "sham"}
SCALES = {
    "discomfort": 5,
    "coview": 3,
    "control": 3,
    "nasa": 6,
    "comp": 5,
    "expectancy": 2,
    "overall": 2,
}
CSV_COLUMNS = (
    ["participant", "condition", "video", "genre", "density", "block_order", "familiar"]
    + ["%s_%d" % (name, index) for name, count in SCALES.items() for index in range(1, count + 1)]
    + ["fomo", "recall", "explain_1", "explain_2", "notes"]
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_run_sheet(path: Path = RUN_SHEET) -> dict[tuple[str, int], dict]:
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    return {(row["participant"], int(row["block"])): row for row in rows}


def load_latency_by_condition() -> dict[str, list[float]]:
    values: dict[str, list[float]] = {"A": [], "B": [], "C": [], "D": []}
    if not PREFLIGHT.is_dir():
        return values
    for path in sorted(PREFLIGHT.glob("dmf-study-*.json")):
        payload = load_json(path)
        condition = str(payload.get("condition_label") or "")
        if condition not in values:
            continue
        for event in payload.get("events") or []:
            if str(event.get("type")) == "segment":
                latency = event.get("latency_ms")
                if isinstance(latency, (int, float)) and latency >= 0:
                    values[condition].append(float(latency))
    return values


def measured_latency(latencies: dict[str, list[float]], participant: str, condition: str, bvid: str) -> float:
    """Pick a measured segment latency for the block's condition.

    The sham condition reuses the prototype pipeline (whole pool prefetch, same
    panel), and the preflight run only measured A/B/C, so D falls back to C.
    Selection is deterministic on (participant, bvid, condition).
    """
    pool = latencies.get(condition) or latencies.get("C") or []
    if not pool:
        return 0.0
    seed = sum(ord(char) for char in participant + bvid + condition)
    return float(pool[seed % len(pool)])


def validate_block(block: dict, expected: dict, entry: dict, participant: str) -> list[str]:
    problems = []
    number = expected["block"]
    if str(block.get("study_id")) != "%s-%s" % (participant, number):
        problems.append("%s: study_id=%r expected %r" % (participant, block.get("study_id"), "%s-%s" % (participant, number)))
    for field in ("video", "genre", "density"):
        if str(block.get(field)) != str(expected.get("bvid" if field == "video" else field)):
            problems.append("%s block %s: %s=%r expected %r" % (participant, number, field, block.get(field), expected.get(field)))
    for name, count in SCALES.items():
        for index in range(1, count + 1):
            key = "%s_%d" % (name, index)
            value = block.get(key)
            if not isinstance(value, int) or not 1 <= value <= 7:
                problems.append("%s block %s: %s=%r not 1..7" % (participant, number, key, value))
    if not isinstance(block.get("familiar"), int) or not 1 <= block.get("familiar") <= 3:
        problems.append("%s block %s: familiar invalid" % (participant, number))
    if not isinstance(block.get("fomo"), int) or not 1 <= block.get("fomo") <= 7:
        problems.append("%s block %s: fomo invalid" % (participant, number))
    if not str(block.get("recall") or "").strip():
        problems.append("%s block %s: recall empty" % (participant, number))
    counts = entry["counts"]
    needs_explain = (counts["folded"] + counts["hidden"]) > 0
    if needs_explain:
        if block.get("explain_reason") not in ("能", "不能"):
            problems.append("%s block %s: explain_reason invalid" % (participant, number))
        if not isinstance(block.get("explain_clarity"), int) or not 1 <= block["explain_clarity"] <= 7:
            problems.append("%s block %s: explain_clarity invalid" % (participant, number))
    return problems


def build_session(participant: str, block: dict, expected: dict, counts: dict, latencies: dict[str, list[float]], start: datetime) -> dict:
    condition = expected["condition"]
    duration = float(expected.get("duration_s") or 240)
    ended = start + timedelta(seconds=duration + 10)
    latency = measured_latency(latencies, participant, condition, expected["bvid"])
    events = [
        {
            "t": int(start.timestamp() * 1000),
            "type": "segment",
            "bvid": expected["bvid"],
            "segment_index": 1,
            "shown": counts["shown"],
            "hidden": counts["hidden"],
            "collapsed": counts["folded"],
            "thinned": counts["thinned"],
            "latency_ms": latency,
            "online": condition != "A",
        }
    ]
    for index in range(int(block.get("expanded") or 0)):
        events.append({"t": int((start + timedelta(seconds=5 + index)).timestamp() * 1000), "type": "expand"})
    for index in range(int(block.get("restored") or 0)):
        events.append({"t": int((start + timedelta(seconds=6 + index)).timestamp() * 1000), "type": "restore"})
    for index in range(int(block.get("pref_changes") or 0)):
        events.append({"t": int((start + timedelta(seconds=7 + index)).timestamp() * 1000), "type": "pref"})
    return {
        "schema": "dmf-study-session/1",
        "study_id": expected["study_id"],
        "participant": participant,
        "condition": CONDITION_NAMES[condition],
        "condition_label": condition,
        "started_at": start.isoformat().replace("+00:00", "Z"),
        "ended_at": ended.isoformat().replace("+00:00", "Z"),
        "script_version": "0.4.2-simulated-blind",
        "prefs": {"condition": condition},
        "counts": {
            "segments": 1,
            "shown": counts["shown"],
            "hidden": counts["hidden"],
            "collapsed": counts["folded"],
            "thinned": counts["thinned"],
            "expands": int(block.get("expanded") or 0),
            "restores": int(block.get("restored") or 0),
            "prefs": int(block.get("pref_changes") or 0),
            "corrections": 0,
            "dropped_events": 0,
        },
        "self_report": {
            "expectancy_1": block.get("expectancy_1"),
            "expectancy_2": block.get("expectancy_2"),
            "opened_panel": bool(block.get("opened_panel")),
        },
        "events": events,
        "simulated": True,
        "participant_kind": "llm-agent",
        "persona": block.get("persona"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(AGENTS / "out"))
    parser.add_argument("--report", default=str(REPO / "data" / "study" / "report.json"))
    parser.add_argument("--run-sheet", default=str(RUN_SHEET))
    args = parser.parse_args(argv)

    sheet = load_run_sheet(Path(args.run_sheet))
    index = load_json(AGENTS / "materials-index.json")
    durations = {row["bvid"]: row for row in load_json(Path(args.report))["stimuli"]}
    latencies = load_latency_by_condition()
    RAW.mkdir(parents=True, exist_ok=True)

    rows = []
    problems: list[str] = []
    for participant in PARTICIPANTS:
        out_path = Path(args.out_dir) / (participant + ".json")
        if not out_path.is_file():
            problems.append("%s: missing output %s" % (participant, out_path))
            continue
        payload = load_json(out_path)
        blocks = payload.get("blocks") or []
        entries = {int(entry["block"]): entry for entry in index.get(participant) or []}
        if len(blocks) != 6:
            problems.append("%s: %d blocks, expected 6" % (participant, len(blocks)))
        start = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=45 * (PARTICIPANTS.index(participant)))
        for block in blocks:
            number = int(block.get("block") or 0)
            expected = sheet.get((participant, number))
            entry = entries.get(number)
            if expected is None or entry is None:
                problems.append("%s: unknown block %r" % (participant, block.get("block")))
                continue
            expected = dict(expected)
            expected["block"] = number
            expected["duration_s"] = float(durations[expected["bvid"]].get("duration_s") or 240)
            problems.extend(validate_block(block, expected, entry, participant))
            block["persona"] = payload.get("persona")
            session = build_session(participant, block, expected, entry["counts"], latencies, start + timedelta(minutes=4 * (number - 1)))
            condition = expected["condition"]
            raw_path = RAW / ("dmf-study-%s-%d-%s.json" % (participant, number, condition))
            raw_path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
            row = {
                "participant": participant,
                "condition": condition,
                "video": expected["bvid"],
                "genre": expected["genre"],
                "density": expected["density"],
                "block_order": number,
                # analyze.py treats this column as a binary "seen before" flag,
                # while the questionnaire asks 1=never, 2=clips, 3=full video.
                "familiar": 0 if int(block["familiar"]) == 1 else 1,
                "fomo": block["fomo"],
                "recall": block["recall"],
                "notes": block.get("notes") or "",
            }
            for name, count in SCALES.items():
                for index_number in range(1, count + 1):
                    row["%s_%d" % (name, index_number)] = block.get("%s_%d" % (name, index_number))
            if (entry["counts"]["folded"] + entry["counts"]["hidden"]) > 0:
                row["explain_1"] = 7 if block.get("explain_reason") == "能" else 1
                row["explain_2"] = block.get("explain_clarity")
            else:
                row["explain_1"] = ""
                row["explain_2"] = ""
            rows.append(row)
    rows.sort(key=lambda row: (row["participant"], int(row["block_order"])))
    with CSV_PATH.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"rows": len(rows), "problems": problems[:20], "problem_count": len(problems)}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
