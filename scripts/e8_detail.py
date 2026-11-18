"""Per-item detail for the prompt ablation (reads the disk cache, no API calls)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "experiments"))
import common as C  # noqa: E402
import eval_prompt as EP  # noqa: E402

CACHE = ROOT / "data" / "experiments" / "results" / "cache"
STYLES = ("v2", "v3", "v4")


def load(kind: str, tag: str) -> dict[str, dict]:
    path = CACHE / ("%s_%s.json" % (kind, tag))
    if not path.exists():
        return {}
    return {rec["text"]: rec for rec in json.loads(path.read_text(encoding="utf-8"))}


def main() -> int:
    backend = sys.argv[1] if len(sys.argv) > 1 else "deepseek"
    bundle = EP.sets()
    out = {"backend": backend, "sets": {}}
    for name, rows in bundle.items():
        entry = {"items": [], "pairs": {}, "score": {}}
        caches = {style: load(backend, "prompt%s-%s" % (style, name)) for style in STYLES}
        for row in rows:
            text = row["text"]
            gold = row.get("dominant") or row.get("label")
            rec = {"text": text, "gold": gold}
            for style in STYLES:
                cached = caches[style].get(text)
                rec[style] = {"label": cached["label"], "tags": cached["tags"]} if cached else None
            entry["items"].append(rec)
            if name == "boundary":
                pair = row.get("focus") or ""
                bucket = entry["pairs"].setdefault(
                    pair, {"n": 0, **{style: 0 for style in STYLES},
                           **{"must_" + style: 0 for style in STYLES}})
                bucket["n"] += 1
                must = row.get("must") or {}
                for style in STYLES:
                    cached = caches[style].get(text)
                    if not cached:
                        continue
                    if cached["label"] == gold:
                        bucket[style] += 1
                    if must and not any(cached["tags"].get(tag, 0.0) >= floor
                                        for tag, floor in must.items()):
                        bucket["must_" + style] -= 0
                    elif must:
                        bucket["must_" + style] += 1
        # dominant accuracy and must-hit per style
        for style in STYLES:
            hits = [1 for row in entry["items"]
                    if (row.get(style) or {}).get("label") == row["gold"]]
            entry["score"][style] = round(len(hits) / max(1, len(entry["items"])), 4)
        out["sets"][name] = entry
    dest = ROOT / "data" / "experiments" / "results" / ("e8_detail_%s.json" % backend)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    for name in ("audit", "boundary"):
        items = out["sets"][name]["items"]
        print("[%s/%s] dominant accuracy %s" % (backend, name, out["sets"][name]["score"]))
        for older, newer in (("v2", "v3"), ("v3", "v4"), ("v2", "v4")):
            changed = [i for i in items if i[older] and i[newer] and i[older]["label"] != i[newer]["label"]]
            fixed = [i for i in changed if i[older]["label"] != i["gold"] and i[newer]["label"] == i["gold"]]
            broke = [i for i in changed if i[older]["label"] == i["gold"] and i[newer]["label"] != i["gold"]]
            print("  %s->%s changed %d fixed %d broke %d" % (older, newer, len(changed), len(fixed), len(broke)))
            for i in fixed:
                print("    + %-22s %s -> %s | %s" % (i["gold"], i[older]["label"], i[newer]["label"], i["text"][:36]))
            for i in broke:
                print("    - %-22s %s -> %s | %s" % (i["gold"], i[older]["label"], i[newer]["label"], i["text"][:36]))
    for pair, bucket in sorted(out["sets"]["boundary"]["pairs"].items()):
        cells = ["%s %d/%d" % (style, bucket[style], bucket["n"]) for style in STYLES]
        musts = ["%s %d/%d" % (style, bucket["must_" + style], bucket["n"]) for style in STYLES]
        print("  pair %-32s %s | must %s" % (pair, " ".join(cells), " ".join(musts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
