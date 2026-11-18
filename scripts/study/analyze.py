"""Analyse the controlled-viewing study data (protocol §7.2, §7.3, §8).

Reads the browser session logs written by study mode and the exported
questionnaire CSV, then produces data/study/analysis.json plus a readable
data/study/analysis.md:

* cleaning: participant / block exclusions (dwell under 60% of the video time,
  self-reported familiarity with 4+ stimuli, missing session logs), marked and
  counted rather than silently dropped;
* scale means and Cronbach alpha per condition;
* planned within-subject contrasts C vs A, C vs B, B vs A with paired Wilcoxon
  signed-rank tests (normal approximation with tie correction), Holm correction
  over the three contrasts, Cohen's d and a participant-level bootstrap 95% CI;
* optional linear mixed model score ~ condition + (1|participant) + (1|video)
  through statsmodels when it is installed, a documented note otherwise;
* RQ1 descriptive genre x density table for condition A (exploratory, video
  level);
* RQ2 latency p50/p95 from the segment events, decision counts, correction
  rates, optional per-tag precision/recall/F1 against an S1 gold file, and
  nominal Krippendorff alpha when a double-annotation file is supplied.

Usage::

    python3 scripts/study/analyze.py
    python3 scripts/study/analyze.py --questionnaire data/study/questionnaire.csv
    python3 scripts/study/analyze.py --gold data/study/gold.jsonl --bootstrap 5000

The script is stdlib only; the mixed model is the one optional dependency and it
degrades to a documented note. Missing inputs never raise: the report records
what was unavailable and exits 0.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_RAW_DIR = REPO / "data" / "study" / "raw"
DEFAULT_QUESTIONNAIRE = REPO / "data" / "study" / "questionnaire.csv"
DEFAULT_REPORT = REPO / "data" / "study" / "analysis.json"
DEFAULT_MARKDOWN = REPO / "data" / "study" / "analysis.md"
DEFAULT_GOLD = REPO / "data" / "study" / "gold.jsonl"
DEFAULT_GOLD_DOUBLE = REPO / "data" / "study" / "gold_double.jsonl"
DEFAULT_MODELS = REPO / "data" / "study" / "models"
DEFAULT_STUDY_REPORT = REPO / "data" / "study" / "report.json"

CONDITIONS = ("A", "B", "C")
# The study script stores the canonical condition ids; the protocol and the
# questionnaire use the A/B/C letters. Accept both so a raw export can be
# analysed without hand editing.
CONDITION_ALIASES = {
    "ALL_ON": "A",
    "ALL-ON": "A",
    "KEYWORD": "B",
    "PROTOTYPE": "C",
}


def _normalise_condition(value: object) -> str:
    """Map a canonical condition id or an A/B/C letter to A/B/C ("" when unknown)."""
    text = str(value or "").strip().upper().replace(" ", "_")
    text = CONDITION_ALIASES.get(text, text)
    return text if text in CONDITIONS else ""

SCALES = {
    "discomfort": ("discomfort", 5),
    "coview": ("coview", 3),
    "control": ("control", 3),
    "nasa": ("nasa", 6),
    "comp": ("comp", 5),
    "explain": ("explain", 2),
    "overall": ("overall", 2),
}
SINGLE_ITEM_OUTCOMES = ("fomo",)
OUTCOMES = tuple(SCALES) + SINGLE_ITEM_OUTCOMES
PRIMARY_OUTCOMES = ("discomfort", "control")
SECONDARY_OUTCOMES = ("coview", "fomo", "nasa", "comp", "overall", "explain")
PLANNED_CONTRASTS = (("C", "A"), ("C", "B"), ("B", "A"))
MIN_DWELL_RATIO = 0.6
FAMILIAR_MAX = 4
MAX_BOOTSTRAP = 5000
SEED = 20261105

# Column aliases tolerated in the exported questionnaire CSV (protocol §7.3).
COLUMN_ALIASES = {
    "participant": ("participant", "participant_id", "pid", "subject", "subject_id", "id"),
    "condition": ("condition", "cond", "condition_code"),
    "video": ("video", "bvid", "stimulus"),
    "genre": ("genre", "video_genre", "type"),
    "density": ("density", "video_density", "density_condition"),
    "block_order": ("block_order", "block_order_index", "block", "order", "sequence"),
    "familiar": ("familiar", "familiarity", "seen", "seen_before", "know"),
    "fomo": ("fomo", "fomo_1", "fomo_score"),
    "recall": ("recall", "free_recall", "recall_text"),
    "notes": ("notes", "note", "comment", "comments"),
}
TRUTHY = ("1", "yes", "y", "true", "是", "看过", "有")
FALSY = ("0", "no", "n", "false", "否", "没看过", "没有")


def _write_json(path: str | Path, payload: object) -> None:
    """Write JSON through a temp file + os.replace so readers see a complete file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_text(path: str | Path, text: str) -> None:
    """Write text through a temp file + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _number(value: object) -> float | None:
    """Parse a numeric cell; blank and NA markers become None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"na", "n/a", "nan", "none", "-", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _truthy(value: object) -> bool | None:
    """Normalise the familiar column into True / False / None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    number = _number(text)
    if number is not None:
        return number > 0
    return None


def _pick(row: dict, aliases: tuple[str, ...]) -> object | None:
    """Return the first present alias value of a canonical column."""
    for alias in aliases:
        if alias in row and str(row[alias]).strip() != "":
            return row[alias]
    return None


def _canonicalise(row: dict) -> dict:
    """Map a raw CSV row onto the canonical questionnaire columns."""
    lower = {str(key).strip().lower(): value for key, value in row.items() if key is not None}
    canonical: dict = {}
    for name, aliases in COLUMN_ALIASES.items():
        canonical[name] = _pick(lower, aliases)
    for name, (prefix, count) in SCALES.items():
        items = []
        for index in range(1, count + 1):
            value = lower.get("%s_%d" % (prefix, index))
            if value is None:
                value = lower.get("%s%d" % (prefix, index))
            items.append(_number(value))
        canonical["%s_items" % name] = items
    canonical["recall"] = canonical.get("recall")
    return canonical


def load_questionnaire(path: str | Path = DEFAULT_QUESTIONNAIRE) -> tuple[list[dict], list[str]]:
    """Read the questionnaire CSV; returns (rows, problems)."""
    target = Path(path)
    problems: list[str] = []
    if not target.is_file():
        return [], ["questionnaire CSV not found: %s" % target]
    rows: list[dict] = []
    try:
        with target.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                return [], ["questionnaire CSV has no header: %s" % target]
            for line_index, raw in enumerate(reader, start=2):
                canonical = _canonicalise(raw)
                participant = str(canonical.get("participant") or "").strip()
                condition = _normalise_condition(canonical.get("condition"))
                video = str(canonical.get("video") or "").strip()
                if not participant or not condition:
                    problems.append("row %d skipped: missing participant or condition" % line_index)
                    continue
                if condition not in CONDITIONS:
                    problems.append("row %d skipped: unknown condition %r" % (line_index, condition))
                    continue
                canonical.update({"participant": participant, "condition": condition, "video": video})
                canonical["outcomes"] = _outcomes_from_row(canonical)
                rows.append(canonical)
    except (OSError, csv.Error) as error:
        return rows, problems + ["failed to read questionnaire CSV: %s" % error]
    return rows, problems


def _outcomes_from_row(row: dict) -> dict:
    """Mean scale scores (items may be partially missing) and single items."""
    outcomes: dict[str, float | None] = {}
    for name, (_prefix, count) in SCALES.items():
        items = [value for value in (row.get("%s_items" % name) or []) if value is not None]
        if items and len(items) >= max(1, count // 2):
            outcomes[name] = sum(items) / len(items)
        else:
            outcomes[name] = None
    outcomes["fomo"] = _number(row.get("fomo"))
    return outcomes


def _parse_time(value: object) -> datetime | None:
    """Parse the ISO timestamps the study script writes (Z suffix tolerated)."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(str(value).strip(), "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _participant_from_name(path: Path, payload: dict) -> str:
    """Recover the participant id from the payload, study_id, or filename."""
    participant = str(payload.get("participant") or "").strip()
    if participant:
        return participant
    study_id = str(payload.get("study_id") or "").strip()
    if study_id:
        return study_id.split("-")[0]
    stem = path.stem
    if stem.startswith("dmf-study-"):
        parts = stem[len("dmf-study-") :].split("-")
        if parts and parts[0]:
            return parts[0]
    return path.parent.name if path.parent != path else ""


def _condition_from_name(path: Path, payload: dict) -> str:
    condition = _normalise_condition(payload.get("condition_label")) or _normalise_condition(
        payload.get("condition")
    )
    if condition:
        return condition
    study_id = str(payload.get("study_id") or "").strip().upper()
    if study_id.endswith(("-A", "-B", "-C")):
        return _normalise_condition(study_id[-1])
    stem = path.stem.upper()
    for candidate in CONDITIONS:
        if stem.endswith("-" + candidate) or stem.endswith("_" + candidate):
            return candidate
    return ""


def load_sessions(raw_dir: str | Path = DEFAULT_RAW_DIR) -> tuple[list[dict], list[str]]:
    """Read every session log under raw_dir; returns (sessions, problems)."""
    root = Path(raw_dir)
    problems: list[str] = []
    if not root.is_dir():
        return [], ["raw session directory not found: %s" % root]
    paths = sorted(root.rglob("dmf-study-*.json"))
    if not paths:
        paths = sorted(root.rglob("*.json"))
    sessions: list[dict] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            problems.append("unreadable session log %s: %s" % (path, error))
            continue
        if not isinstance(payload, dict):
            problems.append("session log is not an object: %s" % path)
            continue
        participant = _participant_from_name(path, payload)
        condition = _condition_from_name(path, payload)
        if not participant or condition not in CONDITIONS:
            problems.append("session log %s skipped: participant=%r condition=%r" % (path, participant, condition))
            continue
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        sessions.append(_summarise_session(path, payload, events, participant, condition))
    return sessions, problems


def _summarise_session(path: Path, payload: dict, events: list, participant: str, condition: str) -> dict:
    """Extract the counters, latencies, bvids and wall time from one session log."""
    type_counts: Counter = Counter()
    latencies: list[float] = []
    bvids: list[str] = []
    online_segments = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        type_counts[event_type] += 1
        if event_type == "segment":
            latency = _number(event.get("latency_ms"))
            if latency is not None and latency >= 0:
                latencies.append(latency)
            bvid = str(event.get("bvid") or "").strip()
            if bvid and bvid not in bvids:
                bvids.append(bvid)
            if _number(event.get("online")) not in (None, 0.0):
                online_segments += 1
    started = _parse_time(payload.get("started_at"))
    ended = _parse_time(payload.get("ended_at"))
    wall_seconds = round((ended - started).total_seconds(), 1) if started and ended else None
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    return {
        "path": str(path),
        "participant": participant,
        "condition": condition,
        "started_at": payload.get("started_at"),
        "ended_at": payload.get("ended_at"),
        "wall_seconds": wall_seconds,
        "prefs": payload.get("prefs") if isinstance(payload.get("prefs"), dict) else {},
        "counts": counts,
        "events": len(events),
        "type_counts": dict(type_counts),
        "latency_ms": latencies,
        "bvids": bvids,
        "online_segments": online_segments,
        "truncated": len(events) >= 5000,
    }


def load_video_durations(path: str | Path = DEFAULT_STUDY_REPORT) -> dict[str, float]:
    """Read bvid -> duration_s from the prepare_stimuli report when it exists."""
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    durations: dict[str, float] = {}
    for entry in (payload.get("stimuli") or []) if isinstance(payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        bvid = str(entry.get("bvid") or "")
        duration = _number(entry.get("duration_s"))
        if bvid and duration:
            durations[bvid] = duration
    return durations


def clean_data(
    questionnaire: list[dict],
    sessions: list[dict],
    durations: dict[str, float],
) -> tuple[list[dict], dict]:
    """Apply the protocol §8.1 exclusions and return the analysable rows."""
    sessions_by_key = {(session["participant"], session["condition"]): session for session in sessions}
    explanations: list[str] = []
    excluded: list[dict] = []
    included: list[dict] = []
    familiar_counts: Counter = Counter()
    for row in questionnaire:
        if _truthy(row.get("familiar")) is True:
            familiar_counts[row["participant"]] += 1
    familiar_drop = {key for key, count in familiar_counts.items() if count >= FAMILIAR_MAX}

    for row in questionnaire:
        participant, condition = row["participant"], row["condition"]
        session = sessions_by_key.get((participant, condition))
        reasons: list[str] = []
        if participant in familiar_drop:
            reasons.append("self-reported familiarity with %d stimuli (>= %d)" % (familiar_counts[participant], FAMILIAR_MAX))
        if session is None:
            explanations.append(
                "no session log for %s/%s: self-report used for behaviour fields" % (participant, condition)
            )
        elif session.get("wall_seconds") and durations:
            expected = sum(durations.get(bvid, 0.0) for bvid in session.get("bvids") or [])
            if expected > 0 and session["wall_seconds"] < MIN_DWELL_RATIO * expected:
                reasons.append(
                    "dwell %.0fs < %.0f%% of the %.0fs video time" % (
                        session["wall_seconds"], MIN_DWELL_RATIO * 100, expected
                    )
                )
        if not any(value is not None for value in row["outcomes"].values()):
            reasons.append("no scale responses")
        if reasons:
            excluded.append(
                {
                    "participant": participant,
                    "condition": condition,
                    "video": row.get("video") or "",
                    "reasons": reasons,
                }
            )
        else:
            row["session"] = session
            included.append(row)

    blocks_by_condition: Counter = Counter(row["condition"] for row in included)
    participants = sorted({row["participant"] for row in included})
    cleaning = {
        "questionnaire_rows": len(questionnaire),
        "session_logs": len(sessions),
        "included_rows": len(included),
        "excluded_rows": len(excluded),
        "included_participants": len(participants),
        "participants": participants,
        "excluded": excluded,
        "excluded_participants": sorted(familiar_drop),
        "rows_by_condition": {condition: blocks_by_condition.get(condition, 0) for condition in CONDITIONS},
        "notes": explanations,
    }
    return included, cleaning


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sd(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; returns 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _participant_condition_scores(rows: list[dict], outcome: str) -> dict[str, dict[str, float]]:
    """Mean score per participant x condition (participants with a condition need >=1 block)."""
    collected: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        value = (row.get("outcomes") or {}).get(outcome)
        if value is None:
            continue
        collected.setdefault(row["participant"], {}).setdefault(row["condition"], []).append(value)
    return {
        participant: {condition: sum(values) / len(values) for condition, values in by_condition.items()}
        for participant, by_condition in collected.items()
    }


def descriptives(rows: list[dict]) -> dict:
    """Per-condition and per-video outcome summaries."""
    by_condition: dict[str, dict] = {}
    for condition in CONDITIONS:
        subset = [row for row in rows if row["condition"] == condition]
        by_condition[condition] = {
            "n_blocks": len(subset),
            "n_participants": len({row["participant"] for row in subset}),
            "outcomes": {
                outcome: _summary_of([(row.get("outcomes") or {}).get(outcome) for row in subset])
                for outcome in OUTCOMES
            },
        }
    by_video: dict[str, dict] = {}
    for row in rows:
        key = row.get("video") or "unknown"
        entry = by_video.setdefault(key, {"n_blocks": 0, "conditions": Counter(), "genres": Counter(), "densities": Counter()})
        entry["n_blocks"] += 1
        entry["conditions"][row["condition"]] += 1
        if row.get("genre"):
            entry["genres"][str(row["genre"]).strip()] += 1
        if row.get("density"):
            entry["densities"][str(row["density"]).strip()] += 1
    for entry in by_video.values():
        entry["conditions"] = dict(entry["conditions"])
        entry["genres"] = dict(entry["genres"])
        entry["densities"] = dict(entry["densities"])
    return {"by_condition": by_condition, "by_video": by_video}


def _summary_of(values: list[float | None]) -> dict:
    usable = [value for value in values if value is not None]
    return {
        "n": len(usable),
        "mean": round(_mean(usable), 3) if usable else None,
        "sd": round(_sd(usable), 3) if _sd(usable) is not None else None,
        "se": round(_sd(usable) / math.sqrt(len(usable)), 3) if len(usable) > 1 else None,
        "median": _median(usable),
        "min": min(usable) if usable else None,
        "max": max(usable) if usable else None,
    }


def cronbach_alpha(item_rows: list[list[float | None]]) -> float | None:
    """Cronbach alpha from complete item rows (None when undefined)."""
    complete = [row for row in item_rows if all(value is not None for value in row)]
    if len(complete) < 3:
        return None
    items = len(complete[0])
    if items < 2:
        return None
    item_variances = []
    for index in range(items):
        values = [row[index] for row in complete]
        variance = _sd([float(value) for value in values])
        if variance is None:
            return None
        item_variances.append(variance ** 2)
    totals = [sum(float(value) for value in row) for row in complete]
    total_sd = _sd(totals)
    if not total_sd:
        return None
    alpha = items / (items - 1) * (1.0 - sum(item_variances) / (total_sd ** 2))
    return round(alpha, 3)


def reliability(rows: list[dict]) -> dict:
    """Cronbach alpha per scale and condition (only scales with >= 2 items)."""
    result: dict[str, dict] = {}
    for condition in CONDITIONS:
        subset = [row for row in rows if row["condition"] == condition]
        condition_result: dict[str, float | None] = {}
        for name, (_prefix, count) in SCALES.items():
            if count < 2:
                continue
            condition_result[name] = cronbach_alpha([row.get("%s_items" % name) or [] for row in subset])
        result[condition] = condition_result
    return result


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, ties sharing the mean rank."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average = (index + end + 2) / 2.0
        for position in range(index, end + 1):
            ranks[order[position]] = average
        index = end + 1
    return ranks


def wilcoxon_signed_rank(differences: list[float]) -> dict:
    """Two-sided paired Wilcoxon test via normal approximation with ties correction."""
    nonzero = [value for value in differences if value != 0.0]
    n = len(nonzero)
    if n == 0:
        return {"n": 0, "statistic": 0.0, "z": 0.0, "p": 1.0, "note": "no non-zero differences"}
    absolute = [abs(value) for value in nonzero]
    ranks = _ranks(absolute)
    w_plus = sum(rank for rank, value in zip(ranks, nonzero) if value > 0)
    w_minus = sum(rank for rank, value in zip(ranks, nonzero) if value < 0)
    tie_counts = Counter(round(value, 12) for value in absolute)
    tie_term = sum(count ** 3 - count for count in tie_counts.values())
    mean = n * (n + 1) / 4.0
    variance = n * (n + 1) * (2 * n + 1) / 24.0 - tie_term / 48.0
    if variance <= 0:
        z = 0.0
        p_value = 1.0
    else:
        z = (w_plus - mean) / math.sqrt(variance)
        p_value = math.erfc(abs(z) / math.sqrt(2.0))
    return {
        "n": n,
        "statistic": min(w_plus, w_minus),
        "w_plus": w_plus,
        "z": round(z, 3),
        "p": round(min(1.0, p_value), 4),
    }


def holm(p_values: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order."""
    count = len(p_values)
    order = sorted(range(count), key=lambda index: p_values[index])
    adjusted = [0.0] * count
    running = 0.0
    for rank, index in enumerate(order):
        value = min(1.0, p_values[index] * (count - rank))
        running = max(running, value)
        adjusted[index] = round(running, 4)
    return adjusted


def cohens_d(differences: list[float]) -> float | None:
    """Paired Cohen's d (mean difference over SD of the differences)."""
    if len(differences) < 2:
        return None
    mean = _mean(differences)
    sd = _sd(differences)
    if not sd:
        return None
    return round(mean / sd, 3)


def bootstrap_ci(
    values: list[float], samples: int = MAX_BOOTSTRAP, seed: int = SEED, alpha: float = 0.05
) -> list[float] | None:
    """Participant-level percentile bootstrap CI for the mean."""
    if len(values) < 2 or samples <= 0:
        return None
    rng = random.Random(seed)
    count = len(values)
    means = []
    for _ in range(samples):
        means.append(sum(values[rng.randrange(count)] for _ in range(count)) / count)
    return [round(_percentile(means, alpha / 2.0), 3), round(_percentile(means, 1 - alpha / 2.0), 3)]


def planned_contrasts(rows: list[dict], samples: int = MAX_BOOTSTRAP) -> dict:
    """Within-subject planned contrasts per outcome with Holm correction."""
    result: dict[str, list[dict]] = {}
    for outcome in OUTCOMES:
        scores = _participant_condition_scores(rows, outcome)
        entries = []
        for left, right in PLANNED_CONTRASTS:
            pairs = [
                (by_condition[left], by_condition[right])
                for by_condition in scores.values()
                if left in by_condition and right in by_condition
            ]
            differences = [first - second for first, second in pairs]
            if len(differences) < 3:
                entries.append(
                    {
                        "contrast": "%s vs %s" % (left, right),
                        "n": len(differences),
                        "note": "too few complete pairs",
                    }
                )
                continue
            test = wilcoxon_signed_rank(differences)
            entries.append(
                {
                    "contrast": "%s vs %s" % (left, right),
                    "n": len(differences),
                    "mean_diff": round(_mean(differences), 3),
                    "ci95": bootstrap_ci(differences, samples),
                    "d": cohens_d(differences),
                    "wilcoxon": test,
                }
            )
        pending = [(index, entry["wilcoxon"]["p"]) for index, entry in enumerate(entries) if "wilcoxon" in entry]
        if pending:
            adjusted = holm([value for _index, value in pending])
            for (index, _value), value in zip(pending, adjusted):
                entries[index]["p_holm"] = value
        result[outcome] = entries
    return result


def try_lmm(rows: list[dict]) -> dict:
    """Fit score ~ condition + (1|participant) + (1|video) when statsmodels exists."""
    try:
        import pandas as pd  # type: ignore
        import statsmodels.formula.api as smf  # type: ignore
    except Exception as error:  # noqa: BLE001 - optional dependency
        return {
            "available": False,
            "note": "statsmodels/pandas not installed (%s); use the planned Wilcoxon contrasts" % type(error).__name__,
        }
    records = []
    for row in rows:
        for outcome in ("discomfort", "control"):
            value = (row.get("outcomes") or {}).get(outcome)
            if value is None:
                continue
            records.append(
                {
                    "score": float(value),
                    "outcome": outcome,
                    "condition": row["condition"],
                    "participant": row["participant"],
                    "video": row.get("video") or "unknown",
                }
            )
    if not records:
        return {"available": True, "models": {}, "note": "no complete discomfort/control rows"}
    frame = pd.DataFrame(records)
    models = {}
    for outcome in ("discomfort", "control"):
        subset = frame[frame["outcome"] == outcome]
        try:
            model = smf.mixedlm(
                "score ~ C(condition)",
                subset,
                groups=subset["participant"],
                vc_formula={"video": "0 + C(video)"},
            )
            fitted = model.fit()
            models[outcome] = {
                "params": {str(key): round(float(value), 4) for key, value in fitted.params.items()},
                "pvalues": {str(key): round(float(value), 4) for key, value in fitted.pvalues.items()},
            }
        except Exception as error:  # noqa: BLE001 - the model is a bonus, not a gate
            models[outcome] = {"error": "%s: %s" % (type(error).__name__, error)}
    return {"available": True, "models": models}


def _rounded_mean(values: list[float | None]) -> float | None:
    """Mean of the non-missing values, rounded, or None when nothing is left."""
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 3)


def rq1_video_level(rows: list[dict]) -> dict:
    """Exploratory genre x density descriptive table for condition A."""
    subset = [row for row in rows if row["condition"] == "A"]
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in subset:
        genre = str(row.get("genre") or "unknown").strip()
        density = str(row.get("density") or "unknown").strip()
        groups.setdefault((genre, density), []).append(row)
    table = []
    for (genre, density), group in sorted(groups.items()):
        table.append(
            {
                "genre": genre,
                "density": density,
                "n_blocks": len(group),
                "n_videos": len({row.get("video") for row in group}),
                "means": {
                    outcome: _rounded_mean([row["outcomes"][outcome] for row in group])
                    for outcome in ("discomfort", "coview", "control", "fomo")
                },
            }
        )
    return {
        "note": "exploratory: participants see only two videos per condition, genre x density is video-level",
        "rows": table,
        "n_blocks": len(subset),
    }


def _latency_stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}
    return {
        "n": len(values),
        "p50_ms": round(_percentile(values, 0.5), 2),
        "p95_ms": round(_percentile(values, 0.95), 2),
        "mean_ms": round(sum(values) / len(values), 2),
    }


def rq2_from_sessions(sessions: list[dict]) -> dict:
    """Latency, decision counts and behaviour rates from the session logs."""
    latency_by_condition: dict[str, list[float]] = {condition: [] for condition in CONDITIONS}
    overall_latency: list[float] = []
    decisions: dict[str, Counter] = {condition: Counter() for condition in CONDITIONS}
    behaviour = Counter()
    online_segments = 0
    total_segments = 0
    wall_seconds = 0.0
    truncated = 0
    for session in sessions:
        condition = session["condition"]
        latency_by_condition[condition].extend(session.get("latency_ms") or [])
        overall_latency.extend(session.get("latency_ms") or [])
        counts = session.get("counts") or {}
        for key in ("shown", "hidden", "collapsed", "thinned"):
            value = _number(counts.get(key))
            if value is not None:
                decisions[condition][key] += int(value)
        # Record-only conditions (A/B) never touch the stream; their applied
        # counts are the whole segment and the prototype's policy result lives
        # in would_* so the behaviour table stays comparable across conditions.
        for key in ("would_shown", "would_hidden", "would_collapsed"):
            value = _number(counts.get(key))
            if value is not None:
                decisions[condition][key] += int(value)
        for label, count in (counts.get("by_label") or {}).items():
            value = _number(count)
            if value is not None:
                decisions[condition]["label:%s" % label] += int(value)
        type_counts = session.get("type_counts") or {}
        behaviour["corrections"] += int(type_counts.get("correction", 0))
        behaviour["restores"] += int(type_counts.get("restore", 0))
        behaviour["expands"] += int(type_counts.get("expand", 0))
        behaviour["pref_changes"] += int(type_counts.get("pref", 0))
        behaviour["segments"] += int(type_counts.get("segment", 0))
        online_segments += int(session.get("online_segments") or 0)
        total_segments += int(session.get("type_counts", {}).get("segment", 0))
        wall_seconds += float(session.get("wall_seconds") or 0.0)
        if session.get("truncated"):
            truncated += 1
    minutes = wall_seconds / 60.0
    behaviour["corrections_per_10min"] = (
        round(behaviour["corrections"] / minutes * 10.0, 2) if minutes > 0 else None
    )
    return {
        "latency": {condition: _latency_stats(latency_by_condition[condition]) for condition in CONDITIONS},
        "latency_overall": _latency_stats(overall_latency),
        "decisions": {condition: dict(decisions[condition]) for condition in CONDITIONS},
        "behaviour": dict(behaviour),
        "online_share": round(online_segments / total_segments, 3) if total_segments else None,
        "truncated_sessions": truncated,
        "session_minutes": round(minutes, 2) if minutes else 0.0,
    }


def _load_gold_rows(path: str | Path) -> list[dict]:
    """Read a gold JSONL file into rows; blank lines and bad JSON are skipped."""
    target = Path(path)
    rows: list[dict] = []
    if not target.is_file():
        return rows
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def evaluate_gold(path: str | Path, models_dir: str | Path) -> dict:
    """Top-1 per-tag precision/recall/F1 against an optional S1 gold file."""
    rows = _load_gold_rows(path)
    if not rows:
        return {"available": False, "note": "gold file missing or empty: %s" % path}
    try:
        from danmaku_filter.classify.linear import LinearTagger
    except Exception as error:  # noqa: BLE001 - optional import
        return {"available": False, "note": "linear classifier unavailable: %s" % error}

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        text = str(row.get("text") or "").strip()
        label = str(row.get("label") or row.get("gold") or row.get("dominant") or "").strip()
        if not text or not label:
            continue
        grouped.setdefault(str(row.get("bvid") or ""), []).append(
            {"text": text, "label": label, "score": _number(row.get("score"))}
        )
    if not grouped:
        return {"available": False, "note": "gold file has no usable text/label rows: %s" % path}

    predictions: list[tuple[str, str]] = []
    relevance: list[tuple[float, float]] = []
    skipped = 0
    models: dict[str, object] = {}
    for bvid, group in grouped.items():
        model_path = Path(models_dir) / ("%s.json" % bvid) if bvid else None
        if model_path is None or not model_path.is_file():
            global_path = Path(models_dir) / "_global.json"
            if bvid and Path(models_dir).is_dir() and not global_path.is_file():
                skipped += len(group)
                continue
            model_path = global_path if global_path.is_file() else None
        if model_path is None:
            skipped += len(group)
            continue
        key = str(model_path)
        if key not in models:
            try:
                models[key] = LinearTagger.load(model_path)
            except Exception:  # noqa: BLE001 - a broken model must not kill the report
                models[key] = None
        model = models[key]
        if model is None:
            skipped += len(group)
            continue
        texts = [item["text"] for item in group]
        labels = model.predict(texts)
        predictions.extend(zip(labels, [item["label"] for item in group]))
        score_lists = model.predict_scores(texts)
        for item, scores in zip(group, score_lists):
            if item["score"] is not None:
                relevance.append((float(scores.get(item["label"], 0.0)), item["score"]))

    if not predictions:
        return {"available": False, "note": "no model matched the gold rows (skipped %d rows)" % skipped}

    labels = sorted({label for pair in predictions for label in pair})
    per_tag: dict[str, dict] = {}
    f1_values = []
    for label in labels:
        true_positive = sum(1 for predicted, gold in predictions if predicted == label and gold == label)
        predicted_positive = sum(1 for predicted, _gold in predictions if predicted == label)
        actual_positive = sum(1 for _predicted, gold in predictions if gold == label)
        precision = true_positive / predicted_positive if predicted_positive else 0.0
        recall = true_positive / actual_positive if actual_positive else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_tag[label] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "support": actual_positive,
        }
        f1_values.append(f1)
    accuracy = sum(1 for predicted, gold in predictions if predicted == gold) / len(predictions)
    result = {
        "available": True,
        "n": len(predictions),
        "skipped_rows": skipped,
        "accuracy": round(accuracy, 3),
        "macro_f1": round(sum(f1_values) / len(f1_values), 3) if f1_values else None,
        "macro_f1_note": (
            "averaged over the union of predicted and gold labels; a label predicted but absent "
            "from the gold set counts with support 0"
        ),
        "per_tag": dict(sorted(per_tag.items(), key=lambda pair: -pair[1]["support"])),
        "source": str(path),
    }
    if relevance:
        result["relevance_mae"] = round(
            sum(abs(predicted - gold) for predicted, gold in relevance) / len(relevance), 3
        )
    else:
        result["relevance_note"] = "gold rows carry no per-label score: relevance MAE skipped"
    return result


def krippendorff_alpha_nominal(pairs: list[tuple[str, str]]) -> float | None:
    """Nominal Krippendorff alpha for two coders over the same units.

    Expected disagreement uses the finite-sample form N(N-1) (Krippendorff 2011).
    Treating the marginals as population proportions biases alpha downwards, which
    matters for the 120-row double-annotation check.
    """
    pairs = [(str(first), str(second)) for first, second in pairs if first and second]
    if len(pairs) < 2:
        return None
    disagreements = sum(1 for first, second in pairs if first != second)
    observed = disagreements / len(pairs)
    marginal: Counter = Counter()
    for first, second in pairs:
        marginal[first] += 1
        marginal[second] += 1
    total = sum(marginal.values())
    if total < 2:
        return None
    expected = (total * total - sum(count * count for count in marginal.values())) / (
        total * (total - 1)
    )
    if expected <= 0:
        return None
    return round(1.0 - observed / expected, 3)


def evaluate_double_annotation(path: str | Path) -> dict:
    """Optional double-annotation agreement: rows with label_a / label_b."""
    rows = _load_gold_rows(path)
    if not rows:
        return {"available": False, "note": "double-annotation file missing or empty: %s" % path}
    pairs = []
    for row in rows:
        first = str(row.get("label_a") or row.get("annotator_a") or "").strip()
        second = str(row.get("label_b") or row.get("annotator_b") or "").strip()
        if first and second:
            pairs.append((first, second))
    alpha = krippendorff_alpha_nominal(pairs)
    if alpha is None:
        return {
            "available": False,
            "note": "Krippendorff alpha undefined (needs >= 2 pairs and > 1 observed label): %s" % path,
        }
    agreements = sum(1 for first, second in pairs if first == second)
    return {
        "available": True,
        "n": len(pairs),
        "agreement": round(agreements / len(pairs), 3),
        "krippendorff_alpha": alpha,
        "source": str(path),
    }


def _fmt(value: object, digits: int = 2) -> str:
    """Format numbers for the markdown tables without raising on None."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return ("%." + str(digits) + "f") % value
    return str(value)


def _table(headers: list[str], rows: list[list[object]]) -> str:
    """Render a GitHub-flavoured markdown table."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_markdown(analysis: dict) -> str:
    """Render the analysis JSON as a compact report."""
    lines = ["# S2 controlled viewing study: analysis", ""]
    lines.append("Generated %s from %s questionnaire rows and %s session logs." % (
        analysis["meta"]["created"],
        analysis["cleaning"]["questionnaire_rows"],
        analysis["cleaning"]["session_logs"],
    ))
    lines.append("")
    cleaning = analysis["cleaning"]
    lines.append("## Data cleaning")
    lines.append("")
    lines.append(
        "Included: %d blocks / %d participants. Excluded: %d blocks."
        % (cleaning["included_rows"], cleaning["included_participants"], cleaning["excluded_rows"])
    )
    if cleaning["rows_by_condition"]:
        lines.append("")
        lines.append("Blocks by condition: " + ", ".join(
            "%s=%d" % (condition, cleaning["rows_by_condition"][condition]) for condition in CONDITIONS
        ))
    if cleaning["excluded"]:
        lines.append("")
        lines.extend(_table(
            ["participant", "condition", "video", "reasons"],
            [[row["participant"], row["condition"], row["video"] or "-", "; ".join(row["reasons"])] for row in cleaning["excluded"]],
        ).splitlines())
    for note in cleaning["notes"]:
        lines.append("")
        lines.append("- " + note)
    lines.append("")

    lines.append("## Descriptives by condition")
    lines.append("")
    header = ["outcome"] + [
        "%s (n=%d)" % (condition, analysis["descriptives"]["by_condition"][condition]["n_blocks"])
        for condition in CONDITIONS
    ]
    rows = []
    for outcome in OUTCOMES:
        cells = [outcome]
        for condition in CONDITIONS:
            summary = analysis["descriptives"]["by_condition"][condition]["outcomes"][outcome]
            if summary["n"]:
                cells.append("%s (SD %s)" % (_fmt(summary["mean"]), _fmt(summary["sd"])))
            else:
                cells.append("-")
        rows.append(cells)
    lines.extend(_table(header, rows).splitlines())
    lines.append("")

    lines.append("## Planned contrasts (paired Wilcoxon, Holm over 3) ")
    lines.append("")
    for outcome in PRIMARY_OUTCOMES + SECONDARY_OUTCOMES:
        entries = analysis["contrasts"].get(outcome) or []
        if not entries:
            continue
        lines.append("### " + outcome)
        lines.append("")
        rows = []
        for entry in entries:
            if "wilcoxon" not in entry:
                rows.append([entry["contrast"], entry.get("n"), "-", "-", "-", "-", entry.get("note", "")])
                continue
            test = entry["wilcoxon"]
            rows.append([
                entry["contrast"],
                entry["n"],
                _fmt(entry.get("mean_diff")),
                "%s .. %s" % (_fmt(entry.get("ci95", [None, None])[0]), _fmt(entry.get("ci95", [None, None])[1])),
                _fmt(entry.get("d")),
                "%.4f / %.4f" % (test.get("p", 1.0), entry.get("p_holm", 1.0)),
                "W=%.1f z=%s" % (test.get("statistic", 0.0), test.get("z")),
            ])
        lines.extend(_table(["contrast", "n", "mean diff", "95% CI", "d", "p / p_holm", "test"], rows).splitlines())
        lines.append("")

    lmm = analysis["lmm"]
    lines.append("## Mixed model")
    lines.append("")
    if not lmm.get("available"):
        lines.append(lmm.get("note", "not available"))
    else:
        for outcome, model in (lmm.get("models") or {}).items():
            if "error" in model:
                lines.append("- %s: %s" % (outcome, model["error"]))
            else:
                lines.append("- %s: params %s" % (outcome, model.get("params")))
    lines.append("")

    rq1 = analysis["rq1"]
    lines.append("## RQ1: condition A, genre x density (exploratory)")
    lines.append("")
    lines.append(rq1.get("note", ""))
    lines.append("")
    rows = []
    for entry in rq1.get("rows") or []:
        rows.append([
            entry["genre"], entry["density"], entry["n_blocks"], entry["n_videos"],
            _fmt(entry["means"].get("discomfort")), _fmt(entry["means"].get("coview")),
            _fmt(entry["means"].get("control")), _fmt(entry["means"].get("fomo")),
        ])
    lines.extend(_table(["genre", "density", "blocks", "videos", "discomfort", "coview", "control", "fomo"], rows).splitlines())
    lines.append("")

    rq2 = analysis["rq2"]
    lines.append("## RQ2: latency and behaviour")
    lines.append("")
    rows = []
    for condition in CONDITIONS:
        stats = rq2["latency"][condition]
        rows.append([condition, stats["n"], _fmt(stats["p50_ms"]), _fmt(stats["p95_ms"]), _fmt(stats["mean_ms"])])
    rows.append(["overall", rq2["latency_overall"]["n"], _fmt(rq2["latency_overall"]["p50_ms"]),
                 _fmt(rq2["latency_overall"]["p95_ms"]), _fmt(rq2["latency_overall"]["mean_ms"])])
    lines.extend(_table(["condition", "segments", "p50 ms", "p95 ms", "mean ms"], rows).splitlines())
    lines.append("")
    behaviour = rq2["behaviour"]
    lines.append(
        "Corrections: %d (%s per 10 min), restores: %d, expands: %d, pref changes: %d, online segments: %s."
        % (
            behaviour.get("corrections", 0),
            _fmt(behaviour.get("corrections_per_10min")),
            behaviour.get("restores", 0),
            behaviour.get("expands", 0),
            behaviour.get("pref_changes", 0),
            _fmt(rq2.get("online_share"), 3),
        )
    )
    lines.append("")
    gold = rq2.get("gold") or {}
    lines.append("## RQ2: gold evaluation")
    lines.append("")
    if not gold.get("available"):
        lines.append(gold.get("note", "not available"))
    else:
        lines.append("n=%d accuracy=%s macro-F1=%s (skipped %d rows)." % (
            gold.get("n"), _fmt(gold.get("accuracy"), 3), _fmt(gold.get("macro_f1"), 3), gold.get("skipped_rows", 0)
        ))
        lines.append("")
        rows = [
            [label, _fmt(stats["precision"], 3), _fmt(stats["recall"], 3), _fmt(stats["f1"], 3), stats["support"]]
            for label, stats in (gold.get("per_tag") or {}).items()
        ]
        lines.extend(_table(["label", "precision", "recall", "F1", "support"], rows).splitlines())
    lines.append("")
    double = rq2.get("double_annotation") or {}
    lines.append("## Double annotation")
    lines.append("")
    if not double.get("available"):
        lines.append(double.get("note", "not available"))
    else:
        lines.append("n=%d agreement=%s Krippendorff alpha=%s." % (
            double.get("n"), _fmt(double.get("agreement"), 3), _fmt(double.get("krippendorff_alpha"), 3)
        ))
    lines.append("")
    for note in analysis.get("notes") or []:
        lines.append("- " + note)
    lines.append("")
    return "\n".join(lines)


def analyse(args: argparse.Namespace) -> dict:
    """Run every analysis step and return the analysis dict."""
    questionnaire, questionnaire_problems = load_questionnaire(args.questionnaire)
    sessions, session_problems = load_sessions(args.raw_dir)
    durations = load_video_durations(args.study_report)
    rows, cleaning = clean_data(questionnaire, sessions, durations)
    cleaning["problems"] = questionnaire_problems + session_problems

    analysis = {
        "schema": 1,
        "meta": {
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "inputs": {
                "questionnaire": str(args.questionnaire),
                "raw_dir": str(args.raw_dir),
                "gold": str(args.gold),
                "gold_double": str(args.gold_double),
                "models_dir": str(args.models_dir),
                "study_report": str(args.study_report),
            },
            "bootstrap_samples": args.bootstrap,
            "seed": SEED,
        },
        "cleaning": cleaning,
        "descriptives": descriptives(rows),
        "reliability": reliability(rows),
        "contrasts": planned_contrasts(rows, args.bootstrap),
        "lmm": try_lmm(rows),
        "rq1": rq1_video_level(rows),
        "rq2": {
            **rq2_from_sessions(sessions),
            "gold": evaluate_gold(args.gold, args.models_dir),
            "double_annotation": evaluate_double_annotation(args.gold_double),
        },
        "notes": [],
    }
    if not rows:
        analysis["notes"].append("no analysable questionnaire rows: every table is empty")
    if cleaning["problems"]:
        analysis["notes"].append("input problems: " + "; ".join(cleaning["problems"]))
    if analysis["rq2"]["gold"].get("available"):
        analysis["notes"].append(
            "gold evaluation uses per-video models in %s; skipped rows are counted" % args.models_dir
        )
    analysis["notes"].append(
        "Wilcoxon p-values use the normal approximation with tie correction; the bootstrap CI resamples participants"
    )
    return analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--questionnaire", default=str(DEFAULT_QUESTIONNAIRE))
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--gold-double", default=str(DEFAULT_GOLD_DOUBLE))
    parser.add_argument("--models-dir", default=str(DEFAULT_MODELS))
    parser.add_argument("--study-report", default=str(DEFAULT_STUDY_REPORT))
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--markdown", default=str(DEFAULT_MARKDOWN))
    parser.add_argument("--bootstrap", type=int, default=MAX_BOOTSTRAP)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    analysis = analyse(args)
    _write_json(args.out, analysis)
    _write_text(args.markdown, render_markdown(analysis) + "\n")
    if not args.quiet:
        cleaning = analysis["cleaning"]
        print(
            "analysed %d blocks / %d participants; wrote %s and %s"
            % (cleaning["included_rows"], cleaning["included_participants"], args.out, args.markdown)
        )
        for note in analysis["notes"]:
            print("  note: " + note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
