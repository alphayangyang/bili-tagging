"""E7: is cross-video failure mostly a label-prior shift? Test prior correction / EM adaptation."""
from __future__ import annotations

import json
import random

import common as C

LABELS = C.LABELS + ["other"]


def normalize(d: dict[str, float]) -> dict[str, float]:
    total = sum(d.values()) or 1.0
    return {k: v / total for k, v in d.items()}


def argmax(scores: dict[str, float]) -> str:
    return max(scores.items(), key=lambda kv: kv[1])[0]


def main() -> None:
    data = C.load_dataset()
    out: dict = {}

    for bvid, rows in data.items():
        if len(rows) < 120:
            continue
        test = C.stratified_sample(rows, 120, seed=20261107)
        test_texts = {r["text"] for r in test}
        pool = [r for r in rows if r["text"] not in test_texts]
        train = [r for b, rs in data.items() if b != bvid for r in rs]
        rng = random.Random(20261107)
        if len(train) > 2000:
            train = rng.sample(train, 2000)

        tagger_preds = C.predict_linear(train, test, "centroid")
        scores = [dict(p["tags"]) for p in tagger_preds]
        gold = [r["dominant"] for r in test]
        base = C.agreement(gold, [argmax(s) for s in scores])

        # train prior
        train_prior = normalize({l: sum(1 for r in train if r["dominant"] == l) for l in LABELS})

        # oracle prior from all teacher labels of this video
        oracle_prior = normalize({l: sum(1 for r in rows if r["dominant"] == l) for l in LABELS})

        def corrected(scores_list: list[dict], target_prior: dict[str, float]) -> list[str]:
            return [argmax({l: scores_list[i].get(l, 0.0) * (target_prior.get(l, 1e-6) / max(train_prior.get(l, 1e-6), 1e-6))
                            for l in LABELS}) for i in range(len(scores_list))]

        oracle = C.agreement(gold, corrected(scores, oracle_prior))

        # few-shot prior from k teacher labels of this video (k=20)
        shots = C.stratified_sample(pool, 20, seed=20261107)
        fewshot_prior = normalize({l: sum(1 for r in shots if r["dominant"] == l) for l in LABELS})
        fewshot = C.agreement(gold, corrected(scores, fewshot_prior))

        # EM over unlabeled test: iterate prior from current predictions
        em_scores = [dict(s) for s in scores]
        em_prior = dict(train_prior)
        for _ in range(5):
            em_scores = [{l: scores[i].get(l, 0.0) * (em_prior.get(l, 1e-6) / max(train_prior.get(l, 1e-6), 1e-6))
                          for l in LABELS} for i in range(len(scores))]
            labels_now = [argmax(s) for s in em_scores]
            em_prior = normalize({l: sum(1 for x in labels_now if x == l) + 1.0 for l in LABELS})
        em = C.agreement(gold, [argmax(s) for s in em_scores])

        # pure prior-only baseline: predict most frequent class of this video (oracle) for all
        majority_oracle = C.agreement(gold, [max(oracle_prior.items(), key=lambda kv: kv[1])[0]] * len(gold))

        out[bvid] = {
            "rows": len(rows), "test": len(test),
            "l1_agreement": round(base, 4),
            "prior_corrected_oracle": round(oracle, 4),
            "prior_corrected_fewshot20": round(fewshot, 4),
            "em_self_training": round(em, 4),
            "majority_oracle_prior": round(majority_oracle, 4),
            "oracle_prior": {k: round(v, 3) for k, v in oracle_prior.items() if v > 0.005},
            "train_prior": {k: round(v, 3) for k, v in train_prior.items() if v > 0.005},
        }
        print(bvid, json.dumps(out[bvid], ensure_ascii=False), flush=True)

    (C.RESULTS / "adaptation.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", C.RESULTS / "adaptation.json")


if __name__ == "__main__":
    main()
