"""Pilot audit sample: draw stratified danmaku from the rules cache, label with the LLM."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from danmaku_filter.classify.llm import LLMClassifier
from danmaku_filter.taxonomy import CATEGORY_IDS
from danmaku_filter.types import Danmaku

SOURCE = Path("data/backup/BV1GJ411x7h7.rules.json")
OUT = Path("/tmp/audit_sample.json")

LABELS = list(CATEGORY_IDS) + ["other"]
PER_LABEL = 6  # 7 labels x 6 = 42


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    items = payload["items"]
    by_label: dict[str, list[tuple[str, dict]]] = {k: [] for k in LABELS}
    for key, item in items.items():
        by_label.setdefault(item.get("dominant", "other"), []).append((key, item))

    rng = random.Random(20261104)
    picked: list[tuple[str, dict]] = []
    for label in LABELS:
        pool = by_label.get(label, [])
        if len(pool) <= PER_LABEL:
            picked.extend(pool)
        else:
            picked.extend(rng.sample(pool, PER_LABEL))

    danmaku = [
        Danmaku(text=item["text"], time_ms=int(item.get("time_ms", 0) or 0),
                 mode=item.get("mode", 1), dmid=key)
        for key, item in picked
    ]
    clf = LLMClassifier(batch_size=40, video_context="Bilibili music/meme video, Chinese danmaku")
    results = clf.classify_batch(danmaku)

    rows = []
    for (key, item), res in zip(picked, results):
        rows.append({
            "id": key,
            "text": item["text"],
            "rules_label": item.get("dominant"),
            "pred_dominant": res.label,
            "pred_score": round(res.confidence, 3),
            "pred_tags": {k: round(v, 2) for k, v in res.tags.items()},
            "note": res.rationale,
        })
    OUT.write_text(json.dumps({"bvid": payload.get("bvid"), "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"picked": len(rows), "out": str(OUT), "dominant": {}}))


if __name__ == "__main__":
    main()
