"""E6: latency / throughput / model-size benchmark."""
from __future__ import annotations

import json
import time

import common as C


def timed(fn, rows):
    started = time.time()
    preds = fn(rows)
    return preds, (time.time() - started) * 1000


def main() -> None:
    data = C.load_dataset()
    a_bvid = "BV1GJ411x7h7"
    a_rows = data[a_bvid]
    train = C.stratified_sample(a_rows, 600, seed=1)
    probe = C.stratified_sample([r for r in a_rows if r["text"] not in {t["text"] for t in train}], 200, seed=2)
    out: dict = {"inprocess": {}, "http": {}, "sizes": {}}

    rules_preds, rules_ms = timed(C.predict_rules, probe)
    out["inprocess"]["rules"] = {"n": len(probe), "ms_per_item": round(rules_ms / len(probe), 4)}

    for kind in ("centroid", "softmax"):
        preds, ms = timed(lambda rows: C.predict_linear(train, rows, kind), probe)
        out["inprocess"][f"linear_{kind}"] = {"n": len(probe), "ms_per_item": round(ms / len(probe), 4)}

    preds, ms = timed(lambda rows: C.predict_sklearn(train, rows), probe)
    out["inprocess"]["tfidf_svm"] = {"n": len(probe), "ms_per_item": round(ms / len(probe), 4)}

    preds, ms = timed(lambda rows: C.predict_bge_anchor(rows), probe[:40])
    out["inprocess"]["bge_anchor"] = {"n": 40, "ms_per_item": round(ms / 40, 4)}

    out["inprocess"]["qwen35_4b"] = {"ms_per_item": 800.0, "source": "E1 matrix (network, local GPU)"}
    out["inprocess"]["deepseek_flash"] = {"ms_per_item": 530.0, "source": "E1 matrix (network, cloud)"}

    # model sizes
    for path in sorted((C.ROOT / "data" / "models").glob("*.json")):
        out["sizes"][path.name] = round(path.stat().st_size / 1024, 1)
    from danmaku_filter.classify.linear import LinearTagger
    tagger = LinearTagger(kind="centroid")
    tagger.train([r["text"] for r in train], [r["dominant"] for r in train])
    out["sizes"]["trained_centroid_600rows"] = round(len(json.dumps(tagger.to_dict())) / 1024, 1)

    # HTTP end-to-end with the real server (linear backend)
    import tempfile
    from pathlib import Path
    from danmaku_filter.server import make_server
    tmp = Path(tempfile.mkdtemp())
    server = make_server("127.0.0.1", 0, tags_dir=str(tmp / "tags"), models_dir=str(C.ROOT / "data" / "models"),
                         online_backend="linear", online_classifier=False)
    port = server.server_address[1]
    import threading, urllib.request
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        items = C.stratified_sample(a_rows, 300, seed=3)
        body = json.dumps({"bvid": "BV1HTTPTEST01", "dmids": [str(i) for i in range(len(items))],
                           "texts": {str(i): r["text"] for i, r in enumerate(items)},
                           "ephemeral": True}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/decisions", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        started = time.time()
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
        elapsed = (time.time() - started) * 1000
        out["http"] = {"n": len(items), "total_ms": round(elapsed, 1),
                       "ms_per_item": round(elapsed / len(items), 3),
                       "found": payload.get("found"), "online": payload.get("online")}
    finally:
        server.shutdown()
        server.server_close()

    (C.RESULTS / "latency.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
