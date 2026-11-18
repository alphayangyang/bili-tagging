#!/usr/bin/env python3
"""Pre-session checks for the S2 controlled viewing study.

Verifies everything that must be true before a participant walks in: the
local decision service, the six stimuli, their per-video models, the
generated run sheet, and the S1 gold file. Prints one line per check and
exits non-zero if any check fails.

Usage:
    python3 scripts/study/precheck.py                 # includes the service check
    python3 scripts/study/precheck.py --offline       # skip the network check
    python3 scripts/study/precheck.py --url http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIM = "data/study"
MIN_MODELS = 6
EXPECTED_PARTICIPANTS = 30
EXPECTED_BLOCKS = 6
GOLD_LABELS = {
    "information",
    "humor",
    "emotional_resonance",
    "irrelevant_repetition",
    "attack",
    "conflict",
    "spoiler",
    "political",
    "inflammatory",
    "other",
}


def check_health(base_url: str, timeout: float = 3.0) -> dict:
    """Check that the local decision service is up and in linear mode."""
    url = base_url.rstrip("/") + "/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return {"name": "service", "ok": False, "detail": "cannot reach %s: %s" % (url, error)}
    problems = []
    if not payload.get("ok"):
        problems.append("ok is false")
    if payload.get("online_backend") != "linear":
        problems.append("online_backend is %r, expected 'linear'" % payload.get("online_backend"))
    if int(payload.get("llm_pending") or 0) != 0:
        problems.append("llm_pending is %s, expected 0 (teacher queue would make conditions irreproducible)" % payload.get("llm_pending"))
    models = int(payload.get("models") or 0)
    if models < MIN_MODELS:
        problems.append("models is %d, expected >= %d" % (models, MIN_MODELS))
    detail = "online_backend=%s models=%s tag_files=%s" % (
        payload.get("online_backend"),
        models,
        payload.get("tag_files"),
    )
    return {"name": "service", "ok": not problems, "detail": "; ".join(problems) or detail}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {"__error__": str(error)}


def check_stimuli(repo: Path = REPO) -> dict:
    """Check the six stimuli are the real, non-placeholder material."""
    path = repo / DIM / "stimuli.json"
    if not path.is_file():
        return {"name": "stimuli", "ok": False, "detail": "missing %s" % path}
    payload = _load_json(path)
    if "__error__" in payload:
        return {"name": "stimuli", "ok": False, "detail": "bad JSON: %s" % payload["__error__"]}
    rows = payload.get("stimuli") or []
    problems = []
    if payload.get("placeholder"):
        problems.append("stimuli.json is still the placeholder set")
    if len(rows) != EXPECTED_BLOCKS:
        problems.append("has %d stimuli, expected %d" % (len(rows), EXPECTED_BLOCKS))
    genres = sorted({str(row.get("genre")) for row in rows})
    densities = sorted({str(row.get("density")) for row in rows})
    if genres != ["entertainment", "information"]:
        problems.append("genres are %s" % genres)
    if densities != ["high", "low"]:
        problems.append("densities are %s" % densities)
    bvids = [str(row.get("bvid")) for row in rows]
    if len(set(bvids)) != len(bvids):
        problems.append("duplicate bvid in stimuli")
    detail = "%d stimuli (%s)" % (len(rows), ", ".join("%s/%s" % (row.get("genre"), row.get("density")) for row in rows))
    return {"name": "stimuli", "ok": not problems, "detail": "; ".join(problems) or detail}


def check_models(repo: Path = REPO) -> dict:
    """Check that every stimulus has a per-video model under data/study/models."""
    stimuli = _load_json(repo / DIM / "stimuli.json").get("stimuli") or []
    models_dir = repo / DIM / "models"
    missing = []
    sizes = []
    for row in stimuli:
        bvid = str(row.get("bvid") or "")
        path = models_dir / ("%s.json" % bvid)
        if not path.is_file():
            missing.append(bvid)
        else:
            sizes.append(path.stat().st_size)
    problems = []
    if missing:
        problems.append("missing models for %s" % ", ".join(missing))
    if sizes and min(sizes) < 20_000:
        problems.append("smallest model is %d B, looks empty" % min(sizes))
    detail = "%d models, %.0f-%.0f KB" % (len(sizes), min(sizes) / 1024, max(sizes) / 1024) if sizes else "no models"
    return {"name": "models", "ok": not problems, "detail": "; ".join(problems) or detail}


def check_report(repo: Path = REPO) -> dict:
    """Check the generated material report says every stimulus is ready."""
    path = repo / DIM / "report.json"
    if not path.is_file():
        return {"name": "report", "ok": False, "detail": "missing %s" % path}
    payload = _load_json(path)
    if "__error__" in payload:
        return {"name": "report", "ok": False, "detail": "bad JSON: %s" % payload["__error__"]}
    summary = payload.get("summary") or {}
    problems = []
    if not summary.get("ready"):
        problems.append("summary.ready is false")
    insufficient = int(summary.get("insufficient") or 0)
    if insufficient:
        problems.append("%d stimuli are insufficient" % insufficient)
    social = summary.get("social") or {}
    low = [label for label, count in social.items() if label in {"attack", "conflict"} and int(count or 0) < 5]
    if low:
        problems.append("entertainment set lacks hide-class comments: %s" % ", ".join(low))
    detail = "%s ok, evaluated=%s" % (summary.get("ok"), summary.get("evaluated"))
    return {"name": "report", "ok": not problems, "detail": "; ".join(problems) or detail}


def check_run_sheet(repo: Path = REPO) -> dict:
    """Check the run sheet covers every participant, condition, and density cell."""
    path = repo / DIM / "run-sheet.csv"
    if not path.is_file():
        return {"name": "run-sheet", "ok": False, "detail": "missing %s" % path}
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as error:
        return {"name": "run-sheet", "ok": False, "detail": "cannot read: %s" % error}
    problems = []
    per_participant: dict[str, list[dict]] = {}
    for row in rows:
        per_participant.setdefault(row.get("participant", ""), []).append(row)
    if len(per_participant) < EXPECTED_PARTICIPANTS:
        problems.append("only %d participants, expected >= %d" % (len(per_participant), EXPECTED_PARTICIPANTS))
    for participant, block_rows in sorted(per_participant.items()):
        if len(block_rows) != EXPECTED_BLOCKS:
            problems.append("%s has %d blocks" % (participant, len(block_rows)))
            continue
        conditions = sorted({row.get("condition", "") for row in block_rows})
        if conditions != ["A", "B", "C"]:
            problems.append("%s conditions are %s" % (participant, conditions))
            continue
        for condition in conditions:
            count = sum(1 for row in block_rows if row.get("condition") == condition)
            if count != 2:
                problems.append("%s/%s has %d blocks" % (participant, condition, count))
        for row in block_rows:
            url = row.get("url", "")
            for token in ("dmf_study=", "dmf_cond=", "dmf_density="):
                if token not in url:
                    problems.append("%s/%s url lacks %s" % (participant, row.get("bvid"), token))
                    break
    detail = "%d participants, %d rows" % (len(per_participant), len(rows))
    return {"name": "run-sheet", "ok": not problems, "detail": "; ".join(problems[:5]) or detail}


def check_gold(repo: Path = REPO) -> dict:
    """Check the S1 gold file is usable for the RQ2 per-tag evaluation."""
    path = repo / DIM / "gold.jsonl"
    if not path.is_file():
        return {"name": "gold", "ok": False, "detail": "missing %s" % path}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            return {"name": "gold", "ok": False, "detail": "bad JSON line in %s" % path}
    problems = []
    if len(rows) < 100:
        problems.append("only %d rows, expected >= 100" % len(rows))
    bad = [row for row in rows if str(row.get("label")) not in GOLD_LABELS or not str(row.get("text") or "").strip()]
    if bad:
        problems.append("%d rows have an unknown or missing label" % len(bad))
    annotators = sorted({str(row.get("annotator")) for row in rows if row.get("annotator")})
    detail = "%d rows, annotators=%s%s" % (
        len(rows),
        ",".join(annotators) or "unknown",
        " (single annotator: report Krippendorff alpha as unavailable)" if len(annotators) <= 1 else "",
    )
    return {"name": "gold", "ok": not problems, "detail": "; ".join(problems) or detail}


def check_version(repo: Path = REPO) -> dict:
    """Check the built userscript is present and its version matches package.json."""
    dist = repo / "extension" / "dist" / "bili-danmaku-filter.user.js"
    package = _load_json(repo / "extension" / "package.json")
    expected = str(package.get("version") or "")
    if not dist.is_file():
        return {"name": "script", "ok": False, "detail": "missing %s" % dist}
    text = dist.read_text(encoding="utf-8", errors="ignore")
    marker = "@version"
    actual = ""
    for line in text.splitlines()[:40]:
        if marker in line:
            actual = line.split(marker)[-1].strip()
            break
    problems = []
    if not actual:
        problems.append("no @version header in dist")
    elif expected and actual != expected:
        problems.append("dist is %s but package.json is %s (rebuild with node extension/build.mjs)" % (actual, expected))
    detail = "dist v%s (package %s)" % (actual or "?", expected or "?")
    return {"name": "script", "ok": not problems, "detail": "; ".join(problems) or detail}


def run_checks(base_url: str, offline: bool = False, repo: Path = REPO) -> list[dict]:
    """Run every pre-session check and return the results in display order."""
    results = []
    if offline:
        results.append({"name": "service", "ok": True, "detail": "skipped (--offline)"})
    else:
        results.append(check_health(base_url))
    results.extend([
        check_stimuli(repo),
        check_models(repo),
        check_report(repo),
        check_run_sheet(repo),
        check_gold(repo),
        check_version(repo),
    ])
    return results


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the checks, print a table, and return an exit code."""
    parser = argparse.ArgumentParser(description="Pre-session checks for the S2 study")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="local decision service base URL")
    parser.add_argument("--offline", action="store_true", help="skip the service check")
    args = parser.parse_args(argv)
    results = run_checks(args.url, offline=args.offline)
    for result in results:
        print("%-10s %s  %s" % (result["name"], "PASS" if result["ok"] else "FAIL", result["detail"]))
    failed = [result for result in results if not result["ok"]]
    print("")
    if failed:
        print("%d/%d checks failed" % (len(failed), len(results)))
        return 1
    print("all %d checks passed" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())