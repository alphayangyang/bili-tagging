"""Build the frozen experiment dataset for the ablation report.

Sources
-------
* existing deepseek-flash teacher labels in data/tags/*.json (source starts with "llm")
* for videos without full teacher labels, label their cached canned texts with the cloud teacher

Output
------
data/experiments/dataset.jsonl   one row per comment: bvid, domain, order, text, time_ms, tags, dominant, score, label_source
data/experiments/summary.json    per-video counts / distributions
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from danmaku_filter.classify.llm import LLMClassifier
from danmaku_filter.types import Danmaku

ROOT = Path(__file__).resolve().parents[2]
TAGS = ROOT / "data" / "tags"
OUT = ROOT / "data" / "experiments"
LABELED = OUT / "labeled"

DOMAINS = {
    "BV1GJ411x7h7": ("music/meme", "【官方 MV】Never Gonna Give You Up - Rick Astley"),
    "BV1w5xh6sEEz": ("music/meme", "【只对你有感觉】|“【恋爱の小曲】”"),
    "BV16hxj6jE19": ("music/meme", "雨爱-🐳蓝色大肥《鱼爱》你呀"),
    "BV1rCxe6wEN9": ("politics", "一条视频看10月世界：中东在燃烧，法国在骚乱，中国网友在跨国吵架"),
    "BV1bnxr6cEJ9": ("politics/conflict", "外网中德骂战！10月31号 再现神推：中国人用梯子是买动物园门票"),
    "BV1mV4F6mE5Z": ("gaming", "八倍AK致敬吃鸡，队友脸色对了"),
    "BV1W18Y61Ee5": ("story/drama", "我妈妈第一次怀孕时……"),
    "BV1PF4g6ME26": ("comedy/配音", "《这是我儿子？》"),
}
# videos whose cached labels are only rules/linear -> need cloud teacher labelling
NEEDS_TEACHER = ["BV1bnxr6cEJ9", "BV1mV4F6mE5Z", "BV1W18Y61Ee5"]


def load_cache(bvid: str) -> dict:
    return json.loads((TAGS / f"{bvid}.json").read_text(encoding="utf-8"))


def teacher_rows(bvid: str) -> list[dict] | None:
    path = LABELED / f"{bvid}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def label_with_teacher(bvid: str, context: str) -> list[dict]:
    payload = load_cache(bvid)
    rows = [v for v in payload["items"].values() if (v.get("text") or "").strip()]
    seen: set[str] = set()
    uniq: list[dict] = []
    for row in rows:
        text = row["text"].strip()
        if text in seen:
            continue
        seen.add(text)
        uniq.append(row)
    clf = LLMClassifier(batch_size=100, video_context=context)
    out: list[dict] = []
    for start in range(0, len(uniq), 100):
        chunk = uniq[start:start + 100]
        batch = [Danmaku(text=r["text"], time_ms=int(r.get("time_ms") or 0), mode=int(r.get("mode") or 1),
                         dmid=str(i)) for i, r in enumerate(chunk)]
        results = clf.classify_batch(batch)
        for row, res in zip(chunk, results):
            out.append({"text": row["text"], "time_ms": int(row.get("time_ms") or 0),
                        "mode": int(row.get("mode") or 1), "tags": dict(res.tags),
                        "dominant": res.label, "score": float(res.confidence),
                        "source": f"llm-{res.source}"})
        print(f"[{bvid}] labelled {min(start + 100, len(uniq))}/{len(uniq)}", flush=True)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    LABELED.mkdir(parents=True, exist_ok=True)
    dataset_path = OUT / "dataset.jsonl"
    summary: dict = {"videos": {}, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with dataset_path.open("w", encoding="utf-8") as fh:
        for bvid, (domain, title) in DOMAINS.items():
            cache = load_cache(bvid) if (TAGS / f"{bvid}.json").exists() else None
            if cache is None:
                print(f"[skip] {bvid}: no cache", flush=True)
                continue
            rows = teacher_rows(bvid)
            if rows is None:
                llm_rows = [v for v in cache["items"].values() if str(v.get("source", "")).startswith("llm")]
                if len(llm_rows) >= 100:
                    rows = [{"text": v["text"], "time_ms": int(v.get("time_ms") or 0),
                             "mode": int(v.get("mode") or 1), "tags": dict(v.get("tags") or {}),
                             "dominant": v.get("dominant"), "score": float(v.get("score") or 0),
                             "source": v.get("source")} for v in llm_rows if v.get("text")]
                elif bvid in NEEDS_TEACHER:
                    print(f"[label] {bvid}: running cloud teacher", flush=True)
                    rows = label_with_teacher(bvid, title)
                    (LABELED / f"{bvid}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                else:
                    print(f"[skip] {bvid}: no teacher labels", flush=True)
                    continue
            for order, row in enumerate(rows):
                rec = {"bvid": bvid, "domain": domain, "title": title, "order": order,
                       "text": row["text"], "time_ms": row.get("time_ms", 0),
                       "tags": row.get("tags") or {}, "dominant": row.get("dominant"),
                       "score": row.get("score", 0), "label_source": row.get("source", "llm")}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            dist = Counter(r.get("dominant") for r in rows)
            ts = sorted(int(r.get("time_ms") or 0) for r in rows)
            summary["videos"][bvid] = {"domain": domain, "title": title, "rows": len(rows),
                                       "dist": dict(dist),
                                       "time_ms_nonzero": sum(1 for t in ts if t > 0),
                                       "time_ms_max": ts[-1] if ts else 0}
            print(f"[ok] {bvid} {domain} rows={len(rows)} dist={dict(dist)}", flush=True)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
