"""Build per-block material packets for the AI-participant study run.

For each (participant, block) row of the 6-participant latin square this script
computes exactly what the participant would see under that condition (A all on,
B platform keyword blocking, C prototype semantic filtering) using the real
comment pool, the shipped per-video model and the repository policy layer, then
writes a markdown packet with the video facts, frame paths, counts and sampled
comments. AI participants read the packet and answer the questionnaire.

Usage::
    python3 scripts/study/agents/build_materials.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from danmaku_filter.classify.linear import LinearTagger
from danmaku_filter.policy import DEFAULT_PREFS, decide

AGENTS = REPO / "data" / "study" / "agents"
RUN_SHEET = AGENTS / "run-sheet-8.csv"
FRAMES = AGENTS / "frames"
BLOCKS = AGENTS / "blocks"

KEYWORDS = ("剧透", "前方高能", "傻逼", "打卡", "？？？")
PROTOTYPE_PREFS = {
    "min_confidence": 0.3,
    "fail_open": True,
    "per_type": {
        "attack": "hide",
        "conflict": "hide",
        "inflammatory": "hide",
        "irrelevant_repetition": "hide",
        "spoiler": "collapse",
        "information": "show",
        "humor": "show",
        "emotional_resonance": "show",
        "political": "show",
        "other": "show",
    },
    "density": {"enabled": False},
}
CONDITION_NAMES = {
    "A": "A - all comments on (Bilibili default, no filtering)",
    "B": "B - platform keyword blocking (five blocked words, no semantic model)",
    "C": "C - prototype semantic filter (hidden: attack, conflict, inflammatory bait, irrelevant repetition; spoiler folded and recoverable)",
}
LOW_DENSITY_PER_MIN = 30
WINDOW_S = 10

def djb2(text: str) -> int:
    value = 5381
    for char in text:
        value = ((value * 33) + ord(char)) & 0xFFFFFFFF
    return value

def load_stimuli() -> dict[str, dict]:
    report = json.loads((REPO / "data" / "study" / "report.json").read_text(encoding="utf-8"))
    return {row["bvid"]: row for row in report["stimuli"]}

def load_model(bvid: str) -> LinearTagger:
    path = REPO / "data" / "study" / "models" / (bvid + ".json")
    if not path.exists():
        path = REPO / "data" / "study" / "models" / "_global.json"
    return LinearTagger.load(path)

def load_pool(bvid: str) -> list[dict]:
    payload = json.loads((REPO / "data" / "tags" / (bvid + ".json")).read_text(encoding="utf-8"))
    items = payload.get("items") or {}
    rows = []
    for dmid, item in items.items():
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        rows.append({"dmid": str(dmid), "text": text, "time_ms": int(item.get("time_ms") or 0)})
    rows.sort(key=lambda row: row["time_ms"])
    return rows

def label_pool(rows: list[dict], model: LinearTagger) -> None:
    scores = model.predict_scores([row["text"] for row in rows])
    for row, row_scores in zip(rows, scores):
        label = max(row_scores.items(), key=lambda pair: pair[1])[0]
        row["label"] = label
        row["score"] = row_scores[label]
        row["tags"] = row_scores

def thin(rows: list[dict], duration_s: float) -> tuple[list[dict], list[dict]]:
    """Deterministically drop to LOW_DENSITY_PER_MIN using stable dmid hashes."""
    if duration_s <= 0:
        return rows, []
    windows = max(1, int(round(duration_s / WINDOW_S)))
    per_window = max(1, int(round(LOW_DENSITY_PER_MIN * WINDOW_S / 60.0)))
    buckets: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        index = min(windows - 1, max(0, int(row["time_ms"] / 1000 / WINDOW_S)))
        buckets[index].append(row)
    kept, dropped = [], []
    for index in range(windows):
        bucket = sorted(buckets.get(index, []), key=lambda row: djb2(row["dmid"]))
        exempt = [row for row in bucket if row["label"] in ("information", "other")]
        rest = [row for row in bucket if row["label"] not in ("information", "other")]
        keep_rest = max(0, per_window - 0)
        chosen = rest[:keep_rest]
        kept.extend(exempt + chosen)
        dropped.extend(rest[keep_rest:])
    kept.sort(key=lambda row: row["time_ms"])
    dropped.sort(key=lambda row: row["time_ms"])
    return kept, dropped

def apply_condition(rows: list[dict], condition: str) -> list[dict]:
    """Apply one display setting; A and D are pass-through, D is the inert sham."""
    out = []
    for row in rows:
        row = dict(row)
        row["action"] = "show"
        row["reason"] = ""
        if condition == "B":
            if any(word in row["text"] for word in KEYWORDS):
                row["action"] = "hide"
                row["reason"] = "platform keyword"
        elif condition == "C":
            decision = decide(row, PROTOTYPE_PREFS)
            row["action"] = decision["action"]
            row["reason"] = decision["reason"]
        out.append(row)
    return out


def mmss(ms: int) -> str:
    seconds = max(0, int(ms // 1000))
    return "%02d:%02d" % (seconds // 60, seconds % 60)

def sample(rows: list[dict], count: int, seed: str) -> list[dict]:
    if len(rows) <= count:
        return rows
    step = len(rows) / float(count)
    picked = []
    for index in range(count):
        offset = int(index * step + djb2(seed + str(index)) % max(1, int(step)))
        picked.append(rows[min(len(rows) - 1, offset)])
    return picked

def block_markdown(row: dict, stimulus: dict, pool: dict, condition: str) -> str:
    shown = [item for item in pool["items"] if item["action"] == "show"]
    folded = [item for item in pool["items"] if item["action"] == "collapse"]
    hidden = [item for item in pool["items"] if item["action"] == "hide"]
    has_panel = condition in {"C", "D"}
    has_capsule = condition in {"C", "D"}
    duration = int(stimulus.get("duration_s") or 0)
    rate = (len(shown) / (duration / 60.0)) if duration else 0.0
    lines = []
    lines.append("# Block material: %s, block %s" % (row["participant"], row["block"]))
    lines.append("")
    lines.append("## Video")
    lines.append("- bvid: %s" % row["bvid"])
    lines.append("- title (Chinese): %s" % stimulus["title"])
    lines.append("- type: %s (%s)" % (row["genre"], stimulus.get("label", "")))
    lines.append("- duration: %d seconds" % duration)
    lines.append("- density cell: %s (original pool %d comments)" % (row["density"], pool["pool_total"]))
    lines.append("- screenshots: %s and %s" % (pool["frames"][0], pool["frames"][1]))
    lines.append("")
    lines.append("## What you see in this block")
    lines.append("这一块只描述**你在屏幕上看得到的东西**。研究不会告诉你这块用的是哪种设置，")
    lines.append("请只根据你看到的界面和弹幕作答，不要猜测组别，也不要在 notes 里写组别。")
    lines.append("")
    lines.append("- 屏幕上是否出现「过滤控制台」面板：%s" % ("有" if has_panel else "没有"))
    lines.append("- 加载时是否出现「正在整理弹幕池」的进度提示：%s" % ("有" if has_capsule else "没有"))
    lines.append("- 弹幕池里原本有：%d 条" % pool["pool_total"])
    if pool["thinned"]:
        lines.append("- 这一块的密度格在播放前抽掉了一部分弹幕：%d 条没有出现（你看到的密度约 %.0f 条/分钟）" % (pool["thinned"], rate))
    lines.append("- 你实际看到的弹幕：%d 条" % len(shown))
    if has_panel:
        lines.append("- 面板统计显示：显示 %d / 折叠 %d / 隐藏 %d" % (len(shown), len(folded), len(hidden)))
    unavailable = len(hidden) + pool["thinned"]
    if unavailable:
        lines.append("- 始终没有在屏幕上出现的弹幕：%d 条（你看不到它们的原文）" % unavailable)
    lines.append("")
    has_time = any(int(item.get("time_ms") or 0) for item in shown)
    lines.append("## Examples of comments you see%s" % (" (time, text)" if has_time else " (text)"))
    for item in sample(shown, 14, row["participant"] + row["bvid"] + condition):
        if has_time:
            lines.append("- %s  %s" % (mmss(item["time_ms"]), item["text"][:60]))
        else:
            lines.append("- %s" % item["text"][:60])
    lines.append("")
    if folded:
        lines.append("## Comments in the panel folded list (one click shows them)")
        for item in sample(folded, 8, row["participant"] + "fold"):
            if has_time:
                lines.append("- %s  %s" % (mmss(item["time_ms"]), item["text"][:60]))
            else:
                lines.append("- %s" % item["text"][:60])
        lines.append("")
    lines.append("## Your reaction")
    lines.append("按 PROTOCOL.md 的题面逐题给 1-7 的整数分；先想清楚「以我这个人格，在这一块里")
    lines.append("看到了什么」，再给分。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-sheet", default=str(RUN_SHEET))
    parser.add_argument("--out", default=str(BLOCKS))
    args = parser.parse_args()

    import csv
    rows = list(csv.DictReader(Path(args.run_sheet).read_text(encoding="utf-8").splitlines()))
    stimuli = load_stimuli()
    out_dir = Path(args.out)
    pools: dict[str, list[dict]] = {}
    models: dict[str, LinearTagger] = {}
    packets = []
    for row in rows:
        bvid = row["bvid"]
        if bvid not in pools:
            pools[bvid] = load_pool(bvid)
            models[bvid] = load_model(bvid)
            label_pool(pools[bvid], models[bvid])
        stimulus = stimuli[bvid]
        duration = float(stimulus.get("duration_s") or 0)
        pool_rows = pools[bvid]
        thinned_rows: list[dict] = []
        if row["density"] == "low":
            pool_rows, thinned_rows = thin(pool_rows, duration)
        decided = apply_condition(pool_rows, row["condition"])
        counts = Counter(item["action"] for item in decided)
        frames = [str(FRAMES / (bvid + "-1.png")), str(FRAMES / (bvid + "-2.png"))]
        pool = {
            "pool_total": len(pools[bvid]),
            "thinned": len(thinned_rows),
            "items": decided,
            "frames": frames,
        }
        directory = out_dir / row["participant"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("block%s-%s.md" % (row["block"], bvid))
        path.write_text(block_markdown(row, stimulus, pool, row["condition"]), encoding="utf-8")
        packets.append({
            "participant": row["participant"],
            "block": int(row["block"]),
            "condition": row["condition"],
            "condition_order": int(row["condition_order"]),
            "bvid": bvid,
            "genre": row["genre"],
            "density": row["density"],
            "study_id": row["study_id"],
            "material": str(path),
            "counts": {
                "pool_total": len(pools[bvid]),
                "thinned": len(thinned_rows),
                "shown": counts.get("show", 0),
                "folded": counts.get("collapse", 0),
                "hidden": counts.get("hide", 0),
            },
        })
    index = defaultdict(list)
    for packet in packets:
        index[packet["participant"]].append(packet)
    for participant, entries in index.items():
        entries.sort(key=lambda entry: entry["block"])
        # The participant-facing index must stay blind: no condition letter, and
        # the study id only carries the block number.
        blind = []
        for entry in entries:
            item = dict(entry)
            item.pop("condition", None)
            item.pop("condition_order", None)
            item["study_id"] = "%s-%d" % (participant, item["block"])
            blind.append(item)
        (out_dir / (participant + ".index.json")).write_text(
            json.dumps(blind, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {participant: entries for participant, entries in index.items()}
    (AGENTS / "materials-index.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("blocks:", len(packets), "participants:", len(index))
    for participant, entries in sorted(index.items()):
        print(participant, [(entry["block"], entry["condition"], entry["bvid"], entry["counts"]["shown"], entry["counts"]["folded"], entry["counts"]["hidden"]) for entry in entries])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

