# 弹幕分类体系（7 类 social function）

> 这是 **写作、标注、prompt、界面** 共用的定义。代码里的唯一来源是 `danmaku_filter/taxonomy.py`；`config/taxonomy.json` 由它导出。改定义时先改 Python 再重新导出，并同步更新本文档。

## 1. 为什么要按「社会功能」分，而不是按情感极性

同一条弹幕的「正/负」不决定它该不该被过滤。例如：

- 「哈哈哈哈哈这操作」——情绪是正的，但可能是低信息重复，用户想 collapse；
- 「你是不是傻」——情绪是负的，属于 attack；
- 「剧透预警：最后主角死了」——语气中性，却是 spoiler。

所以分类维度是**这条弹幕在观看场景里起什么作用**（social function），而不是它让谁高兴/不高兴。

## 2. 七类定义

| id | 名称 | 定义 | 正例 | 默认处理 |
|---|---|---|---|---|
| information | Information | 补充关于视频的事实/解释：出处、科普、背景、翻译、时间点、画面解释 | 这个 BGM 出自《星际穿越》；注意右下角的时间 | show |
| humor | Humor | 以逗乐为目的、且带有内容的玩笑/梗/神评（不包含纯刷屏） | 这波操作属实是反向带货；导演：我谢谢你 | show |
| emotional_resonance | Emotional resonance | 表达或呼应情绪、共情、认同、共同感受 | 看到这里真的泪目了；太真实了，说的就是我 | show |
| irrelevant_repetition | Irrelevant repetition | 低信息、重复、刷屏式反应：叠字、数字/词语刷屏、「第一」「前排」、无内容的起哄 | 哈哈哈哈哈哈哈；1111；第一；前排打卡 | collapse |
| attack | Attack | 针对个人或群体的侮辱、骚扰、敌意 | up 主就是个傻逼；滚出去吧别恶心人了 | hide |
| conflict | Conflict | 对抗性、升级争吵的回复，含挑衅与贬低式嘲讽，但没有直接辱骂 | 你懂什么就在这里说；别杠了，有本事你来 | hide |
| spoiler | Spoiler | 透露视频/剧情后面的内容，破坏惊喜；需要结合时间点与后续内容判断 | 剧透预警：他最后没活下来；别急，等下就反转了 | hide |

- `other`：标注工具里的兜底类，不属于七类目标标签；评测时单独报告。
- 默认处理只是插件初始值，最终由用户按类别 show / hide / collapse。

## 3. 标注指南（给标注员和 LLM prompt 用）

1. **标注社会功能，不标情感极性**：问「这条弹幕在观看场景里做什么」，而不是「你喜不喜欢它」。
2. **允许上下文**：可以看前后弹幕、视频标题、当前时间点；spoiler 必须结合「后面会发生什么」判断。
3. **只给一个主标签**；如果同时符合两类，按下面的优先级取更「需要被处理」的一类：
   attack > spoiler > conflict > information > emotional_resonance > humor > irrelevant_repetition。
4. **attack vs conflict**：有直接辱骂/人身攻击 → attack；只是立场对抗、嘲讽、抬杠 → conflict。
5. **humor vs irrelevant_repetition**：有内容的笑点/梗 → humor；纯叠字、重复、刷屏 → irrelevant_repetition。
6. **information vs emotional_resonance**：提供可验证信息 → information；只表达感受 → emotional_resonance。
7. **spoiler**：只透露「当前时间点之前」的内容不算；透露之后剧情/结局才算。视频本身是剪辑/解说时，以「是否破坏第一次观看」为准。
8. **other**：确实无法归入任何一类时使用；不要因为「不喜欢」就标成 attack。

## 4. 标注流程建议

- Pilot：100 条（跨 3–5 个视频、不同类别），两人独立标，算 Cohen's κ；目标 ≥ 0.7，边界案例讨论后写进本文档。
- 正式集：600–1000 条，分层抽样（每类 ≥ 60 条）；gold set 与 LLM 分类结果逐条比对。
- 记录：每条弹幕的 text、time_ms、label、标注员、是否边界案例；边界案例单独存一份，用来改 prompt 和规则。

## 5. LLM prompt 约定（在 `danmaku_filter/classify/llm.py` 里）

- 输入：类别定义 + 每类 2–3 个示例 + 待标弹幕（带时间点）。
- 输出：JSON 数组，每项 `{id, label, confidence, rationale}`，label 必须是 `danmaku_filter.taxonomy.CATEGORY_IDS` 之一或 `other`。
- 视频上下文（标题、简介、后续剧情摘要）作为可选字段，尤其用于 spoiler。
- 每批 30–50 条，控制单次请求长度；失败时重试并记录原始响应。

## 6. 边界案例待补

- 攻击性梗（「你行你上」算 conflict 还是 humor？）
- 反讽/阴阳怪气（「可太厉害了」）
- 剧透与预测（「我猜最后会反转」——预测不算剧透，但可能减损体验，是否单列？）
- 引战但无脏话（「不服来辩」→ conflict）
- 字幕/翻译类弹幕（information，但可能覆盖画面）

## 7. 与 PRD v0.1 的对齐：多标签、辅助标签与「分类 ≠ 审核」

本节说明 7 类标注体系与产品 PRD（见 `docs/design/prd-v0.1.md`）的关系，避免研究口径与实现口径打架。

- **研究标注仍然用单主标签**：每条弹幕标一个 dominant label，便于计算 Cohen's κ、per-label P/R/F1，并与 slides 的 7 类一致。
- **运行时中间表示用多标签**：系统内部输出 `{tag: score}`，一条弹幕可同时命中多个标签；呈现决策由用户策略（policy layer）决定，而不是由分类器直接决定。
- **候选第 8 类 Irrelevance**：PRD 把 Repetition 与 Irrelevance 分开。研究阶段先不拆：标注工具加 `irrelevance_secondary` 字段收集证据，pilot 的 κ 与边界案例支持后再升级为正式类别（升级后 Part 5 的 "seven categories" 要改成 eight）。
- **Sarcasm（讽刺）**：作为辅助研究标签，不进入主分类；用于分析反讽类困难样本、LLM 与标注者的分歧。
- **`other`** 仍然是标注兜底类；多标签表示里可以没有标签，但主标签必须有。
- 术语：论文正文用 social function（与 slides 一致），架构文档可用 semantic tags。

## 8. v0.3 变更：两类新标签 + other 正式化（2026-11-04）

> 触发：用户指出「选视频太友善」，需要节奏/时政视频与对应标签；同时 other 不能当垃圾桶。
> 代码唯一来源已改：`danmaku_filter/taxonomy.py`（spec 版本 0.3），`config/taxonomy.json` 已重导出。

### 8.1 新增两个 provisional 标签

| id | 中文 | 定义 | 示例 | 默认策略 |
|---|---|---|---|---|
| `political` | 政治表达 | 对公共事务、政策、机构、人物、国族/地域身份的立场表达，可严肃可反讽 | 这政策早该改了 / 懂的都懂 / 又开始赢麻了 | show（用户可折叠） |
| `inflammatory` | 引战 | 在性别、地域、饭圈、道德等争议议题上刻意站队或拱火，未必辱骂具体人 | 这也能洗？ / 两边都别装了 / 不服来辩 | hide |

- 优先级/边界：`attack`（人身辱骂） > `inflammatory`（拱火） > `conflict`（两人抬杠）。
- 状态：provisional。先导/正式标注各 ≥ 60 条、双人一致性达标后才写进论文正文；不达标就退回 7 类 + 复用 conflict。
- 命名待定：`inflammatory` vs `controversy`（见 `docs/design/plan-v2.md` §9）。

### 8.2 other 是「不可判定」类，不是默认出口

- 定义：无法从文本与时间点判断社会功能，或不属于任何一类的文本（乱码、纯符号、无上下文的私聊式发言）。
- 规则：所有标签都 < 0.2 时才标 other；只是「模糊」时给最接近的标签 0.2–0.4，并在 note 说明理由。
- prompt（`danmaku_filter/classify/llm.py`）与 `prompt_guide()` 都已写入该规则；student 模型里 other 是独立分类头。
- 诊断：模型 other 率 vs 人工 other 率，差 ≤ 5pp；否则先查「过度兜底」再查「漏标」。

### 8.3 本文档与既有章节的关系

- 第 3 节「只给一个主标签」是 v0.2 时代的标注约定；v0.3 起标注重心是多标签 relevance 向量，主标签只用于一致性统计与展示。
- 第 7 节的「候选第 8 类 Irrelevance」仍未拆，优先级低于本次两类。


## 9. 判定顺序与最小对立对（v0.4 提案，2026-11-05）

> 触发：部署模型在 131 条留出样本上暴露单向塌缩——`information` 只有 19% 召回，61% 被判成 `humor`；`irrelevant_repetition` 的 29%、`spoiler` 的 60% 也被吸进 `humor`。原因是「给每类打 0–1 分」没有优先级，模型把「读起来有语气」等同于幽默。
> 唯一来源：`danmaku_filter/taxonomy.py` 的 `DECISION_ORDER`；prompt v3（`DM_LLM_PROMPT=v3`，默认）与本文档共用同一段文字。

### 9.1 判定顺序（标注员与模型执行同一套）

1. **先判噪声**：只有符号、叠字、数字/字母刷屏、空口嗨、打卡（？？？、。。。。、1111、kkkk、纯「哈哈哈哈」、第一、前排、打卡）→ `irrelevant_repetition` 0.8–1.0，不要当玩笑。
2. **再判风险**：辱骂个人/群体 → `attack`；没有辱骂但抬杠、升级争吵 → `conflict`；在争议议题上刻意站队拱火 → `inflammatory`；透露具体剧情或结局 → `spoiler`。风险类优先于口味类，可互相叠加。
3. **信息优先于口味**：给出事实、出处、署名、翻译、时间点、片名、数字，或对视频的真实提问 → `information` ≥ 0.6，**即使语气在玩梗**；带事实的玩笑同时保留中等幽默分。
4. **幽默 vs 共鸣**：幽默必须有语言游戏（双关、梗、荒诞对比、反转、引用）；纯情绪反应（好听、泪目、太真实了、破防了、帅、好感动）→ `emotional_resonance`，读起来好笑也算共鸣。两者都成立时幽默 0.6–0.8、共鸣 0.4–0.6。
5. **最后才 other**：只有上面全部 < 0.2 时才让 `other` ≥ 0.2。

### 9.2 最小对立对（回归测试 + prompt few-shot）

同一话题、只差一个词、必须落到边界两侧的句子对，放在 `danmaku_filter/boundary.py`（23 对 / 46 条）：

- information vs humor：`这首歌是1987年的` / `这首歌1987年的，我爸都会唱`
- information vs emotional_resonance：`这集只有24分钟` / `这集看哭了`
- humor vs emotional_resonance：`好听` / `好听，笑死`
- spoiler vs humor：`凶手是他弟弟` / `我赌五毛凶手是他弟弟`
- irrelevant_repetition vs humor：`哈哈哈哈` / `哈哈哈哈这翻译`
- attack vs conflict：`up主就是个傻逼` / `你懂什么就在这里说`

用法：其中 6 对（每个边界一对）进 prompt v3 作为锚点，全部 46 条作为回归测试与 `scripts/eval_prompt.py` 的评测集。**期望标签由句子构造决定，不是人工标注**，用于验证「边界是否被遵守」，不能当作金标替代人工。

### 9.3 评测口径

报告除总体 agreement 外，必须给 `per-pair 混淆` 与「边界子集准确率」；否则幽默吸走一切也能拿高分（现有 0.66 就是这样来的）。实验结果见 `docs/experiments/e8-boundary.md`。
