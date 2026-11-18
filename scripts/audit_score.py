"""Score the pilot tag audit: model dominant tag vs one-author manual labels.

Reads data/pilot/audit_sample.json and data/pilot/audit_gold.json, prints agreement
and the mean absolute intensity error.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "data" / "pilot" / "audit_sample.json"
GOLD = REPO / "data" / "pilot" / "audit_gold.json"


def main() -> None:
    rows = json.loads(SAMPLE.read_text(encoding="utf-8"))["rows"]
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    matches = 0
    errors = []
    mae = []
    for index, row in enumerate(rows, 1):
        label, intensity = gold[str(index)]
        if row["pred_dominant"] == label:
            matches += 1
        else:
            errors.append({"text": row["text"], "pred": row["pred_dominant"],
                           "pred_score": row["pred_score"], "manual": label})
        mae.append(abs(row["pred_score"] - intensity))
    n = len(rows)
    print(json.dumps({
        "n": n,
        "dominant_match": matches,
        "dominant_agreement": round(matches / n, 4),
        "mean_absolute_intensity_error": round(sum(mae) / n, 3),
        "errors": errors,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
