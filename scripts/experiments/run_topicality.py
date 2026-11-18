"""E3: is "is this comment about this video?" solvable zero-training with bge-m3?

Probe: encode each video's (title + domain) as an environment vector, score every
comment by cosine(comment, environment), and measure ROC-AUC against 180 hand
labels (30 comments per video across 6 videos). Compare two cheap baselines over the same items:

* char-bigram overlap between comment and title (containment coefficient)
* TF-IDF cosine between comment and title (char bigrams, idf from all comments)

External validity: use the frozen teacher labels as a noisy topicality proxy,
treating dominant in {"other", "irrelevant_repetition"} as negatives and score
with 1 - cosine(comment, environment).

Only the standard library, numpy and the local Ollama endpoint are used; the
script never writes outside data/experiments/topicality and
data/experiments/results.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "data" / "experiments" / "dataset.jsonl"
DEFAULT_GOLD_DIR = ROOT / "data" / "experiments" / "topicality"
DEFAULT_RESULT = ROOT / "data" / "experiments" / "results" / "topicality.json"

SEED = 20261105
PER_VIDEO = 30
EMBED_URL = "http://127.0.0.1:11434/api/embed"
EMBED_MODEL = "bge-m3"
BATCH_SIZE = 64
EXTERNAL_NEGATIVE_LABELS = {"other", "irrelevant_repetition"}

# Hand labels authored by the single annotator for the deterministic sample
# below: {bvid: {order: (label, reason)}}. 1 = about this video's content,
# subject, visuals, audio or author; 0 = generic reaction / unrelated chat.
MANUAL_LABELS: dict[str, dict[int, tuple[int, str]]] = {
    "BV1GJ411x7h7": {
        54: (0, "泛化反应，未指向画面或歌词"),
        98: (1, "直接评价歌曲本身"),
        138: (1, "表示专程来看本视频"),
        201: (1, "对歌曲上瘾的直接反应"),
        263: (0, "无关的 Daft Punk 玩笑"),
        309: (1, "评价歌曲越听越上瘾"),
        333: (0, "无关的 CS:GO 玩笑"),
        347: (0, "无关的 GTA5 玩笑"),
        348: (0, "无关的 Cytus 3 玩笑"),
        377: (1, "指涉画面中 Rick 的笑容"),
        380: (1, "指涉视频 BGM"),
        549: (0, "无关动画梗"),
        608: (0, "语义不明"),
        629: (0, "无关歌手玩梗"),
        640: (1, "评价视频翻译字幕"),
        690: (0, "泛化“开头见”重看反应"),
        773: (1, "评价视频翻译字幕"),
        840: (1, "复制的视频字幕数据行"),
        867: (1, "含歌词译文的视频字幕行"),
        934: (1, "指涉 Rick 笑容的治愈感"),
        978: (0, "无关的绝地求生玩笑"),
        982: (0, "无关动画假预告"),
        994: (0, "无关电影假预告"),
        1146: (0, "语义不明"),
        1155: (0, "无关的 GTA5 玩笑"),
        1238: (1, "关于 Rick 本人入驻 B 站"),
        1259: (1, "指涉视频中的歌曲旋律"),
        1296: (0, "无关淫梦梗"),
        1357: (0, "无关游戏假预告"),
        1395: (0, "无关的来源提示"),
    },
    "BV1w5xh6sEEz": {
        6: (0, "语义不明"),
        12: (0, "无关的个人处境闲聊"),
        17: (1, "评价画面中人物的腕力"),
        25: (1, "评价画面中展示的操作"),
        32: (0, "无法确认指向视频内容"),
        38: (1, "解读画面中两人的互动"),
        41: (0, "泛化代入感叹"),
        43: (1, "指涉画面中的宿伞之魂形象"),
        47: (1, "评价画面人物外貌"),
        68: (0, "无法确认指向视频内容"),
        76: (1, "评价画面人物的优雅气质"),
        78: (0, "颜文字刷屏"),
        80: (0, "泛化困惑"),
        86: (1, "指出视频的倒放手法"),
        96: (1, "评价画面中球员的强度"),
        105: (1, "指涉画面中的雨伞"),
        108: (1, "评价画面人物气质"),
        116: (1, "应援画面中的角色火花花"),
        122: (1, "评价画面人物神态"),
        124: (1, "由歌曲引发的具体回忆"),
        143: (1, "评价画面人物气质"),
        144: (1, "与叶罗丽角色作具体视觉比较"),
        149: (0, "语义不明的大风刷屏"),
        154: (1, "评价画面中的男女两人"),
        157: (1, "应援画面中的角色火花花"),
        158: (1, "指涉画面歌曲与游戏角色"),
        162: (1, "评价画面人物气质"),
        165: (1, "用火影梗描述画面动作"),
        166: (0, "泛化“可爱”感叹"),
        170: (0, "泛化自我指涉"),
    },
    "BV1rCxe6wEN9": {
        13: (0, "抢首评"),
        50: (1, "讨论视频中的美国工资与物价"),
        65: (1, "感慨视频中的国际局势"),
        77: (1, "讨论视频中的法国骚乱"),
        86: (1, "讨论视频中的选举议题"),
        87: (1, "评价视频中国际局势的走向"),
        94: (1, "讨论视频中的电车与油价"),
        137: (0, "无意义字符"),
        152: (1, "讨论视频中的 A 股下跌"),
        157: (1, "讨论视频中的 A 股下跌"),
        160: (0, "无关玩梗"),
        172: (0, "无关玩笑"),
        210: (0, "泛化“笑了”反应"),
        216: (0, "泛化嘲讽"),
        233: (1, "补充讨论视频中的美国油价"),
        309: (1, "讨论视频中的法国骚乱"),
        314: (1, "讨论视频中的油价与新能源货车"),
        324: (0, "泛化笑声"),
        339: (1, "讨论视频中的 A 股下跌"),
        348: (1, "讨论视频中的法国骚乱"),
        382: (1, "讨论视频中的 A 股下跌"),
        383: (0, "泛化口号"),
        389: (1, "指涉视频的月刊形式"),
        403: (1, "讨论视频中的美国物价"),
        404: (1, "讨论视频中的东南亚诈骗园区"),
        409: (1, "评论画面中主播的唇色"),
        431: (0, "无意义字符"),
        444: (1, "讨论视频中的投票日期"),
        445: (0, "无意义字符"),
        459: (1, "讨论视频中的选举结果"),
    },
    "BV1bnxr6cEJ9": {
        1: (1, "讨论视频中的德国土耳其人身份"),
        3: (0, "泛化网络用语"),
        4: (0, "泛化笑声"),
        10: (0, "泛化笑声"),
        11: (1, "讨论视频是否给出原贴"),
        12: (1, "对视频内容表示困惑"),
        16: (1, "讨论视频是否由 AI 生成"),
        17: (1, "讨论视频中的原贴与截图"),
        19: (0, "无意义字符"),
        20: (1, "讨论视频呈现的外网乱象"),
        21: (1, "讨论视频中的原贴位置"),
        27: (0, "泛化笑声"),
        29: (0, "围观式玩梗"),
        30: (1, "讨论视频中的原贴与截图"),
        31: (1, "讨论视频中的外网平台话题"),
        36: (0, "无关的如厕玩笑"),
        38: (1, "表示通过视频记住了国旗"),
        39: (1, "讨论视频中的翻墙话题"),
        40: (0, "无关的机器人玩梗"),
        42: (1, "讨论视频中的国旗记忆点"),
        47: (1, "对视频中人物或言行表示佩服"),
        48: (1, "讨论视频中德国人身份质疑"),
        52: (1, "评论视频中人物的刻薄言行"),
        54: (1, "讨论视频中“遮挡”是否大方"),
        55: (1, "讨论视频中的中外处境差异"),
        56: (0, "泛化笑场"),
        57: (1, "讨论视频中的欧洲局势"),
        60: (1, "讨论视频中的 AI 语音"),
        63: (0, "泛化围观玩梗"),
        65: (0, "泛化围观玩梗"),
    },
    "BV1mV4F6mE5Z": {
        6: (0, "泛化笑声"),
        12: (1, "对视频中游戏行为表示不满"),
        17: (1, "讨论视频中的玩家与角色"),
        25: (1, "讨论视频中的队友与角色"),
        32: (1, "形容视频中的枪声与画面震动"),
        38: (0, "泛化笑声"),
        41: (1, "解析视频中的笑点"),
        43: (1, "讨论视频中的角色梗"),
        47: (0, "泛化“在现场”玩梗"),
        68: (1, "讨论视频中的角色与实力"),
        76: (0, "泛化不敢看"),
        78: (1, "讨论视频中的队友与角色"),
        80: (1, "讨论视频中的角色"),
        86: (1, "讨论视频中的游戏道具"),
        96: (1, "讨论视频标题中的八倍 AK"),
        105: (1, "讨论视频中的游戏操作"),
        108: (1, "讨论视频中的角色表现"),
        116: (0, "泛化“救命”"),
        122: (1, "讨论视频中的游戏操作"),
        124: (0, "无关地名玩梗"),
        143: (1, "讨论视频中的游戏操作"),
        144: (1, "讨论视频中的游戏操作细节"),
        150: (1, "讨论视频中的游戏道具"),
        154: (1, "讨论视频中的角色梗"),
        157: (1, "讨论视频中的创作者与角色"),
        159: (0, "语义不明玩梗"),
        162: (1, "讨论视频中的饮酒画面"),
        163: (1, "讨论视频中出场的创作者"),
        166: (0, "泛化文雅感叹"),
        171: (1, "指涉视频系列“齐家杯”"),
    },
    "BV1W18Y61Ee5": {
        6: (0, "无意义字符"),
        12: (1, "评价视频中的吃鱼情节"),
        17: (1, "质疑视频故事的真实性"),
        25: (1, "由视频情节联想到自身经历"),
        28: (1, "评价视频中鱼的做法"),
        32: (1, "就视频故事逻辑发问"),
        38: (1, "评价视频的纯爱主题"),
        41: (1, "评价视频的母爱主题"),
        43: (0, "无关游戏梗"),
        47: (1, "评价视频的母爱主题"),
        68: (1, "指出视频开场的问题"),
        76: (1, "评价视频的纯爱主题"),
        78: (1, "讨论视频的重看体验"),
        80: (1, "就视频故事情节吐槽"),
        86: (1, "对视频内容表示困惑"),
        96: (0, "无意义字符"),
        105: (1, "由视频情节联想到自身经历"),
        108: (1, "评论视频故事的反转"),
        110: (1, "评论视频故事的真相"),
        116: (1, "评论视频故事的真相"),
        122: (1, "评论视频故事的反转"),
        124: (1, "由视频中的鱼汤联想到自身经历"),
        127: (0, "泛化困惑"),
        142: (1, "评价视频的纯爱主题"),
        147: (1, "评论视频中的配偶行为"),
        151: (1, "总结视频主角"),
        154: (1, "由视频情节联想到母亲"),
        157: (0, "泛化感叹"),
        158: (1, "评论视频故事中的父亲"),
        161: (1, "指涉视频故事真相"),
    },
}


def load_dataset(path: Path) -> dict[str, list[dict]]:
    """Group dataset rows per video, ordered by their original order field."""
    videos: dict[str, list[dict]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            videos.setdefault(row["bvid"], []).append(row)
    for rows in videos.values():
        rows.sort(key=lambda r: int(r["order"]))
    return videos


def sample_orders(rows: list[dict], per_video: int, seed: int) -> list[int]:
    """Deterministically sample comment orders for one video."""
    rng = random.Random(seed)
    n = min(per_video, len(rows))
    return sorted(rng.sample(range(len(rows)), n))


def environment_text(title: str, domain: str) -> str:
    """Build the text encoded as the video environment vector."""
    return f"{title}\n领域：{domain}"


def post_json(url: str, payload: dict, timeout: float = 180.0) -> dict:
    """POST JSON and decode the JSON response."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def embed_texts(texts: list[str], url: str, model: str, batch_size: int) -> np.ndarray:
    """Embed texts with a local Ollama model, returning an (n, dim) float array."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                data = post_json(url, {"model": model, "input": batch})
                vectors.extend(data["embeddings"])
                break
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
                last_error = exc
                time.sleep(1.0 + attempt)
        else:
            raise RuntimeError(f"embedding failed: {last_error}")
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] != len(texts):
        raise RuntimeError(f"unexpected embedding shape: {matrix.shape}")
    return matrix


def cosine_scores(matrix: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Cosine similarity between every row of matrix and a single reference."""
    matrix_norm = np.linalg.norm(matrix, axis=1)
    reference_norm = float(np.linalg.norm(reference))
    denominator = np.clip(matrix_norm * reference_norm, 1e-12, None)
    return matrix @ reference / denominator


def roc_auc(labels: Iterable[int], scores: Iterable[float]) -> float:
    """Rank-based ROC-AUC with average ranks for ties; raises on one class."""
    pairs = sorted(zip(scores, labels), key=lambda pair: pair[0])
    n_pos = sum(1 for _, label in pairs if label == 1)
    n_neg = len(pairs) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise ValueError("ROC-AUC needs both classes")
    ranks = [0.0] * len(pairs)
    index = 0
    while index < len(pairs):
        end = index
        while end + 1 < len(pairs) and pairs[end + 1][0] == pairs[index][0]:
            end += 1
        average_rank = (index + end) / 2.0 + 1.0
        for slot in range(index, end + 1):
            ranks[slot] = average_rank
        index = end + 1
    positive_rank_sum = sum(
        rank for rank, (_, label) in zip(ranks, pairs) if label == 1
    )
    return (positive_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def bootstrap_auc(
    labels: list[int], scores: list[float], rounds: int = 2000, seed: int = 7
) -> tuple[float, float]:
    """Percentile 95% confidence interval for ROC-AUC."""
    rng = random.Random(seed)
    values: list[float] = []
    n = len(labels)
    for _ in range(rounds):
        indices = [rng.randrange(n) for _ in range(n)]
        sample_labels = [labels[i] for i in indices]
        if len(set(sample_labels)) < 2:
            continue
        values.append(roc_auc(sample_labels, [scores[i] for i in indices]))
    if not values:
        return float("nan"), float("nan")
    values.sort()
    low = values[max(0, int(0.025 * len(values)) - 1)]
    high = values[min(len(values) - 1, int(0.975 * len(values)))]
    return low, high


def char_bigrams(text: str) -> set[str]:
    """Character bigrams ignoring whitespace."""
    clean = "".join(text.split())
    return {clean[i:i + 2] for i in range(len(clean) - 1)}


def bigram_overlap(comment: str, title: str) -> float:
    """Containment of the comment's bigrams in the title's bigrams."""
    comment_bigrams = char_bigrams(comment)
    if not comment_bigrams:
        return 0.0
    title_bigrams = char_bigrams(title)
    return len(comment_bigrams & title_bigrams) / len(comment_bigrams)


class TfidfSpace:
    """Character-bigram TF-IDF cosine over the comment corpus."""

    def __init__(self, documents: list[str]) -> None:
        self.n_docs = len(documents)
        document_counts: Counter[str] = Counter()
        for document in documents:
            document_counts.update(set(char_bigrams(document)))
        self.idf = {
            gram: math.log((self.n_docs + 1) / (count + 1)) + 1.0
            for gram, count in document_counts.items()
        }

    def vector(self, text: str) -> dict[str, float]:
        """Sparse tf-idf vector of a text."""
        counts = Counter(char_bigrams(text))
        return {
            gram: count * self.idf.get(gram, math.log(self.n_docs + 1) + 1.0)
            for gram, count in counts.items()
        }

    @staticmethod
    def cosine(left: dict[str, float], right: dict[str, float]) -> float:
        """Cosine between two sparse vectors."""
        if len(left) > len(right):
            left, right = right, left
        dot = sum(value * right.get(gram, 0.0) for gram, value in left.items())
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / (left_norm * right_norm)


def build_sample(
    videos: dict[str, list[dict]], per_video: int, seed: int
) -> tuple[list[dict], list[dict]]:
    """Deterministically sample comments and attach the hand labels."""
    gold: list[dict] = []
    missing: list[str] = []
    for bvid, rows in videos.items():
        title = rows[0]["title"]
        for order in sample_orders(rows, per_video, seed):
            row = rows[order]
            label_reason = MANUAL_LABELS.get(bvid, {}).get(int(row["order"]))
            if label_reason is None:
                missing.append(f"{bvid}#{row['order']}")
                continue
            label, reason = label_reason
            gold.append(
                {
                    "bvid": bvid,
                    "order": int(row["order"]),
                    "text": row["text"],
                    "title": title,
                    "label": label,
                    "reason": reason,
                }
            )
    if missing:
        raise RuntimeError(f"missing hand labels for {len(missing)} sampled rows: {missing[:5]}")
    return gold, []


def dump_sample(videos: dict[str, list[dict]], per_video: int, seed: int) -> None:
    """Print the deterministic annotation sample as JSON lines."""
    for bvid, rows in videos.items():
        title = rows[0]["title"]
        domain = rows[0]["domain"]
        for index in sample_orders(rows, per_video, seed):
            row = rows[index]
            print(json.dumps(
                {
                    "bvid": bvid,
                    "order": int(row["order"]),
                    "title": title,
                    "domain": domain,
                    "text": row["text"],
                },
                ensure_ascii=False,
            ))


def pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation; 0.0 when either input has no variance."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / n
    var_x = sum((x - mean_x) ** 2 for x in xs) / n
    var_y = sum((y - mean_y) ** 2 for y in ys) / n
    if var_x == 0.0 or var_y == 0.0:
        return 0.0
    return cov / math.sqrt(var_x * var_y)


def score_group(
    labels: list[int], scores: list[float], bootstrap: bool = False, strict: bool = True
) -> dict:
    """Summarize one (labels, scores) pair; None AUC when a class is missing."""
    result: dict = {
        "n": len(labels),
        "n_positive": sum(1 for label in labels if label == 1),
        "n_negative": sum(1 for label in labels if label == 0),
    }
    try:
        result["auc"] = roc_auc(labels, scores)
    except ValueError:
        if strict:
            raise
        result["auc"] = None
        return result
    if bootstrap:
        low, high = bootstrap_auc(labels, scores)
        result["ci95"] = [low, high]
    return result


def run(
    dataset_path: Path,
    gold_dir: Path,
    result_path: Path,
    skip_external: bool,
    per_video: int = PER_VIDEO,
) -> dict:
    """Run the whole experiment and write gold + results files."""
    videos = load_dataset(dataset_path)
    gold, _ = build_sample(videos, per_video, SEED)
    gold_dir.mkdir(parents=True, exist_ok=True)
    gold_path = gold_dir / "gold.json"
    gold_path.write_text(
        json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    env_texts = [
        environment_text(rows[0]["title"], rows[0]["domain"])
        for rows in videos.values()
    ]
    all_comment_texts = [row["text"] for rows in videos.values() for row in rows]
    unique_comments = list(dict.fromkeys(all_comment_texts))

    started = time.time()
    env_matrix = embed_texts(env_texts, EMBED_URL, EMBED_MODEL, BATCH_SIZE)
    comment_matrix = embed_texts(unique_comments, EMBED_URL, EMBED_MODEL, BATCH_SIZE)
    embed_seconds = round(time.time() - started, 2)
    comment_index = {text: i for i, text in enumerate(unique_comments)}

    # Exploratory probe: a zero-training environment vector built from the
    # video's own comments (leave-one-out centroid). Not pre-registered; a
    # deployable fallback when the title is short or uninformative.
    video_comment_sums: dict[str, np.ndarray] = {}
    for bvid, rows in videos.items():
        matrix = comment_matrix[[comment_index[row["text"]] for row in rows]]
        video_comment_sums[bvid] = matrix.sum(axis=0)

    def loo_centroid_score(bvid: str, text: str) -> float:
        """Cosine to the mean comment embedding, excluding the comment itself."""
        vector = comment_matrix[comment_index[text]]
        total = video_comment_sums[bvid] - vector
        centroid = total / max(float(np.linalg.norm(total)), 1e-12)
        return float(cosine_scores(vector[None, :], centroid)[0])

    sample_scores: list[float] = []
    bigram_scores: list[float] = []
    tfidf_scores: list[float] = []
    centroid_scores: list[float] = []
    combined_scores: list[float] = []
    tfidf = TfidfSpace(all_comment_texts)
    video_index = {bvid: i for i, bvid in enumerate(videos)}
    title_vector = {
        bvid: tfidf.vector(rows[0]["title"]) for bvid, rows in videos.items()
    }
    per_video_scores: dict[str, dict[str, list[float]]] = {
        bvid: {"env": [], "bigram": [], "tfidf": [], "centroid": [], "combined": [], "labels": []}
        for bvid in videos
    }

    for item in gold:
        bvid = item["bvid"]
        environment = env_matrix[video_index[bvid]]
        vector = comment_matrix[comment_index[item["text"]]]
        env_score = float(cosine_scores(vector[None, :], environment)[0])
        bigram = bigram_overlap(item["text"], item["title"])
        tfidf_score = TfidfSpace.cosine(tfidf.vector(item["text"]), title_vector[bvid])
        centroid_score = loo_centroid_score(bvid, item["text"])
        combined_score = (env_score + centroid_score) / 2.0
        sample_scores.append(env_score)
        bigram_scores.append(bigram)
        tfidf_scores.append(tfidf_score)
        centroid_scores.append(centroid_score)
        combined_scores.append(combined_score)
        bucket = per_video_scores[bvid]
        bucket["env"].append(env_score)
        bucket["bigram"].append(bigram)
        bucket["tfidf"].append(tfidf_score)
        bucket["centroid"].append(centroid_score)
        bucket["combined"].append(combined_score)
        bucket["labels"].append(item["label"])

    labels = [item["label"] for item in gold]
    hand_labels = {
        "overall": score_group(labels, sample_scores, bootstrap=True),
        "per_video": {},
    }
    baselines = {
        "char_bigram_overlap": {
            "overall": score_group(labels, bigram_scores, bootstrap=True),
            "per_video": {},
        },
        "tfidf_cosine_title": {
            "overall": score_group(labels, tfidf_scores, bootstrap=True),
            "per_video": {},
        },
    }
    for bvid, bucket in per_video_scores.items():
        hand_labels["per_video"][bvid] = score_group(
            bucket["labels"], bucket["env"], strict=False
        )
        for name, key in (
            ("char_bigram_overlap", "bigram"),
            ("tfidf_cosine_title", "tfidf"),
        ):
            baselines[name]["per_video"][bvid] = score_group(
                bucket["labels"], bucket[key], strict=False
            )

    exploratory = {
        "note": (
            "not pre-registered; the leave-one-out comment centroid uses no "
            "labels and is deployable from the segment bytes themselves"
        ),
        "comment_centroid_loo": {
            "overall": score_group(labels, centroid_scores, bootstrap=True),
            "per_video": {
                bvid: score_group(bucket["labels"], bucket["centroid"], strict=False)
                for bvid, bucket in per_video_scores.items()
            },
        },
        "title_plus_comment_centroid": {
            "overall": score_group(labels, combined_scores, bootstrap=True),
            "per_video": {
                bvid: score_group(bucket["labels"], bucket["combined"], strict=False)
                for bvid, bucket in per_video_scores.items()
            },
        },
    }

    dataset_info = {
        "path": str(dataset_path.relative_to(ROOT)),
        "rows": len(all_comment_texts),
        "videos": {
            bvid: {
                "title": rows[0]["title"],
                "domain": rows[0]["domain"],
                "n_rows": len(rows),
            }
            for bvid, rows in videos.items()
        },
    }

    result: dict = {
        "dataset": dataset_info,
        "experiment": "e3-topicality",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "embedding": {
            "model": EMBED_MODEL,
            "endpoint": EMBED_URL,
            "dim": int(env_matrix.shape[1]),
            "environment_text": "title + '\n领域：' + domain",
            "embed_seconds": embed_seconds,
            "n_texts": len(env_texts) + len(unique_comments),
        },
        "annotation": {
            "seed": SEED,
            "per_video": per_video,
            "n_videos": len(videos),
            "n_samples": len(gold),
            "label_definition": "1=about this video's content/subject/visuals/audio/author, 0=generic reaction or unrelated chat",
            "annotators": 1,
            "gold_path": str(gold_path.relative_to(ROOT)),
            "label_counts": {
                "positive": sum(1 for label in labels if label == 1),
                "negative": sum(1 for label in labels if label == 0),
            },
        },
        "hand_labels": hand_labels,
        "hand_label_diagnostics": {
            "env_score_mean_positive": statistics.mean(
                score for score, label in zip(sample_scores, labels) if label == 1
            ),
            "env_score_mean_negative": statistics.mean(
                score for score, label in zip(sample_scores, labels) if label == 0
            ),
            "env_score_min": min(sample_scores),
            "env_score_median": statistics.median(sample_scores),
            "env_score_max": max(sample_scores),
            "env_score_length_pearson": pearson(
                sample_scores, [float(len(item["text"])) for item in gold]
            ),
            "n_tied_env_scores": len(sample_scores) - len(set(sample_scores)),
        },
        "baselines": baselines,
        "exploratory_probes": exploratory,
        "conclusion": {
            "note": "primary metric is the overall hand-label ROC-AUC; baselines use the same sampled items",
        },
    }

    if not skip_external:
        external_labels: list[int] = []
        probe_scores: dict[str, list[float]] = {
            "title_env": [],
            "comment_centroid_loo": [],
            "title_plus_comment_centroid": [],
        }
        external_per_video: dict[str, dict[str, list]] = {
            bvid: {"labels": [], **{name: [] for name in probe_scores}}
            for bvid in videos
        }
        for bvid, rows in videos.items():
            environment = env_matrix[video_index[bvid]]
            for row in rows:
                text = row["text"]
                vector = comment_matrix[comment_index[text]]
                env_cos = float(cosine_scores(vector[None, :], environment)[0])
                centroid_cos = loo_centroid_score(bvid, text)
                values = {
                    "title_env": 1.0 - env_cos,
                    "comment_centroid_loo": 1.0 - centroid_cos,
                    "title_plus_comment_centroid": 1.0 - (env_cos + centroid_cos) / 2.0,
                }
                label = 0 if row.get("dominant") in EXTERNAL_NEGATIVE_LABELS else 1
                external_labels.append(label)
                external_per_video[bvid]["labels"].append(label)
                for name, value in values.items():
                    probe_scores[name].append(value)
                    external_per_video[bvid][name].append(value)
        result["external_validity"] = {
            "definition": (
                "negative=dominant in {other, irrelevant_repetition}, "
                "positive=everything else, score=1-cosine"
            ),
            "negative_labels": sorted(EXTERNAL_NEGATIVE_LABELS),
            "probes": {
                name: {
                    "overall": score_group(external_labels, values, bootstrap=True),
                    "per_video": {
                        bvid: score_group(
                            bucket["labels"], bucket[name], strict=False
                        )
                        for bvid, bucket in external_per_video.items()
                    },
                }
                for name, values in probe_scores.items()
            },
        }

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    """Parse arguments, run the experiment and print the headline metrics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--skip-external", action="store_true")
    parser.add_argument("--per-video", type=int, default=PER_VIDEO)
    parser.add_argument(
        "--dump-sample",
        action="store_true",
        help="print the deterministic annotation sample as JSON lines and exit",
    )
    args = parser.parse_args()
    if args.dump_sample:
        dump_sample(load_dataset(args.dataset), args.per_video, SEED)
        return
    result = run(
        args.dataset, args.gold_dir, args.result, args.skip_external, args.per_video
    )
    print(json.dumps(
        {
            "n_samples": result["annotation"]["n_samples"],
            "hand_label_auc": result["hand_labels"]["overall"],
            "baselines": {
                name: data["overall"] for name, data in result["baselines"].items()
            },
            "exploratory_probes": {
                name: data["overall"]
                for name, data in result["exploratory_probes"].items()
                if isinstance(data, dict) and "overall" in data
            },
            "external_validity": {
                name: data["overall"]
                for name, data in result.get("external_validity", {})
                .get("probes", {})
                .items()
            },
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
