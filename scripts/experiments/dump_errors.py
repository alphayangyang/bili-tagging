"""Dump qualitative errors of a per-video bootstrap L1 model (for the report's error analysis)."""
import json
import common as C

data = C.load_dataset()
out = {}
for bvid in ("BV1GJ411x7h7", "BV1rCxe6wEN9", "BV1mV4F6mE5Z"):
    rows = data[bvid]
    test = C.stratified_sample(rows, 120, seed=20261108)
    test_texts = {r["text"] for r in test}
    pool = [r for r in rows if r["text"] not in test_texts]
    train = C.stratified_sample(pool, 200, seed=20261108)
    preds = C.predict_linear(train, test, "centroid")
    errors = []
    for r, p in zip(test, preds):
        if p["label"] != r["dominant"]:
            errors.append({"text": r["text"], "gold": r["dominant"], "pred": p["label"],
                           "score": round(float(p["score"]), 3)})
    out[bvid] = {"n_test": len(test), "errors": errors[:12]}
    print(bvid, len(errors), "errors /", len(test))
    for e in errors[:6]:
        print("  ", json.dumps(e, ensure_ascii=False))
(C.RESULTS / "error_dump.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
