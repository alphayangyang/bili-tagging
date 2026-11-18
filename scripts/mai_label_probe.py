#!/usr/bin/env python3
"""Probe mai-replier-9b (QQ group-chat model) as a danmaku pragmatic annotator.

Runs the 120-item study gold set through the local Ollama model with a few-shot
prompt (anchors disjoint from the study gold), parses the returned JSON (array or
one-object-per-line), and reports agreement + macro F1 via scripts/f1_eval.py.
"""
import json, os, re, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts.f1_eval import cost, prf, summarize  # noqa: E402

OLLAMA = os.environ.get("MAI_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("MAI_MODEL", "mai-replier-9b")
GOLD = os.path.join(ROOT, "data/study/gold_v2.jsonl")
OUT = os.path.join(ROOT, "data/experiments/results/mai_label_probe.json")

LABELS = [
    ("information", "提供信息或解释剧情设定"),
    ("humor", "玩笑、梗、调侃、笑点"),
    ("emotional_resonance", "共鸣、被戳到、情绪反应"),
    ("irrelevant_repetition", "无意义重复、刷屏、纯符号"),
    ("attack", "辱骂、人身攻击"),
    ("conflict", "争执、对杠、互喷"),
    ("spoiler", "剧透、提前说剧情结局"),
    ("political", "政治立场或时政评论"),
    ("inflammatory", "引战、钓鱼、贴标签挑衅"),
    ("other", "没有社交功能：@人、离题私事、看不懂的内部梗"),
]

FEWSHOT = [
    ("这个翻译是官方的吗", "information"),
    ("太好听了，谢谢我的好兄弟骗我", "humor"),
    ("这首歌真的陪我走过很多年", "emotional_resonance"),
    ("1111111", "irrelevant_repetition"),
    ("up主就是个傻逼", "attack"),
    ("你行你上啊，别在这阴阳怪气", "conflict"),
    ("后面剧透，凶手是他弟弟", "spoiler"),
    ("这政策就是摆明了不想让人说话", "political"),
    ("楼上的小粉红又来护主了", "inflammatory"),
    ("@老大南", "other"),
]

SYSTEM = (
    "你是中文弹幕语用标注员。你熟悉B站弹幕和QQ群聊的网络用语：单独一个问号表示惊讶或困惑，"
    "草/6/笑死表示笑，前排/打卡是凑热闹。给每条弹幕选最贴近的一个社会功能。"
    "标签：" + "；".join("%s=%s" % kv for kv in LABELS) + "。"
    "不要输出思考过程，不要输出别的文字；每条输出一行 JSON，形如 "
    '{"id": 1, "label": "humor", "note": "短理由"}，label 必须是上面十个之一。'
)


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def build_prompt(rows, offset):
    body = ["%d. %s" % (offset + i + 1, row["text"]) for i, row in enumerate(rows)]
    shots = ["%s -> %s" % (t, lab) for t, lab in FEWSHOT]
    return ("示例（只示范标签选择，不要照抄）：\n" + "\n".join(shots) +
            "\n\n请标注下面 %d 条：\n%s" % (len(rows), "\n".join(body)))


def call(prompt, timeout=600):
    payload = json.dumps({
        "model": MODEL, "stream": False,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "options": {"num_ctx": 8192, "temperature": 0.0, "num_predict": 1200},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("message", {}).get("content", "")


def body_of(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return "\n".join(lines[i:])
    return text


def normalize(rows):
    out = []
    for row in rows:
        label = str(row.get("label", "")).strip().lower().replace("-", "_")
        if label not in dict(LABELS):
            label = "other"
        out.append((label, str(row.get("note", ""))[:40]))
    return out


def parse_rows(text, expected):
    body = body_of(text)
    for chunk in reversed(re.findall(r"\[.*\]", body, flags=re.S)):
        try:
            rows = json.loads(chunk)
        except Exception:
            continue
        if isinstance(rows, list) and len(rows) == expected:
            return normalize(rows)
    objs = []
    for chunk in re.findall(r"\{[^{}]*\}", body, flags=re.S):
        try:
            objs.append(json.loads(chunk))
        except Exception:
            continue
    if len(objs) == expected:
        return normalize(objs)
    return None


def main():
    gold = read_jsonl(GOLD)
    batch = int(os.environ.get("MAI_BATCH", "4"))
    results, started = [], time.time()
    for start in range(0, len(gold), batch):
        rows = gold[start:start + batch]
        got = None
        for attempt in range(3):
            text = call(build_prompt(rows, start))
            got = parse_rows(text, len(rows))
            if got:
                break
            sys.stderr.write("batch %d attempt %d parse failed; tail=%r\n" % (start, attempt, text[-160:]))
        if not got:
            got = [("other", "parse-failed")] * len(rows)
        for row, (label, note) in zip(rows, got):
            results.append({"text": row["text"], "bvid": row["bvid"],
                            "gold": row["label"], "pred": label, "note": note})
        sys.stderr.write("done %d/%d (%.1fs)\n" % (min(start + batch, len(gold)), len(gold), time.time() - started))
    preds = [r["pred"] for r in results]
    labels = [r["gold"] for r in results]
    pairs = list(zip(labels, preds))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"rows": results}, fh, ensure_ascii=False, indent=1)
    summary = summarize(pairs)
    summary["agreement"] = sum(1 for a, b in zip(preds, labels) if a == b) / float(len(labels))
    summary["n"] = len(labels)
    summary["per_label"] = {lab: prf(pairs, lab) for lab, _ in LABELS}
    summary["mean_cost"] = sum(cost(g, p) for g, p in pairs) / float(len(pairs))
    summary["elapsed_s"] = round(time.time() - started, 1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "rows": results}, fh, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "per_label"}, ensure_ascii=False, indent=1))
    print(json.dumps(summary["per_label"], ensure_ascii=False))


if __name__ == "__main__":
    main()
