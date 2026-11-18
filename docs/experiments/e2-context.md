# E2 实验：视频上下文特征对线性打标器的跨域增益

> 目的：量化把「环境」（视频标题 / 分区 / 时间位置 / 相邻弹幕）注入词面线性打标器后，跨视频（LOVO）标注能带来多少提升。当前 LOVO 基线约 0.32–0.44（agreement）。
>
> 结论先行：**没有增益，全部变体都显著劣于 text-only V0**。平均 agreement：V0 0.3296，V1（+标题）0.2567（−7.29 pp），V2（+标题+分区）0.2635（−6.61 pp），V3（+标题+位置桶）0.2801（−4.95 pp），V4（+标题+前后弹幕）0.2590（−7.06 pp）。原因不是 idf 实现 bug，而是结构性的：在固定 L2 归一化的 hashed TF-IDF 里，把长上下文字符串拼进输入，会让上下文占输入 tf-idf 质量的 78–84%（实测 V1 为 0.785），而 held-out 视频的上下文又在 fit 语料中 OOV（df=0），于是逐条弹幕的信号被稀释、上下文变成一个「视频内常数偏置」，预测塌缩到少数标签。**不要在当前冻结契约下把视频上下文拼进词面模型输入。**
>
> 产物：`data/experiments/results/context.json`（结构化结果）、本文件。运行脚本：`scripts/experiments/run_context.py`（约 6 秒跑完）。

## 1. 数据与协议

数据：`data/experiments/dataset.jsonl`（Lead 于 2026-11-04T18:02:16Z 冻结，2457 行、6 个视频，sha256 前缀 `b182f3cd`）。标签全部为 deepseek-flash teacher 给的 `dominant`（`label_source=llm`），非人工金标。

| 视频 | domain | 标题 | 行数 | 主要标签（计数） |
|---|---|---|---|---|
| BV1GJ411x7h7 | music/meme | 【官方 MV】Never Gonna Give You Up - Rick Astley | 1407 | humor 902, emotional_resonance 233, information 200, irrelevant_repetition 65 |
| BV1w5xh6sEEz | music/meme | 【只对你有感觉】\|“【恋爱の小曲】” | 173 | humor 66, emotional_resonance 45, information 43 |
| BV1rCxe6wEN9 | politics | 一条视频看10月世界：中东在燃烧，法国在骚乱，中国网友在跨国吵架 | 472 | information 206, irrelevant_repetition 84, humor 63, political 53 |
| BV1bnxr6cEJ9 | politics/conflict | 外网中德骂战！10月31号 再现神推：中国人用梯子是买动物园门票 | 67 | humor 15, political 13, information 10 |
| BV1mV4F6mE5Z | gaming | 八倍AK致敬吃鸡，队友脸色对了 | 174 | humor 69, information 52, emotional_resonance 25 |
| BV1W18Y61Ee5 | story/drama | 我妈妈第一次怀孕时…… | 164 | humor 60, emotional_resonance 47, information 22 |

- 协议：leave-one-video-out，每个视频当一次测试集，其余 5 个视频训练；训练集按固定随机种子降采样到 ≤2000 行（只有 BV1GJ411x7h7 折触发：n_train=1050，其余折 1985–2000）。
- 模型：`danmaku_filter.classify.linear.LinearTagger(kind="centroid")`，冻结的 hashed char 1–3 gram TF-IDF（dim=2^18，L2 归一化），没有改契约、没有 class_weight。
- 特征变体（上下文字符串拼到 `text` 之后，整体走同一个 vectorizer）：
  - V0：仅 text；V1：text + 视频标题；V2：text + 标题 + domain；V3：text + 标题 + 位置桶（`order / 该视频总条数` → 5 桶）；V4：text + 标题 + 前一条 + 后一条弹幕（各截断 20 字）。
  - 补充变体（用来分离「标题」与其它上下文通道，避免结论只归因于标题）：S2：text + domain；S3：text + 位置桶；S4：text + 前后弹幕。
- 指标：
  - **agreement**：`dominant` 与预测标签逐条一致率（每折算，跨视频取算术平均；另附按行数加权的 `weighted`，但主指标用算术平均，见 §6）。
  - **macro-F1**：`danmaku_filter.metrics.prf` 的 per-label F1 宏平均（按文本 join，重复文本取最后一条预测）。
  - **tag MAE**：预测分数向量与 gold `tags` 向量在共有标签上的平均绝对误差（gold 来自 teacher 的标签分数）。
- 复现：`cd /home/alphayang/bili-tagging && python3 scripts/experiments/run_context.py`。

## 2. 主结果（冻结数据集，6 折）

### 2.1 dominant agreement

| 视频 | 行数 | V0 | V1 | V2 | V3 | V4 |
|---|---|---|---|---|---|---|
| BV1GJ411x7h7 | 1407 | 0.4805 | 0.6411 | 0.4264 | 0.6517 | 0.6446 |
| BV1w5xh6sEEz | 173 | 0.3584 | 0.1561 | 0.3410 | 0.2254 | 0.2139 |
| BV1rCxe6wEN9 | 472 | 0.1992 | 0.1123 | 0.1144 | 0.1038 | 0.0975 |
| BV1bnxr6cEJ9 | 67 | 0.3134 | 0.2239 | 0.2090 | 0.1940 | 0.2090 |
| BV1mV4F6mE5Z | 174 | 0.3276 | 0.1322 | 0.1609 | 0.2069 | 0.1207 |
| BV1W18Y61Ee5 | 164 | 0.2988 | 0.2744 | 0.3293 | 0.2988 | 0.2683 |
| **跨视频平均** | 2457 | **0.3296** | **0.2567** | **0.2635** | **0.2801** | **0.2590** |
| **Δ vs V0（pp）** | | +0.00 | **−7.29** | **−6.61** | **−4.95** | **−7.06** |
| 行数加权 | 2457 | 0.3904 | 0.4335 | 0.3292 | 0.4489 | 0.4351 |

注意：BV1GJ411x7h7 折上 V1/V3/V4 的 agreement 反而升高（0.48→0.64/0.65），这是**塌缩到该视频多数类 humor（占 64%）** 的假象，不是上下文真的带来了信息；同位置 macro-F1 从 0.1408 掉到 0.0881/0.1038/0.0930（见 2.2），且其预测 98%/95%/97% 都是 humor（见 §3 诊断）。

### 2.2 macro-F1

| 视频 | V0 | V1 | V2 | V3 | V4 |
|---|---|---|---|---|---|
| BV1GJ411x7h7 | 0.1408 | 0.0881 | 0.0925 | 0.1038 | 0.0930 |
| BV1w5xh6sEEz | 0.1335 | 0.0425 | 0.0827 | 0.0605 | 0.0449 |
| BV1rCxe6wEN9 | 0.1018 | 0.0366 | 0.0436 | 0.0299 | 0.0286 |
| BV1bnxr6cEJ9 | 0.1786 | 0.1231 | 0.0545 | 0.1036 | 0.0843 |
| BV1mV4F6mE5Z | 0.1164 | 0.0683 | 0.0541 | 0.1153 | 0.0478 |
| BV1W18Y61Ee5 | 0.1307 | 0.1239 | 0.1272 | 0.1139 | 0.0797 |
| **跨视频平均** | **0.1336** | **0.0804** | **0.0758** | **0.0878** | **0.0630** |
| **Δ vs V0（pp）** | +0.00 | **−5.32** | **−5.78** | **−4.58** | **−7.06** |

### 2.3 tag MAE（越低越好；共有标签上的平均绝对误差）

| 视频 | V0 | V1 | V2 | V3 | V4 |
|---|---|---|---|---|---|
| BV1GJ411x7h7 | 0.4818 | 0.4881 | 0.4491 | 0.4858 | 0.4838 |
| BV1w5xh6sEEz | 0.2928 | 0.3067 | 0.2816 | 0.3031 | 0.3045 |
| BV1rCxe6wEN9 | 0.3386 | 0.3495 | 0.3500 | 0.3482 | 0.3468 |
| BV1bnxr6cEJ9 | 0.4334 | 0.4441 | 0.4405 | 0.4442 | 0.4439 |
| BV1mV4F6mE5Z | 0.2800 | 0.2933 | 0.2894 | 0.2903 | 0.2915 |
| BV1W18Y61Ee5 | 0.3538 | 0.3680 | 0.3609 | 0.3643 | 0.3643 |
| **跨视频平均** | **0.3634** | 0.3749 | 0.3619 | 0.3726 | 0.3725 |

MAE 几乎不动（±0.012），因为分数向量本身很平（每行 max prob 只有 0.11–0.21），光看 MAE 会漏掉 agreement/macro-F1 的塌缩。

### 2.4 补充变体（分离上下文通道；不含标题）

| 变体 | 上下文 | agreement 平均 | Δ vs V0 | macro-F1 平均 | Δ vs V0 |
|---|---|---|---|---|---|
| S2 | text + domain | 0.2569 | −7.27 pp | 0.0748 | −5.88 pp |
| S3 | text + 位置桶 | 0.3058 | −2.38 pp | 0.1129 | −2.07 pp |
| S4 | text + 前后弹幕 | 0.2578 | −7.18 pp | 0.0753 | −5.83 pp |

逐视频 agreement（供对照）：

| 视频 | V0 | S2 | S3 | S4 |
|---|---|---|---|---|
| BV1GJ411x7h7 | 0.4805 | 0.3156 | 0.4485 | 0.4662 |
| BV1w5xh6sEEz | 0.3584 | 0.3988 | 0.2948 | 0.2832 |
| BV1rCxe6wEN9 | 0.1992 | 0.1102 | 0.1695 | 0.1483 |
| BV1bnxr6cEJ9 | 0.3134 | 0.1940 | 0.2836 | 0.1493 |
| BV1mV4F6mE5Z | 0.3276 | 0.3218 | 0.3276 | 0.2011 |
| BV1W18Y61Ee5 | 0.2988 | 0.2012 | 0.3110 | 0.2988 |
| **跨视频平均** | **0.3296** | **0.2569** | **0.3058** | **0.2578** |

即：不是「标题」特有的问题，任何附加文本都掉点；只有很短的常数 token（位置桶）损失较小，但也**没有任何正收益**。

### 2.5 与平凡基线的对比

训练集多数类在该测试视频上的占比（`train_majority_baseline`）跨视频平均 0.2739。V0 0.3296 比平凡基线高约 +5.6 pp（模型确实学到了东西）；V1（0.2567）、V2（0.2635）、V4（0.2590）都**低于**平凡基线，V3（0.2801）只高 0.6 pp。上下文变体的「收益」在统计上不存在。

## 3. 诊断：预测为什么塌缩

`context.json` 每折都存了预测诊断（`top_pred_label`、`top_pred_share`、`distinct_pred_labels`、`mean_max_prob`、`test_majority_share`、`train_majority_baseline`）。关键对比（每折 top 预测的占比 / 不同预测标签数）：

| 视频 | V0 top@share / distinct | V1 top@share / distinct | 测试多数类占比 |
|---|---|---|---|
| BV1GJ411x7h7 | humor@0.48 / 9 | humor@0.98 / 4 | 0.64 |
| BV1w5xh6sEEz | humor@0.45 / 8 | emotional_resonance@0.51 / 6 | 0.38 |
| BV1rCxe6wEN9 | humor@0.60 / 9 | political@0.97 / 2 | 0.44 |
| BV1bnxr6cEJ9 | humor@0.24 / 8 | political@0.61 / 4 | 0.22 |
| BV1mV4F6mE5Z | humor@0.60 / 7 | emotional_resonance@0.35 / 10 | 0.40 |
| BV1W18Y61Ee5 | humor@0.37 / 8 | emotional_resonance@0.66 / 9 | 0.37 |

含标题的变体在 6 个视频里有 3 个（含最大视频）把 92–99% 的行预测成同一个标签，不同预测标签数从 7–9 掉到 2–4；其余视频 top share 也明显抬升（0.35–0.66）。而 V0 的预测分布分散（top 0.24–0.60、7–9 个标签），说明逐条弹幕的信号在上下文变体里基本被抹掉了。

### 机制（在 BV1GJ411x7h7 折上用同一模型做了三组离线诊断）

1. **上下文占掉了输入的大部分质量。** 冻结的 hashed TF-IDF 对整段输入做 L2 归一化；把上下文拼进去后输入长度约为纯弹幕的 2–6 倍。用该折模型的 idf 精确计算（逐 gram 的 `tf*idf` 平方和）得上下文质量占比：V1 **0.785**、V2 0.820、V3 0.800、V4 0.843；S2 0.492、S3 0.276、S4 0.644。逐条弹幕只剩约 16–22% 的质量（V1–V4）。
2. **held-out 视频的上下文在 fit 语料里是 OOV，方向由哈希碰撞决定，而且在视频内是常数。** 该折训练语料 n_docs=1050、df>0 的桶 16206 个；OOV 桶的 fallback idf = ln(1+1050)+1 ≈ 7.96，训练中已见桶的 idf 中位数 ≈ 7.26（因为弹幕短、多数桶 df=1）。fallback 只比中位数高约 10%，**idf 值不是主因**；真正的问题是这部分质量对应的方向在空间里基本是随机的、且同一视频的每一行都一样。
3. **分类器 margin 很小，常数偏置足以决定全部预测。** centroid 的 cosine 分数普遍很平（每行 max prob 0.11–0.21），逐条弹幕带来的分数差通常只有 ±0.0x；一个视频内恒定的上下文分量会把所有行往同一方向推，于是 argmax 几乎全被同一个标签占据。

对照实验（同折、同训练集）：

- 把 OOV 桶的 idf 从 7.96 强行改成已见桶中位数 7.26 后重打分：agreement 0.6091（原 0.6411），top 仍是 humor（1204/1407 = 86%）——**塌缩未被修复**，进一步说明不是 fallback idf 数值的问题。
- 把测试标题替换成一条训练集里的标题（让上下文全部落在已见词表内）：1407 行**全部**预测成 emotional_resonance，agreement 0.1656——即使上下文是 in-vocabulary，常数分量照样压过弹幕信号。

## 4. 结论

1. **在当前冻结特征契约下，「把视频上下文拼进文本输入」跨视频不可行。** V1/V2/V3/V4 的跨视频平均 agreement 分别比 V0 低 7.29 / 6.61 / 4.95 / 7.06 pp，macro-F1 低 4.58–7.06 pp；没有一个变体在平均意义上超过 V0。
2. **表观的提升是多数类假象，必须用宏 F1/预测分布复核。** BV1GJ411x7h7 折 V1 agreement 0.6411 恰好等于该视频 humor 占比 0.64，对应宏 F1 0.0881（远低于 V0 0.1408）、98% 的行预测为 humor。只看 agreement 会被这种塌缩骗到。
3. **根因是「长 + 视频内恒定 + 测试时 OOV」三件事共同作用，而不是 idf 阈值 bug。** L2 归一化按输入长度分配质量（上下文占 78–84%），held-out 上下文不在 fit 词表内（方向约等于哈希碰撞噪声、视频内恒定），小 margin 的分类器于是输出常数标签；把 idf 改小、把标题换成训练标题都无法修复（§3 对照）。补充变体 S2/S3/S4 说明这不是标题特有：domain、相邻弹幕同样掉点，只有位置桶（最短常数 token）损失最小且仍无收益。
4. **如果后续还要用上下文，必须换融合方式，不能拼字符串。** 可选方向：(a) 训练时把上下文放进 fit 语料，让 df>0——这只能用于「同视频先 bootstrap/自适应再打标」的协议（本 E2 的 LOVO 协议外推不了）；(b) 上下文作为独立特征通道，用固定小权重与 comment 通道的分数相加（不经过同一个 L2 归一化），或用视频级主题先验对类别先验做校准；(c) 把 vectorizer 拆成 comment-only 与 context-only 两路，再在分数层融合。无论哪条，都需要单独的消融验证，不能默认「拼进去就有用」。
5. **对生产的含义。** 线性模型定位是离线快速回填/降级路径，且 LLM 打标本身已经看过视频标题/上下文；因此线性模型应保持 text-only，视频上下文交给 policy 层或 LLM，不要塞进词面特征。跨视频 LOVO 基线 0.32–0.44 就是当前词面模型的上限区间，提升要靠更多同域数据或更强的语义特征，而不是拼接上下文。

## 5. 产物与复现

`data/experiments/results/context.json` 结构：

- `experiment`、`created`、`dataset`（行数、sha256、每视频的行数/dominant 分布、observed_labels、macro_f1_labels）；
- `config`（协议、tagger、vectorizer、max_train_rows、seed、position_buckets、neighbor_chars、各变体定义）；
- `results.<bvid>.<variant>`：每折每变体的 `n_test`、`n_train`、`train_capped`、`train_labels`、`unseen_test_rows`、`agreement`、`agreement_ceiling`、`macro_f1`、`per_label`、`tag_mae`、`tag_covered_rows`、`tag_pair_count`、`diagnostics`；
- `supplementary.<bvid>.<S2|S3|S4>`：同上；
- `aggregate` / `supplementary_aggregate`：跨视频平均/行数加权，以及相对 V0 的 pp 差。

复现（约 6 秒，纯标准库）：

~~~bash
cd /home/alphayang/bili-tagging
python3 scripts/experiments/run_context.py
~~~

## 6. 局限

- 只有 6 个视频、2457 行；其中 2 个测试视频不足 70 行，单折方差大；BV1GJ411x7h7 占总行数 57%，所以「算术平均」和「按行加权」差异很大（行数加权下 V1 0.4335 反而高于 V0 0.3904，因为该折塌缩恰好押中 humor），主结论以跨视频算术平均为准。
- 标签是 teacher（deepseek-flash）给的，不是人工金标；teacher 与线性模型共享词面偏差会同时影响参照系。
- 只测了 centroid 一种头；softmax 头在 LOVO 上略强（0.44 vs 0.32），但两者共享同一个 vectorizer 的归一化问题，预计结论方向一致（未在本次数据上重跑，属于外推）。
- 只测了一种「拼字符串」的融合方式；§4 的替代融合方向没有做实验，不能据此断言上下文信息本身无用——只能断言**经冻结 TF-IDF 的 L2 归一化拼接后无用**。
- agreement ceiling 在 5 折为 1.0（训练集覆盖测试集全部标签），BV1GJ411x7h7 折为 0.9964（仅 5 行 spoiler 是训练未见标签）；政治类标签基本只出现在政治类视频里，跨域不可学是数据本身的限制，不随上下文特征改变。
- 上下文质量占比 0.785 等数字是在 BV1GJ411x7h7 折、用该折模型的 idf 精确算的（未计哈希碰撞交叉项，误差可忽略）；其它折未逐一重复，但输入长度结构相同。
