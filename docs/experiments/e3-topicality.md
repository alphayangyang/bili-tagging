# E3 — Is "is this comment about this video?" solvable zero-training with bge-m3?

> Experiment `e3-topicality`, run 2026-11-04T18:05Z on the dataset frozen at 2026-11-05 02:01 (+08).
> Script: `scripts/experiments/run_topicality.py`. Artifacts:
> `data/experiments/topicality/gold.json` (180 hand-labelled items),
> `data/experiments/results/topicality.json` (all metrics + diagnostics).

## 1. Question

The product needs a **topicality axis**: for an arbitrary comment on an arbitrary
video, can we tell whether it is about *this* video (its content, subject,
visuals, audio or author), rather than a generic reaction or unrelated chat?

If a zero-training embedding probe works, unseen videos could get a topicality
signal without labels and without an LLM call per comment. Hypothesis: an
**environment vector** built from the video title (+ domain) and compared by
cosine to the comment embedding gives a topicality score clearly better than
chance and better than cheap lexical title overlap.

## 2. Method

- **Data.** `data/experiments/dataset.jsonl`: 2457 comments from 6 videos
  (frozen teacher labels present but used only for the external-validity probe,
  never for the primary metric).
- **Environment vector.** bge-m3 (Ollama `POST http://127.0.0.1:11434/api/embed`,
  1024-d) embedding of `title + "\n领域：" + domain`. Same model embeds comments
  directly. Score = `cosine(comment, environment)`.
- **Gold labels.** Deterministic sample of 30 comments per video
  (`random.Random(20261105).sample` over each video's rows, sorted by order) →
  180 items, 119 positive / 61 negative. Single annotator; definition:
  1 = about this video's content / subject / visuals / audio / author;
  0 = generic reaction ("哈哈哈哈哈", "来了", "第一") or unrelated chat / other
  videos. Reasons per item are stored in `gold.json`.
- **Baselines** over the same 180 items: (a) character-bigram containment
  between comment and title; (b) character-bigram TF-IDF cosine between comment
  and title, idf over all 2457 comments.
- **Metric.** Rank-based ROC-AUC with average ranks for ties; percentile 95%
  bootstrap CI over 2000 resamples (seed 7); per-video AUC.
- **External validity (free proxy labels).** Treat `dominant \in {other,
  irrelevant_repetition}` as off-topic (241 of 2457 comments) and score with
  `1 - cosine` (zero-training anomaly score), so the AUC reads as
  "does this score rank off-topic comments higher?"
- **Exploratory probes (not pre-registered).** (i) leave-one-out comment
  centroid: the mean embedding of a video's own comments minus the scored
  comment, then cosine — needs no labels and no title; (ii) average of the
  environment score and the centroid score.

## 3. Results

### 3.1 Primary metric (hand labels, n = 180)

| probe | ROC-AUC | 95% CI |
|---|---|---|
| bge-m3 title+domain environment cosine | **0.5705** | [0.4777, 0.6501] |
| baseline: char-bigram containment vs title | 0.5545 | [0.5205, 0.5887] |
| baseline: char-bigram TF-IDF cosine vs title | 0.5550 | [0.5214, 0.5890] |
| exploratory: LOO comment centroid cosine | 0.5553 | [0.4627, 0.6449] |
| exploratory: mean(environment, LOO centroid) | 0.5740 | [0.4788, 0.6614] |

The environment probe's point estimate is above both baselines, but its CI
includes 0.5 (two-sided p ≈ 0.11 from the bootstrap standard error), so the
result **does not establish** a topicality signal for the title-based
environment vector. The lexical baselines sit just above chance with much
tighter CIs because their scores are heavily tied (most comments share no
bigrams with the title); their AUCs are near 0.55 mainly by ranking the few
title-overlapping comments high.

### 3.2 Per-video breakdown (hand labels)

| video | domain | rows | pos/neg in sample | env AUC | bigram AUC | tfidf AUC | LOO-centroid AUC |
|---|---|---|---|---|---|---|---|
| BV1GJ411x7h7 (Rick Astley MV) | music/meme | 1407 | 13/17 | 0.6923 | 0.5385 | 0.5385 | 0.8145 |
| BV1w5xh6sEEz (恋爱の小曲) | music/meme | 173 | 20/10 | 0.4400 | 0.5000 | 0.5000 | 0.6000 |
| BV1rCxe6wEN9 (10月世界) | politics | 472 | 20/10 | 0.5350 | 0.6000 | 0.6000 | 0.3300 |
| BV1bnxr6cEJ9 (中德骂战) | politics/conflict | 67 | 19/11 | 0.4498 | 0.5263 | 0.5311 | 0.5024 |
| BV1mV4F6mE5Z (八倍AK吃鸡) | gaming | 174 | 22/8 | 0.5511 | 0.5682 | 0.5682 | 0.3636 |
| BV1W18Y61Ee5 (妈妈第一次怀孕) | story/drama | 164 | 25/5 | 0.7840 | 0.5800 | 0.5800 | 0.7840 |

Heterogeneity is large and goes in both directions: narration/story and the
music video work (0.69–0.78), while the two meme-heavy videos where on-topic
comments talk about the *persona/meme* rather than the title (恋爱の小曲,
中德骂战) are at or below chance. With 30 items per video, each single-video
AUC has a CI of roughly ±0.18, so only the extremes should be read as signal.

### 3.3 External validity (teacher-label proxy, n = 2457, 241 off-topic)

| probe | ROC-AUC | 95% CI |
|---|---|---|
| environment cosine (1 − cos) | 0.5165 | [0.4796, 0.5536] |
| LOO comment centroid | 0.5528 | [0.5081, 0.5948] |
| mean(environment, centroid) | 0.5380 | [0.4976, 0.5778] |

Per video, the environment probe is 0.42–0.56; the LOO centroid reaches 0.685
on BV1rCxe6wEN9 (news) but 0.295 on BV1W18Y61Ee5 — it captures "what this
comment section is about" extremely well for one video and anti-predicts
another. Only the centroid's overall interval excludes 0.5, and its effect
size (≈ 0.55) is too small to justify a filter axis.

### 3.4 Diagnostics (hand-labelled items)

| diagnostic | value |
|---|---|
| mean env cosine, positive comments | 0.4107 |
| mean env cosine, negative comments | 0.3966 |
| env cosine min / median / max | 0.2570 / 0.3955 / 0.5682 |
| Pearson corr(env cosine, comment char length) | 0.1215 |
| tied env scores | 3 / 180 |

Positives are only 0.014 cosine above negatives, and the whole score range is
compressed to 0.26–0.57 — bge-m3 cosines for short Chinese danmaku are strongly
anisotropic and mostly capture general "chat-ness", not topicality. The mild
positive length correlation (r = 0.12) means longer, more contentful comments
score marginally higher even when they are generic; the effect is too small to
explain the AUCs but is not zero.

## 4. Interpretation

1. **The title+domain environment vector is not a usable topicality axis.**
   On 180 hand labels the AUC is 0.57 with a CI covering 0.5, and on the 2457-row
   external proxy it is 0.52. It is also only marginally above a trivial
   char-bigram overlap baseline.
2. **Why it fails.** Bilibili titles are short, meme-dense and deliberately
   oblique ("【官方 MV】…", "八倍AK致敬吃鸡"); on-topic comments refer to
   personas, scenes, series and inside jokes, not to title tokens. A title is a
   poor summary of what a video is *about* for meme content. bge-m3's anisotropy
   further compresses all comment–title cosines into a narrow band.
3. **The only zero-label signal worth remembering is the comment centroid**, and
   even that is weak and unstable (external AUC 0.553, per-video 0.30–0.69).
   It measures "fits this comment section's dominant topic", which is close to
   topicality by construction, but it cannot distinguish a generic reaction from
   a content-specific one when the generic reaction is also dominant.
4. **Decision for the product:** do not add a title-embedding topicality gate or
   a topicality filter axis. Keep the frozen teacher classifier as the label
   source. If a cheap context feature is ever needed (e.g. de-prioritising
   off-topic comments in unseen videos), the LOO comment centroid is the only
   candidate with evidence above chance, and it should be used at most as a
   weak tie-break, never as a hide/collapse trigger.
5. **Negative result is still useful:** it rules out the cheapest zero-training
   design for the "topicality" dimension before any engineering was invested,
   and it explains why the classifier's `other`/`irrelevant_repetition`
   behaviour should not be approximated by title similarity.

## 5. Limitations

- **Single annotator, 180 labels**; the 1/0 boundary for comments like
  "在现场" or "笑点解析" is subjective, and no inter-annotator agreement (κ) was
  measured. Reasons are stored per item so the labels can be audited.
- **Wide CIs**: 30 items per video gives per-video CIs of about ±0.18; the
  overall CI (±0.086) still overlaps 0.5.
- **External proxy is circular**: the negative class comes from the same hybrid
  classifier whose label quality is being assumed, and
  `other`/`irrelevant_repetition` mean "not one of the six social functions",
  not strictly "off-topic". The 0.55 centroid result may partly reflect
  classifier quirks rather than topicality.
- **Narrow data**: 6 videos, one platform, one time window, 2457 comments;
  results may not transfer to other content types.
- **One embedding model, one environment-text template**; no instruction
  prefixes, no description/tags/transcript context, and no tuning of the cosine
  temperature. A stronger environment vector (e.g. multi-vector title + tags +
  transcript) was not tested.
- **Length confound** is present but small (r = 0.12).

## 6. Reproduce

```bash
# requires local Ollama with bge-m3 and data/experiments/dataset.jsonl
python3 scripts/experiments/run_topicality.py            # ~1 min (30 s embedding)
# inspect / regenerate the annotation sample:
python3 scripts/experiments/run_topicality.py --dump-sample
```

Outputs: `data/experiments/topicality/gold.json` (list of 180 items with
`bvid, order, text, title, label, reason`) and
`data/experiments/results/topicality.json` (dataset, embedding, annotation,
hand_labels, hand_label_diagnostics, baselines, exploratory_probes,
external_validity). The script writes only those two paths.
