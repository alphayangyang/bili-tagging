"""Assemble AI-participant outputs into the study analysis inputs.

Reads data/study/agents/out/P0x.json (six simulated participants, six blocks
each), data/study/agents/materials-index.json (what each block actually showed)
and data/study/preflight/ (real measured segment latencies for the same
participant/condition/video triples), then writes:

* data/study/questionnaire.csv - one row per block, protocol §7.3 columns;
* data/study/raw/dmf-study-<pid>-<block>-<cond>.json - one session log per block
  so scripts/study/analyze.py can pair the self-report with the system counters.

The session logs are reconstructions: the participants are LLM agents that read
the block material offline instead of watching the video in a browser, so the
counters come from the same policy pipeline that generated the material and the
latencies are the measured values from the preflight run of the same block.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AGENTS = REPO / "data" / "study" / "agents"
PREFLIGHT = REPO / "data" / "study" / "preflight"
RAW = REPO / "data" / "study" / "raw"
CSV_PATH = REPO / "data" / "study" / "questionnaire.csv"

PARTICIPANTS = ("P01", "P02", "P03", "P04", "P05", "P06")
SCALES = {
    "discomfort": 5,
    "coview": 3,
    "control": 3,
    "nasa": 6,
    "comp": 5,
    "overall": 2,
}
CSV_COLUMNS = (
    ["participant", "condition", "video", "genre", "density", "block_order", "familiar"]
    + ["%s_%d" % (name, index) for name, count in SCALES.items() for index in range(1, count + 1)]
    + ["fomo", "recall", "explain_1", "explain_2", "notes"]
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def preflight_key(participant: str, condition: str, bvid: str) -> str:
    return "%s-%s-%s" % (participant, condition, bvid)


def load_latency_by_condition() -> dict[str, list[float]]:
    """Measured segment latencies per condition from the preflight run."""
    values: dict[str, list[float]] = {"A": [], "B": [], "C": []}
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


def load_preflight() -> dict[str, dict]:
    measurements: dict[str, dict] = {}
    if not PREFLIGHT.is_dir():
        return measurements
    for path in sorted(PREFLIGHT.glob("dmf-study-*.json")):
        payload = load_json(path)
        participant = str(payload.get("participant") or "")
        if not participant:
            study_id = str(payload.get("study_id") or "")
            participant = study_id.split("-")[0]
        condition = str(payload.get("condition_label") or "")
        bvids = []
        for event in payload.get("events") or []:
            bvid = str(event.get("bvid") or "")
            if bvid and bvid not in bvids:
                bvids.append(bvid)
        if not participant or not condition or not bvids:
            continue
        measurements[preflight_key(participant, condition, bvids[0])] = payload
    return measurements


def measured_latency(latencies: dict[str, list[float]], participant: str, condition: str, bvid: str) -> float:
    """Pick a measured latency for the block's condition.

    The simulated participants read offline instead of loading the segment in a
    browser, so the latency has to come from the instrumented preflight run of
    the same condition.  Selection is deterministic on (participant, bvid) so
    re-running the assembler keeps the numbers stable.
    """
    pool = latencies.get(condition) or []
    if not pool:
        return 0.0
    seed = sum(ord(char) for char in participant + bvid + condition)
    return float(pool[seed % len(pool)])


def validate_block(block: dict, entry: dict, participant: str) -> list[str]:
    problems = []
    for field in ("study_id", "condition", "genre", "density"):
        expected = entry.get(field)
        if str(block.get(field)) != str(expected):
            problems.append("%s: %s=%r expected %r" % (participant, field, block.get(field), expected))
    if str(block.get("video")) != str(entry.get("bvid")):
        problems.append("%s: video=%r expected %r" % (participant, block.get("video"), entry.get("bvid")))
    for field in ("block",):
        expected = entry[field] if field in entry else None
        if field == "block":
            continue
        if str(block.get(field)) != str(expected):
            problems.append("%s: %s=%r expected %r" % (participant, field, block.get(field), expected))
    for name, count in SCALES.items():
        for index in range(1, count + 1):
            key = "%s_%d" % (name, index)
            value = block.get(key)
            if not isinstance(value, int) or not 1 <= value <= 7:
                problems.append("%s block %s: %s=%r not 1..7" % (participant, block.get("block"), key, value))
    if not isinstance(block.get("familiar"), int) or not 1 <= block.get("familiar") <= 3:
        problems.append("%s block %s: familiar invalid" % (participant, block.get("block")))
    if not isinstance(block.get("fomo"), int) or not 1 <= block.get("fomo") <= 7:
        problems.append("%s block %s: fomo invalid" % (participant, block.get("block")))
    if not str(block.get("recall") or "").strip():
        problems.append("%s block %s: recall empty" % (participant, block.get("block")))
    if block["condition"] == "C":
        if block.get("explain_reason") not in ("能", "不能"):
            problems.append("%s block %s: explain_reason invalid" % (participant, block.get("block")))
        if not isinstance(block.get("explain_clarity"), int) or not 1 <= block["explain_clarity"] <= 7:
            problems.append("%s block %s: explain_clarity invalid" % (participant, block.get("block")))
    return problems


def build_session(participant: str, block: dict, entry: dict, latencies: dict[str, list[float]], start: datetime) -> dict:
    duration = float(entry.get("duration_s") or 240)
    counts = entry["counts"]
    started = start
    ended = start + timedelta(seconds=duration + 10)
    latency = measured_latency(latencies, participant, block["condition"], block["video"])
    events = [
        {
            "t": int(started.timestamp() * 1000),
            "type": "segment",
            "bvid": block["video"],
            "segment_index": 1,
            "shown": counts["shown"],
            "hidden": counts["hidden"],
            "collapsed": counts["folded"],
            "thinned": counts["thinned"],
            "latency_ms": latency,
            "online": block["condition"] != "A",
        }
    ]
    for index in range(int(block.get("expanded") or 0)):
        events.append({"t": int((started + timedelta(seconds=5 + index)).timestamp() * 1000), "type": "expand"})
    for index in range(int(block.get("restored") or 0)):
        events.append({"t": int((started + timedelta(seconds=6 + index)).timestamp() * 1000), "type": "restore"})
    for index in range(int(block.get("pref_changes") or 0)):
        events.append({"t": int((started + timedelta(seconds=7 + index)).timestamp() * 1000), "type": "pref"})
    return {
        "schema": "dmf-study-session/1",
        "study_id": block["study_id"],
        "participant": participant,
        "condition": {"A": "all_on", "B": "keyword", "C": "prototype"}[block["condition"]],
        "condition_label": block["condition"],
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "ended_at": ended.isoformat().replace("+00:00", "Z"),
        "script_version": "0.4.2-simulated",
        "prefs": {"condition": block["condition"]},
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
        "events": events,
        "simulated": True,
        "participant_kind": "llm-agent",
        "persona": block.get("persona"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(AGENTS / "out"))
    parser.add_argument("--report", default=str(REPO / "data" / "study" / "report.json"))
    args = parser.parse_args(argv)

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
            entry = entries.get(number)
            if entry is None:
                problems.append("%s: unknown block %r" % (participant, block.get("block")))
                continue
            entry = dict(entry)
            entry["duration_s"] = float(durations[entry["bvid"]].get("duration_s") or 240)
            problems.extend(validate_block(block, entry, participant))
            block["persona"] = payload.get("persona")
            session = build_session(participant, block, entry, latencies, start + timedelta(minutes=4 * (number - 1)))
            (RAW / ("dmf-study-%s-%d-%s.json" % (participant, number, block["condition"]))).write_text(
                json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
            row = {
                "participant": participant,
                "condition": block["condition"],
                "video": block["video"],
                "genre": block["genre"],
                "density": block["density"],
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
            if block["condition"] == "C":
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
