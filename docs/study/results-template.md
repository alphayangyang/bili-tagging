# S2 结果报告模板（填 analysis.json 即可）

用法：正式数据收齐后跑

  python3 scripts/study/analyze.py --raw-dir data/study/raw --questionnaire data/study/questionnaire.csv --out data/study/analysis.json --markdown data/study/analysis.md --bootstrap 5000 --gold data/study/gold.jsonl --gold-double data/study/gold_double.jsonl --models-dir data/study/models

然后把本文件里的占位符替换成 analysis.json / analysis.md 的对应值。带 ⚠ 的段落是「没有达到条件就不要写」的部分。

## 标题

Filtering danmaku by social function: a controlled viewing study (S2)

## 摘要（150–200 词）

- 目的与 RQ1–RQ3 各一句；
- 被试 N = {cleaning.included_participants}（招募 {recruited}，排除 {cleaning.excluded_rows} 块）；
- 主要结果：C vs A 在 discomfort {contrasts.discomfort 中 C vs A 的估计/CI/p}、control {}、co-viewing {}；
- RQ2：per-tag macro-F1 {rq2.gold.macro_f1}、延迟 p50 {rq2.latency_overall.p50_ms} ms；
- 结论一句 + 局限一句（单标注者、单城市、B 站单一平台）。

## 1. 引言（3 段）

1. 弹幕的社会功能与观看体验冲突（引用 Chen 2017 / Wu 2018 / He 2021 / Mu 2025 / Jhaver 2023 / Wang 2025）；
2. 现有平台只有全开/全关/关键词，缺按社会功能与密度的查看者侧控制；
3. 本文用 S2 受控实验检验「类型 × 密度」的体验效应，并报告一个可用原型。

## 2. 方法

### 2.1 设计

- within-subject，3 条件（A 全开 / B 平台关键词 / C 原型）× 每条件 2 支视频；拉丁方平衡 6 种条件顺序；
- 材料：6 支真实 B 站短视频（信息 3 / 娱乐 3，密度高/低配对），见 docs/study/stimuli-selection.md；
- 密度操纵：high = 原样（实测峰值 225–750 条/分），low = 10 秒窗稳定 hash 抽稀到 30 条/分（三条件都执行）。

### 2.2 被试

| 项 | 值 |
|---|---|
| 招募 / 纳入 | {recruited} / {cleaning.included_participants} |
| 年龄（M, SD） | 从基线问卷算 |
| 性别 | 从基线问卷算 |
| 每周 B 站时长中位数 | 从基线问卷算 |
| 弹幕习惯（常看/偶尔/不看） | 从基线问卷算 |

### 2.3 材料与仪器

- 6 支视频与每支的整池条数、峰值密度、标签分布：从 data/study/report.json 的 stimuli 表抄；
- 原型：油猴脚本 v0.4.1 + 本地服务（linear 后端、每视频线性模型 + teacher 兜底），C 条件启动最长等 15 秒；
- 单位：{video}，耳机/音箱，浏览器缩放 100%。

### 2.4 测量

- 不适感 5 题、共看 3 题、控制感 3 题、FOMO 1 题、NASA-TLX 6 题、理解 5 题 MCQ + 自由回忆、可解释性 2 题、overall 2 题；
- Cronbach α 从 {reliability.A} 起抄（< .60 的题在报告里标注）。

### 2.5 分析口径（写方法时抄这一节）

- 条件对比：paired Wilcoxon（正态近似 + ties 校正）+ Holm 校正 3 次（C vs A / C vs B / B vs A）；效应量 Cohen's d（配对）+ 按被试重采样的 bootstrap 95% CI；
- 功效：N=24 对 d ≥ 0.7 的差异有 80%+ 把握（`scripts/study/power_sim.py` 模拟；d = 0.6 时 power ≈ .78）；更小的效应只报效应量与 CI、不宣称显著；
- 金标（RQ2）：dominant 准确率 + macro-F1（按「预测标签 ∪ gold 标签」平均，见 analysis.json 的 macro_f1_note）+ per-tag P/R/F1 与 support；双标注时报名义型 Krippendorff α（有限样本修正）；
- 延迟：会话日志 percentiles（p50/p95），并说明这是浏览器到本地服务的端到端时间，不是 GPU 推理时间。

## 3. 结果

### 3.1 数据清洗

- 问卷 {cleaning.questionnaire_rows} 行、日志 {cleaning.session_logs} 份；纳入 {cleaning.included_rows} 块 / {cleaning.included_participants} 人；排除 {cleaning.excluded_rows} 块（原因见 {cleaning.excluded}）。
- 各条件块数：{cleaning.rows_by_condition}。

### 3.2 描述统计（按条件）

| 结局 | A M(SD) | B M(SD) | C M(SD) |
|---|---|---|---|
| discomfort | {descriptives.by_condition.A.outcomes.discomfort.mean} | | |
| control | | | |
| co-viewing | | | |
| FOMO | | | |
| NASA-TLX | | | |
| 理解正确率 | | | |
| overall | | | |

### 3.3 计划对比（RQ3）

| 对比 | 结局 | 估计 | 95% CI | p（Holm） | 效应量 |
|---|---|---|---|---|---|
| C vs A | discomfort | | | | |
| C vs A | control | | | | |
| C vs B | discomfort | | | | |
| B vs A | discomfort | | | | |

数据来自 {contrasts}；LMM 不可用时的回退见 {lmm.note}。⚠ 若 C vs A 的 p 未过 Holm，结论写成「方向一致但未达显著，效应量 d = …」。

### 3.4 RQ1：类型 × 密度（仅 A 条件）

| genre | density | n_blocks | discomfort | control | FOMO |
|---|---|---|---|---|---|
| information | high | | | | |
| information | low | | | | |
| entertainment | high | | | | |
| entertainment | low | | | | |

数据来自 {rq1.rows}；⚠ 每格只有 1 支视频、n 小，按协议写成探索性描述 + 效应量，不做显著性宣称。

### 3.5 RQ2：分类质量与延迟

- 金标：{rq2.gold.n} 条，dominant 准确率 {rq2.gold.accuracy}，macro-F1 {rq2.gold.macro_f1}，skipped {rq2.gold.skipped_rows}；
- per-tag 表从 {rq2.gold.per_tag} 抄（precision/recall/F1/support）；
- 双标注：{rq2.double_annotation}（⚠ 单标注者时这里写「未做，Krippendorff α 无法估计」，并把 docs/study/gold-annotation.md §6 的局限抄进 4.1）；
- 延迟：p50 {rq2.latency_overall.p50_ms} ms / p95 {rq2.latency_overall.p95_ms} ms（块级），分条件 {rq2.latency}；
- 行为：纠正 {rq2.behaviour.corrections} 次、恢复 {rq2.behaviour.restores} 次、展开 {rq2.behaviour.expands} 次，每 10 分钟纠正 {rq2.behaviour.corrections_per_10min}；在线打标占比 {rq2.online_share}；截断会话 {rq2.truncated_sessions}。

### 3.6 定性

- 主题 1 信任与可解释（引语 1–2 条，匿名 P07）；
- 主题 2 边界争议（哪些弹幕被试认为不该过滤）；
- 主题 3 FOMO 与找回（展开/恢复行为对应的访谈片段）；
- 主题 4 与平台关键词过滤的对比（B vs C 的直观差异）。

## 4. 讨论

### 4.1 主要发现（对应 H1a–H3d）

| 假设 | 结果 | 读法 |
|---|---|---|
| H1a 类型影响舒适/控制 | | |
| H1b 密度影响 | | |
| H1c 类型 × 密度交互 | | ⚠ 探索性 |
| H2 原型优于全开 | | |
| H3a–d 原型 vs 关键词（负担、FOMO、信任、理解） | | |

### 4.2 与先前工作的关系

- 与 Mu 2025（眼动/场依存）对齐：个体差异可能调节 C 的效果；
- 与 Jhaver 2023（可理解、低负担的个性化审核）：本研究的解释与纠错机制是同一诉求的查看者侧版本；
- 与 Wang 2025（LLM prompting 起步快但难持续优化）：本研究的线性模型 + teacher 升级是折中证据。

### 4.3 局限

1. 单标注者金标，边界标签的 P/R/F1 有标注者偏差；
2. 每格 1 支视频，RQ1 只能探索性；
3. 便利样本、单城市、以学生为主；
4. 弹幕池来自公开 API（list.so 上限约 1,200 条），超长视频覆盖不全；
5. 低密度格的 30 条/分是人工抽稀，与自然低密度视频不完全等价；
6. 实验时长与疲劳、条件顺序已平衡但仍有残留。

## 5. 结论

- 一句话结论（类型与密度是否影响体验）+ 一句话产品含义（查看者侧按功能/密度控制可行）+ 一句话未来工作（双标注、跨平台、长期部署）。

## 附录

- A 问卷全文（docs/study/questionnaire.md）；
- B 主试逐字稿（docs/study/session-script.md）；
- C 分析输出（data/study/analysis.json / analysis.md）；
- D 材料选择（docs/study/stimuli-selection.md）；
- E 金标说明（docs/study/gold-annotation.md）。

## 报告前自检

- [ ] 所有数字来自 analysis.json，不手抄错位；
- [ ] 排除标准在结果前写死，不事后改；
- [ ] 显著性措辞与 p 值一致（未过校正不写「显著」）；
- [ ] 单标注者与 RQ1 探索性都在局限里；
- [ ] 匿名引语只写编号，不写姓名/uid；
- [ ] 附录含复现命令与脚本版本（v0.4.1）。