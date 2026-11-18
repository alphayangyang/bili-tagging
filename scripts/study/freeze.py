"""Freeze or verify the S2 study materials (hashes + counts).

Usage:
    python3 scripts/study/freeze.py --write      # write data/study/manifest.json
    python3 scripts/study/freeze.py --verify     # verify current files against the manifest

The manifest is the appendix record of what exactly was run: stimuli list,
teacher labels, gold set, per-video models, run sheet, questionnaire template
and the built userscript. Re-run --write only when the materials change on
purpose; otherwise --verify should stay green before every session.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDY_DIR = REPO_ROOT / "data" / "study"
MANIFEST = STUDY_DIR / "manifest.json"

# path -> role; the run sheet and questionnaire are templates, the rest are inputs.
TOP_LEVEL = (
    "stimuli.json",
    "report.json",
    "teacher_sample.jsonl",
    "gold.jsonl",
    "gold_sheet.jsonl",
    "run-sheet.csv",
    "questionnaire.csv",
)
USCRIPT = REPO_ROOT / "extension" / "dist" / "bili-danmaku-filter.user.js"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def collect_files() -> list[Path]:
    files = [STUDY_DIR / name for name in TOP_LEVEL]
    models = sorted((STUDY_DIR / "models").glob("*.json"))
    files.extend(models)
    if USCRIPT.is_file():
        files.append(USCRIPT)
    return [path for path in files if path.is_file()]


def _counts() -> dict:
    counts: dict = {}
    stimuli_path = STUDY_DIR / "stimuli.json"
    if stimuli_path.is_file():
        payload = json.loads(stimuli_path.read_text(encoding="utf-8"))
        items = payload.get("stimuli") or payload.get("videos") or []
        counts["stimuli"] = len(items) if isinstance(items, list) else None
    for name, key in (("teacher_sample.jsonl", "teacher_rows"), ("gold.jsonl", "gold_rows"), ("gold_sheet.jsonl", "gold_sheet_rows")):
        path = STUDY_DIR / name
        if path.is_file():
            counts[key] = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    models = list((STUDY_DIR / "models").glob("*.json"))
    counts["models"] = len(models)
    counts["model_bytes"] = sum(path.stat().st_size for path in models)
    sheet = STUDY_DIR / "run-sheet.csv"
    if sheet.is_file():
        rows = [line for line in sheet.read_text(encoding="utf-8").splitlines() if line.strip()]
        counts["run_sheet_rows"] = max(0, len(rows) - 1)
        counts["run_sheet_participants"] = len({line.split(",")[0] for line in rows[1:]}) if len(rows) > 1 else 0
    if USCRIPT.is_file():
        counts["userscript_bytes"] = USCRIPT.stat().st_size
    return counts


def build_manifest() -> dict:
    files = []
    for path in collect_files():
        files.append({"path": _relative(path), "bytes": path.stat().st_size, "sha256": sha256_of(path)})
    return {
        "schema": "dmf-study-manifest/1",
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
        "counts": _counts(),
    }


def write_manifest(path: Path | None = None) -> dict:
    target = MANIFEST if path is None else path
    payload = build_manifest()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def verify_manifest(path: Path | None = None) -> dict:
    target = MANIFEST if path is None else path
    if not target.is_file():
        return {"ok": False, "reason": "manifest missing: run python3 scripts/study/freeze.py --write", "checked": 0, "missing": [], "changed": []}
    payload = json.loads(target.read_text(encoding="utf-8"))
    missing = []
    changed = []
    for entry in payload.get("files", []):
        target = REPO_ROOT / entry["path"]
        if not target.is_file():
            missing.append(entry["path"])
        elif sha256_of(target) != entry["sha256"]:
            changed.append(entry["path"])
    return {
        "ok": not missing and not changed,
        "reason": "" if not missing and not changed else "materials changed; re-freeze after intentional edits",
        "checked": len(payload.get("files", [])),
        "missing": missing,
        "changed": changed,
        "created": payload.get("created"),
        "counts": payload.get("counts", {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify the S2 study materials.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.write:
        payload = write_manifest()
        print("wrote %s: %d files, counts=%s" % (MANIFEST, len(payload["files"]), json.dumps(payload["counts"], ensure_ascii=False)))
        return 0
    result = verify_manifest()
    if result["ok"]:
        print("manifest OK: %d files match (frozen %s)" % (result["checked"], result.get("created", "?")))
        return 0
    print("manifest FAIL: %s" % result["reason"])
    for name in result["missing"]:
        print("  missing: %s" % name)
    for name in result["changed"]:
        print("  changed: %s" % name)
    return 1


if __name__ == "__main__":
    sys.exit(main())
