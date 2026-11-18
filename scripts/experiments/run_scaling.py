"""E4/E5: bootstrap scaling curves and confidence-gated escalation sweep."""
from __future__ import annotations

import json
import random
import time

import common as C

K_VALUES = [10, 25, 50, 100, 200, 400]
THETAS = [0.2, 0.3, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8]
SEEDS = [20261101, 20261102, 20261103]


def stratified_take(rows: list[dict], k: int, seed: int) -> list[dict]:
    if k >= len(rows):
        return list(rows)
    return C.stratified_sample(rows, k, seed=seed)


def main() -> None:
    data = C.load_dataset()
    out: dict = {"scaling": {}, "escalation": {}}

    for bvid, rows in data.items():
        if len(rows) < 120:
            continue
        test = C.stratified_sample(rows, 80, seed=20261105)
        test_texts = {r["text"] for r in test}
        pool = [r for r in rows if r["text"] not in test_texts]
        curve = {}
        for k in K_VALUES:
            if k > len(pool):
                continue
            scores = {"agreement": [], "macro_f1": [], "fit_s": []}
            for seed in SEEDS:
                train = stratified_take(pool, k, seed)
                started = time.time()
                preds = C.predict_linear(train, test, "centroid")
                fit = time.time() - started
                gold_labels = [r["dominant"] for r in test]
                pred_labels = [p["label"] for p in preds]
                scores["agreement"].append(C.agreement(gold_labels, pred_labels))
                scores["macro_f1"].append(C.macro_f1(gold_labels, pred_labels))
                scores["fit_s"].append(fit)
            curve[k] = {
                "agreement": round(sum(scores["agreement"]) / len(scores["agreement"]), 4),
                "agreement_min": round(min(scores["agreement"]), 4),
                "agreement_max": round(max(scores["agreement"]), 4),
                "macro_f1": round(sum(scores["macro_f1"]) / len(scores["macro_f1"]), 4),
                "predict_s": round(sum(scores["fit_s"]) / len(scores["fit_s"]), 4),
            }
            print(bvid, k, json.dumps(curve[k]), flush=True)
        out["scaling"][bvid] = {"rows": len(rows), "test_rows": len(test), "curve": curve}

        # escalation: train L1 on 200 teacher labels, test on the 80 held-out comments
        if len(pool) < 200:
            continue
        train = stratified_take(pool, 200, 20261105)
        preds = C.predict_linear(train, test, "centroid")
        confs = [float(p["score"]) for p in preds]
        gold_labels = [r["dominant"] for r in test]
        pred_labels = [p["label"] for p in preds]
        base = C.agreement(gold_labels, pred_labels)
        sweep = {}
        for theta in THETAS:
            escalated = [i for i, c in enumerate(confs) if c < theta]
            fixed = [gold_labels[i] if i in escalated else pred_labels[i] for i in range(len(test))]
            sweep[str(theta)] = {
                "ceiling_agreement": round(C.agreement(gold_labels, fixed), 4),
                "escalation_rate": round(len(escalated) / len(test), 4),
            }
        out["escalation"][bvid] = {"base_agreement": round(base, 4), "n": len(test), "sweep": sweep}
        print(bvid, "escalation", json.dumps(out["escalation"][bvid]), flush=True)

    (C.RESULTS / "scaling.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", C.RESULTS / "scaling.json")


if __name__ == "__main__":
    main()
