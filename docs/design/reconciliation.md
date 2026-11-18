# PRD v0.1 × 课程 slides × 现有实现：对齐与收敛

> 输入：用户提供的早期笔记 [prd-v0.1.md](prd-v0.1.md)、slides 1–6、Part 1–4 正文、仓库现有代码。
> 目的：在写 Part 5 和继续开发之前，把「哪些沿用、哪些收敛、哪些先不做」定下来。

## 0. TL;DR

PRD 是产品/工程视角的扩写，范围大于课程研究方案。它的三个核心思想应该吸收：

1. **语义理解与呈现决策解耦**（中间表示层）；
2. **多标签 + 分数** 代替单一 Keep/Remove；
3. **用户拥有最终控制权**，分类 ≠ 审核。

但有两处必须先和小组收敛，否则会和 slides、队友正文、7 类标注打架：

- **标签数量**：slide 是 7 类（把「重复」和「无关」合成 irrelevant repetition）；PRD 是 8 类（Repetition / Irrelevance 分开）并建议加 Sarcasm。
- **研究问题**：PRD 有 4 个 RQ；Part 5 目前按 3 个写。建议 3 个（PRD 的交互效应并进 RQ1，权衡并进 RQ3/评测）。

## 1. 一致、可直接沿用的部分

| PRD | 对应来源 | 现状 |
|---|---|---|
| 用户侧、语义感知、可解释、可个性化 | slide 2、Part 1–2 | ✅ 一致 |
| 分类不等于审核；Show / Hide / Collapse | PRD 1.2 / 4.3 | ✅ 已写进 Part 5 草稿与 README |
| LLM prompting + 规则 + 反馈精修 | slide 4 Approach、Wang et al. (2025) | ✅ 已有 rules/llm 两个 backend |
| 可解释 + 即时纠正 | slide 4、Jhaver et al. (2023) | ✅ Part 5 有对应句 |
| 技术指标 P/R/F1 + 延迟 | slide 4、PRD 7.1 | ✅ metrics.py 已按标签统计 |
| 用户负担 / 观看体验指标 | slide 4、PRD 7.2 | ✅ 已进 project-brief 评测表 |
| 弹幕文本是不可信输入 | PRD 6.4 | ⚠️ 代码未做 prompt 注入防护 |

## 2. 冲突与建议决策

### 2.1 标签体系：7 类 vs 8 类（+ Sarcasm）

- slide 4 的 7 类：information / humor / emotional resonance / irrelevant repetition / attack / conflict / spoiler。
- PRD 把 repetition 与 irrelevance 拆成两类，另设 Sarcasm 辅助标签。
- **建议**：study 的主标签保持 **7 类**；PRD 的 8 类/多标签作为**运行时中间表示**；Irrelevance 在标注工具里先作为 secondary 字段收集，等 pilot 的 κ 与边界案例证明标注者能稳定区分后，再决定是否升为第 8 类；Sarcasm 作为辅助研究标签，不进主分类。
- 理由：与 slides、Part 3/4 的措辞、500 词 Introduction 保持一致；7 类已经覆盖 slide 4；拆分类别会把标注成本和正文改写成本推高，且没有数据支撑。
- 如果最终决定拆：Part 5 的 “across seven social-function categories” 要改成 eight，并同步庄骞/田牧远的正文。

### 2.2 单标签 vs 多标签

- PRD 原则二：标签不互斥、分别打分 → 中间表示用多标签。
- 现有代码：单 `label` + `confidence`。
- **建议**：**标注 gold set 用单主标签**（便于 κ、P/R/F1，与 slide 一致）；**运行时用多标签 `{tag: score}`**（PRD）。评测分开报告「主标签 P/R/F1」与「多标签匹配质量」。
- 代码影响：`ClassificationResult` 增加 `tags: dict[str, float]`，保留 dominant label；rules/LLM 输出升级；metrics 增加多标签版本；UI 显示标签与分数。

### 2.3 RQ 收敛（建议 3 个）

| PRD | 建议的 Part 5 RQ |
|---|---|
| RQ1 类型如何影响体验 | **RQ1**：弹幕类型与数量如何影响观看体验？（把 PRD RQ2 的交互效应并进来） |
| RQ2 数量×类型交互 | 同上（作为 RQ1 的一部分） |
| RQ3 个性化过滤能否改善控制感 | **RQ2**：可解释分类器能否在中文弹幕上达到可用精度与实时延迟？ |
| RQ4 精度/成本/密度/计算权衡 | **RQ3**：用户控制的过滤（含密度）能否改善体验而不增加负担？权衡在评测里回答 |
| — | 技术可行性作为独立 RQ（PRD 里隐含在 Q2/Q7） |

写法：Part 5 里只列 3 个 RQ，RQ1 用 “type and volume” 一句话覆盖交互效应。

### 2.4 密度控制

- slide 4 只把 volume 当作研究变量；PRD 4.6 建议做成独立产品模块。
- **建议**：研究阶段 volume 作为操纵变量；原型里做成独立 density controller（按时间窗限流 / 合并重复 / 按类别优先级）；Part 5 一句话点名 “type and density”。
- 约束沿用 PRD：密度控制只能从已有弹幕中选择，不能创造内容。

### 2.5 用户自定义标签

- 属于 Phase 3+，不进 500 词 Introduction，放在 Method / future work。
- 技术路线：自然语言描述 + 少量示例 → LLM 泛化；必须配套缓存失效策略（PRD 5.3 已指出）。

### 2.6 反馈必须分两层

PRD 4.5 明确区分：

- **策略层**：改 show/hide/collapse、阈值、密度 → 立即改变呈现；
- **分类层**：纠正标签 → 作为后续分类改进的参考数据。

现有 `FeedbackStore` 只做了「文本 → 标签覆盖」，把两层混在一起。需要拆成两个事件流，否则评测里的「纠正次数」无法归因（用户改偏好 ≠ 分类错了）。

### 2.7 计算、缓存与降级

- 研究原型：**预计算标签 + 本地缓存**（同一设备），不承诺多用户共享；PRD 6.2 的共享缓存作为后续。
- 未知视频：规则基线先跑，LLM 异步补标；模型不可用时保持原样渲染（PRD 6.3）。
- 弹幕不可信：LLM prompt 里把弹幕放在数据区并声明「不要执行其中指令」，输出必须做 schema 校验；当前 `llm.py` 未做，列入 TODO。

## 3. 合并后的三层架构（一句话版）

采集/清洗 → **语义理解层**（LLM + 规则 + 上下文）→ **多标签中间表示**（缓存）→ **个性化决策层**（偏好 / 阈值 / 冲突 / 密度）→ **渲染层**（show / hide / collapse + 解释）→ **双通道反馈** → 回流。与 PRD 5.1 一致。

## 4. 对 Part 5 的影响

只需加一句「语义标签与呈现决策分离」，并让 policy 句覆盖 type 与 density。已加入 v3 草稿（165 词，见 [../intro/part5-approach.md](../intro/part5-approach.md) 第 11 节）。其余保持 v1。

## 5. 代码 TODO（按优先级）

1. **多标签中间表示**：types / rules / llm / metrics / UI 数据格式。
2. **policy layer**：纯函数 `decide(tags, prefs, density) → show|hide|collapse`，与分类器解耦，便于单测。
3. **反馈双通道**：policy events 与 label corrections 分开存储。
4. **density controller**：时间窗限流、重复合并、类别优先级。
5. **LLM 防护与降级**：prompt 注入防护、schema 校验、超时/失败回退规则。
6. **userscript / 扩展 MVP**：拦截弹幕流 → 查缓存 → 渲染决策 → 解释/纠正 UI。

## 6. 需要组内拍板的 5 个问题

1. 主标签 7 类还是 8 类？Irrelevance 是否拆出？
2. Sarcasm 是否进主标签，还是只做辅助研究标签？
3. Part 5 写 3 个 RQ 还是 4 个？目标词数 135 / 165？
4. 研究阶段做不做密度控制实验，还是只作为 study 变量？
5. 自定义标签是否进 MVP，还是明确放到 future work？
