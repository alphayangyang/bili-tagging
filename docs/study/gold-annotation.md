# S1 gold set：120 条单标注金标（data/study/gold.jsonl）

本文件记录 S2 实验里 RQ2 金标的构造方式、分布、与 teacher 的差异，以及如何把它扩展成两人双标。

## 1. 为什么需要这份金标

- RQ2 要比的是「小模型 vs 人」而不是「小模型 vs 大模型」。没有人工金标时，analyze.py 的 RQ2 只能给出 teacher 一致率，不能报 per-tag P/R/F1。
- 原计划 D11–D12 做 180 条两人独立标注 + Krippendorff α；双人标注未落实，所以先做 120 条单标注版本，把管线和指标跑通，并在报告里如实标注单标注者局限。

## 2. 抽样

| 项 | 值 |
|---|---|
| 来源池 | data/study/teacher_sample.jsonl（6 支刺激视频各 117–197 条 time 分层抽样） |
| 抽样方式 | 每支视频按其时间顺序等距取 20 条（step = len // 20），6 支共 120 条 |
| 盲标 | 标注时不看 teacher 的 dominant/score，只看文本与时间 |
| 标注表 | data/study/gold_sheet.jsonl（bvid/text/time_ms，无标签） |
| 标注人 | lead（1 人），按 docs/taxonomy.md v0.3/v0.4 的判定顺序标注 dominant |
| 产出 | data/study/gold.jsonl，每行 bvid/text/time_ms/label/annotator |

## 3. 标签分布

| 标签 | 条数 | 占比 |
|---|---|---|
| humor | 38 | 31.7% |
| other | 34 | 28.3% |
| information | 21 | 17.5% |
| emotional_resonance | 15 | 12.5% |
| attack | 6 | 5.0% |
| irrelevant_repetition | 4 | 3.3% |
| conflict | 2 | 1.7% |

other 偏多是真实情况：刺激视频里大量碎片弹幕（「哦对了」「月」「？？？」）没有可命名的社会功能，按 v0.4 规则应记为 other，而不是硬塞给 humor。

## 4. 与 teacher（deepseek-flash）的差异

- 可对齐 120 条，dominant 一致率 0.575。
- 主要混淆：teacher 把 情感共鸣 判成 other 8 次、把 information 判成 other 7 次、把 ER 判成 information 6 次、把 conflict 判成 other 5 次、把 humor 判成 other 5 次；反向看，标注者把 4 条 teacher 判 IR 的标成 humor、3 条 teacher 判 information 的标成 humor。
- 读法：差异集中在「碎片弹幕要不要给功能标签」以及 humor/ER/information 三者边界，与 E8 边界实验的结论一致（幽默吸引子）。这不是标注错误，而是任务本身在边界上不确定；正式实验若做双标，α 预计在 .55–.70 区间，需要报告并做分歧讨论。
- 诊断（不是信度）：把 teacher 当作「第二标注者」跑同一条 --gold-double 路径，得到 n=120、agreement 0.575、名义型 Krippendorff α = 0.467。这是人-模型一致性的下限参考；真人-真人 α 应高于它，但仍受同一批边界案例拖累。

## 5. 用这份金标评 per-video 线性模型

命令（与正式实验收尾一致）：

  python3 scripts/study/analyze.py --raw-dir data/study/raw --questionnaire data/study/questionnaire.csv --out data/study/analysis.json --markdown data/study/analysis.md --gold data/study/gold.jsonl --models-dir data/study/models

当前结果（120 条，模型 = data/study/models 的 6 个 per-video LinearTagger）：

| 指标 | 值 |
|---|---|
| 覆盖 | 120/120（skipped 0） |
| dominant 准确率 | 0.558 |
| macro-F1 | 0.496 |

| 标签 | precision | recall | F1 | support |
|---|---|---|---|---|
| humor | 0.763 | 0.763 | 0.763 | 38 |
| other | 1.000 | 0.147 | 0.256 | 34 |
| information | 0.520 | 0.619 | 0.565 | 21 |
| emotional_resonance | 0.423 | 0.733 | 0.537 | 15 |
| attack | 0.667 | 1.000 | 0.800 | 6 |
| irrelevant_repetition | 0.111 | 0.250 | 0.154 | 4 |
| conflict | 0.250 | 1.000 | 0.400 | 2 |

读法：

- other 的 precision 1.000 但 recall 0.147：线性模型几乎从不预测 other（它总把碎片分到某个功能标签），这是「other 作为兜底」在设计上的代价，也是报告里必须写的局限。
- attack/conflict recall 1.0 但 support 只有 6/2，不能据此宣称风险类标签可靠。
- humor/information 是模型的主战场；ER precision 偏低（0.423）说明模型把不少非 ER 判成 ER，与 teacher 的幽默吸引子方向相反。

计算口径：dominant 准确率 = argmax 预测标签与 gold label 一致的比例；macro-F1 在「预测标签 ∪ gold 标签」上逐标签算 F1 再取平均（预测到但 gold 没有的标签也计入、support 记 0），所以模型输出 taxonomy 之外的标签会拉低 macro-F1，复核时看 per_tag 的 support 列判断。

## 6. 局限

- 单标注者，无 Krippendorff α；金标本身带 lead 的判定偏好（尤其 other 的使用）。
- 每支视频 20 条、按时间等距，不是随机抽样；对高频重复文本略有高估。
- 标签分布偏 humor/other，information 只有 21 条、conflict 2 条，per-tag 指标对小类不稳定。
- 与 teacher 一致率 0.575 不能当作标注质量指标（teacher 不是人）。

## 7. 怎么扩展成两人双标（正式版）

1. 另找一名组员，独立标 gold_sheet.jsonl（不看 gold.jsonl、不看 teacher 标签）；
2. 生成 data/study/gold_double.jsonl，每行保留 bvid/text，并写 label_a / label_b 两个字段；
3. 跑 analyze.py 时加 --gold-double data/study/gold_double.jsonl，会输出 agreement 与 Krippendorff α（目标 ≥ .67，≥ .80 为好）。α 用名义型公式（Krippendorff 2011），期望分歧按 N(N−1) 做有限样本修正；analyze.py 有 4 组与参考实现对齐的回归值，不修正会系统性低估 α；
4. 分歧条目开会讨论，形成最终 gold.jsonl（讨论后的标签写回 label 字段），报告里同时给讨论前 α 与讨论后金标。

行格式示例（JSONL，每行一个对象）：

  {"bvid": "BV1Ho4y1D7Wz", "text": "...", "label_a": "humor", "label_b": "emotional_resonance"}

## 8. 复现

  # gold.jsonl 由 gold_sheet.jsonl 与本文档 §3 的标签列表一次性生成，生成后即作为金标入库；
  # 如需重标，改动 gold.jsonl 的 label 字段（不要改 text/bvid），再跑：
  python3 scripts/study/analyze.py --raw-dir data/study/raw --questionnaire data/study/questionnaire.csv --out data/study/analysis.json --markdown data/study/analysis.md --gold data/study/gold.jsonl --models-dir data/study/models