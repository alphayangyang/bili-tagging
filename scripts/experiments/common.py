"""Shared helpers for the experiment harness (data loading, decision makers, metrics).

All experiments read the frozen dataset produced by prepare_dataset.py and write
JSON artefacts under data/experiments/results/.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "experiments"
RESULTS = DATA / "results"
CACHE = RESULTS / "cache"
RESULTS.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
LABELS = ["information", "humor", "emotional_resonance", "irrelevant_repetition",
          "attack", "conflict", "spoiler", "political", "inflammatory"]
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3"


# ---------------------------------------------------------------- data


def load_dataset() -> dict[str, list[dict]]:
    by_video: dict[str, list[dict]] = defaultdict(list)
    with (DATA / "dataset.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            by_video[row["bvid"]].append(row)
    for rows in by_video.values():
        rows.sort(key=lambda r: r["order"])
    return dict(by_video)


def load_summary() -> dict:
    return json.loads((DATA / "summary.json").read_text(encoding="utf-8"))


def human_gold() -> list[dict]:
    sample = json.loads((ROOT / "data" / "pilot" / "audit_sample.json").read_text(encoding="utf-8"))
    gold = json.loads((ROOT / "data" / "pilot" / "audit_gold.json").read_text(encoding="utf-8"))
    rows = []
    for idx, row in enumerate(sample["rows"], start=1):
        entry = gold.get(str(idx))
        if not entry:
            continue
        label, intensity = entry
        rows.append({"bvid": sample["bvid"], "text": row["text"], "label": label,
                     "intensity": float(intensity), "order": idx})
    return rows


def stratified_sample(rows: list[dict], total: int, seed: int = 20261105) -> list[dict]:
    """Stratified by dominant label, deterministic."""
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get("dominant"))].append(row)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    out: list[dict] = []
    keys = sorted(buckets)
    while len(out) < total and any(buckets[k] for k in keys):
        for key in keys:
            if buckets[key] and len(out) < total:
                out.append(buckets[key].pop())
    rng.shuffle(out)
    return out


# ---------------------------------------------------------------- metrics


def agreement(gold_labels: list[str], pred_labels: list[str]) -> float:
    if not gold_labels:
        return 0.0
    return sum(1 for g, p in zip(gold_labels, pred_labels) if g == p) / len(gold_labels)


def macro_f1(gold_labels: list[str], pred_labels: list[str], labels: list[str] | None = None) -> float:
    labels = labels or sorted(set(gold_labels) | set(pred_labels))
    scores = []
    for label in labels:
        tp = sum(1 for g, p in zip(gold_labels, pred_labels) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold_labels, pred_labels) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold_labels, pred_labels) if g == label and p != label)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def tag_mae(gold_tags: list[dict], pred_tags: list[dict]) -> float:
    diffs = []
    for gold, pred in zip(gold_tags, pred_tags):
        for label in set(gold) | set(pred):
            diffs.append(abs(float(gold.get(label, 0.0)) - float(pred.get(label, 0.0))))
    return sum(diffs) / len(diffs) if diffs else 0.0


def bootstrap_ci(values: list[float], n: int = 2000, seed: int = 20261105) -> tuple[float, float]:
    rng = random.Random(seed)
    if not values:
        return (0.0, 0.0)
    means = []
    for _ in range(n):
        means.append(sum(rng.choice(values) for _ in values) / len(values))
    means.sort()
    return (means[int(0.025 * n)], means[int(0.975 * n)])


# ---------------------------------------------------------------- decision makers


def _to_danmaku(rows: list[dict]):
    from danmaku_filter.types import Danmaku
    return [Danmaku(text=r["text"], time_ms=int(r.get("time_ms") or 0), mode=1, dmid=str(i))
            for i, r in enumerate(rows)]


def predict_rules(rows: list[dict]) -> list[dict]:
    from danmaku_filter.classify.rules import RuleClassifier
    clf = RuleClassifier()
    out = []
    for row in rows:
        res = clf.classify(_to_danmaku([row])[0])
        out.append({"label": res.label, "tags": dict(res.tags), "score": float(res.confidence)})
    return out


def predict_linear(train_rows: list[dict], test_rows: list[dict], kind: str = "centroid", cap: int = 4000) -> list[dict]:
    from danmaku_filter.classify.linear import LinearTagger
    train = train_rows
    if len(train) > cap:
        train = random.Random(20261105).sample(train, cap)
    tagger = LinearTagger(kind=kind)
    tagger.train([r["text"] for r in train], [r["dominant"] for r in train])
    scores = tagger.predict_scores([r["text"] for r in test_rows])
    out = []
    for score in scores:
        best = max(score.items(), key=lambda kv: (kv[1], -LABELS.index(kv[0]) if kv[0] in LABELS else -99))
        out.append({"label": best[0], "tags": score, "score": float(best[1])})
    return out


def predict_sklearn(train_rows: list[dict], test_rows: list[dict], cap: int = 4000) -> list[dict]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.svm import LinearSVC
    train = train_rows
    if len(train) > cap:
        train = random.Random(20261105).sample(train, cap)
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3), min_df=1, sublinear_tf=True)
    x_train = vec.fit_transform([r["text"] for r in train])
    x_test = vec.transform([r["text"] for r in test_rows])
    model = LinearSVC(C=0.5, class_weight="balanced")
    model.fit(x_train, [r["dominant"] for r in train])
    preds = model.predict(x_test)
    return [{"label": str(p), "tags": {str(p): 1.0}, "score": 1.0} for p in preds]


def ollama_embed(texts: list[str], batch: int = 16) -> list[list[float]]:
    out: list[list[float]] = []
    for start in range(0, len(texts), batch):
        chunk = texts[start:start + batch]
        body = json.dumps({"model": EMBED_MODEL, "input": chunk}).encode()
        req = urllib.request.Request(f"{OLLAMA}/api/embed", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
        out.extend(payload["embeddings"])
    return out


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def predict_bge_anchor(rows: list[dict], env_text: str = "", cache_path: Path | None = None) -> list[dict]:
    """Label anchors: mean embedding of each category definition + examples; argmax cosine."""
    from danmaku_filter.taxonomy import CATEGORY_DEFS
    anchors = {}
    for cat in CATEGORY_DEFS:
        texts = [f"{cat['name']}: {cat['definition']}"] + [str(e) for e in cat.get("examples", [])]
        anchors[cat["id"]] = texts
    flat = [t for texts in anchors.values() for t in texts]
    flat_emb = ollama_embed(flat)
    anchor_vec = {}
    i = 0
    for label, texts in anchors.items():
        vecs = flat_emb[i:i + len(texts)]
        i += len(texts)
        dim = len(vecs[0])
        anchor_vec[label] = [sum(v[d] for v in vecs) / len(vecs) for d in range(dim)]
    item_emb = ollama_embed([r["text"] for r in rows])
    out = []
    for emb in item_emb:
        scores = {label: max(0.0, _cos(emb, anchor_vec[label])) for label in anchor_vec}
        best = max(scores.items(), key=lambda kv: kv[1])
        out.append({"label": best[0], "tags": scores, "score": float(best[1])})
    return out


def llm_backend(kind: str) -> "object":
    """Configure env and return an LLMClassifier for 'qwen35-local' or 'deepseek'."""
    from danmaku_filter.classify import llm
    if kind == "qwen35-local":
        os.environ["DM_LLM_BASE_URL"] = f"{OLLAMA}/v1"
        os.environ["DM_LLM_MODEL"] = "qwen3.5:4b"
        os.environ["DM_LLM_OLLAMA"] = "1"
        os.environ["DM_LLM_NUM_CTX"] = "8192"
        os.environ["DM_LLM_BATCH"] = "8"
        os.environ["DM_LLM_TEMP"] = "0.2"
        os.environ["DM_LLM_TIMEOUT"] = "600"
    elif kind == "deepseek":
        for key in ("DM_LLM_BASE_URL", "DM_LLM_MODEL", "DM_LLM_OLLAMA", "DM_LLM_NUM_CTX",
                    "DM_LLM_BATCH", "DM_LLM_TEMP", "DM_LLM_TIMEOUT"):
            os.environ.pop(key, None)
        os.environ["DM_LLM_MODEL"] = "deepseek-flash"
        os.environ["DM_LLM_BATCH"] = "100"
        os.environ["DM_LLM_TIMEOUT"] = "300"
    else:
        raise ValueError(kind)
    return llm.LLMClassifier(batch_size=llm.batch_size(), video_context="Bilibili danmaku")


def predict_llm(rows: list[dict], kind: str, tag: str, context: str = "") -> list[dict]:
    """Run an LLM backend over rows with a disk cache keyed by (kind, tag)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{kind}_{tag}".replace("/", "_")
    path = CACHE / f"{key}.json"
    cached: dict[str, dict] = {}
    if path.exists():
        for rec in json.loads(path.read_text(encoding="utf-8")):
            cached[rec["text"]] = rec
    todo = [r for r in rows if r["text"] not in cached]
    if todo:
        from danmaku_filter.classify import llm
        clf = llm_backend(kind)
        if context:
            clf.video_context = context
        started = time.time()
        for start in range(0, len(todo), 40):
            chunk = todo[start:start + 40]
            for attempt in range(3):
                try:
                    results = clf.classify_batch(_to_danmaku(chunk))
                    break
                except Exception as exc:  # a malformed generation must not kill the whole matrix
                    print(f"  [{kind}/{tag}] chunk {start} attempt {attempt + 1} failed: {exc}", flush=True)
                    if attempt == 2:
                        raise
            for row, res in zip(chunk, results):
                cached[row["text"]] = {"text": row["text"], "label": res.label,
                                       "tags": dict(res.tags), "score": float(res.confidence)}
            path.write_text(json.dumps(list(cached.values()), ensure_ascii=False), encoding="utf-8")
            print(f"  [{kind}/{tag}] {min(start + 40, len(todo))}/{len(todo)} "
                  f"{(time.time() - started):.0f}s", flush=True)
        path.write_text(json.dumps(list(cached.values()), ensure_ascii=False), encoding="utf-8")
    return [{"label": cached[r["text"]]["label"], "tags": cached[r["text"]]["tags"],
             "score": cached[r["text"]]["score"]} for r in rows]


def evaluate(rows: list[dict], preds: list[dict], elapsed_ms: float) -> dict:
    gold_labels = [r.get("dominant") or r.get("label") for r in rows]
    pred_labels = [p["label"] for p in preds]
    gold_tags = [r.get("tags") or {} for r in rows]
    pred_tags = [p.get("tags") or {} for p in preds]
    hits = [1.0 if g == p else 0.0 for g, p in zip(gold_labels, pred_labels)]
    lo, hi = bootstrap_ci(hits, n=1000)
    return {
        "n": len(rows),
        "agreement": round(agreement(gold_labels, pred_labels), 4),
        "agreement_ci95": [round(lo, 4), round(hi, 4)],
        "macro_f1": round(macro_f1(gold_labels, pred_labels), 4),
        "tag_mae": round(tag_mae(gold_tags, pred_tags), 4),
        "ms_per_item": round(elapsed_ms / max(1, len(rows)), 3),
        "pred_dist": dict(Counter(pred_labels)),
        **({"intensity_mae": round(sum(abs(float(p.get("score", 0)) - float(r.get("intensity", 0)))
                                        for r, p in zip(rows, preds)) / max(1, len(rows)), 4)}
           if all("intensity" in r for r in rows) else {}),
    }
