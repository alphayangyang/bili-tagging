# AI 被试模拟运行（S2 dry run，v2：加 sham 条件 + 盲化）

本目录是一次**模拟**受控实验的完整产物：8 个 LLM 子智能体扮演 8 名有不同人格倾向的被试，
按 docs/study/agents/PROTOCOL.md 的盲法手册看 6 支材料视频（只能看两张截图与弹幕样例，不能真的播放），
填写每块 7 点量表（含期待/信任 2 题）与 debrief 记录。**这些数据不是人类被试数据**，只用于：

1. 跑通「材料生成 → 被试作答 → 组装 → analyze.py → IMR 结果」整条分析管线；
2. 给 IMR 文档一个可引用的示意结果（明确标注 simulated）；
3. 找出设计/脚本/组装里的 bug，并检验「盲化 + sham」是否改变结论。

任何对外提交的文档都必须写明 participants are simulated LLM agents，不得冒充真人。

## 这一版（v2）改了什么

- **加 sham 条件 D**：面板、整池加载进度、折叠列表都在，但什么都不过滤；A vs D 分离界面/期待效应，C vs D 分离语义过滤净效应。
- **盲化材料**：不再写条件名与「这一组会过滤什么」，只写可见事实（有无面板/进度胶囊、池量、实际可见条数、面板统计），也不列出被隐藏弹幕原文；被试无法从材料推断组别。
- **8 人 × 6 块 = 48 blocks**：每人看全部 6 支视频一次；条件多重集 {A,A,B,B,C,D}，按 (p−1) mod 4 轮转；每条件 12 块、每个（视频,条件）单元 2 次。
- 正式设计（真人）是 4 条件 × 8 块/人，见 docs/study/protocol.md §2.1 的变更说明；这里是缩减版试跑。

## 文件

| 路径 | 内容 |
| data/study/agents/personas.json | 8 个人格设定与盲法评分基线 |
| data/study/agents/PROTOCOL.md | 发给每个 agent 的中文作答手册（盲法规则、题面、输出 JSON 格式） |
| data/study/agents/run-sheet-8.csv | 48 块真实分配（participant, condition, block, bvid, genre, density, study_id, url） |
| scripts/study/agents/build_run_sheet8.py | 生成 48 块分配（拉丁方轮转） |
| scripts/study/agents/build_materials.py | 由 run-sheet + report.json + 标签模型生成每块盲化材料 |
| data/study/agents/blocks/P01..P08/ | 每块 Markdown 材料（可见事实、截图、池量、可见条数） |
| data/study/agents/blocks/P0x.index.json | 发给被试的盲索引（只有 block/study_id/bvid/genre/density/counts） |
| data/study/agents/materials-index.json | 内部材料索引（含真实 condition，不发给被试） |
| data/study/agents/frames/ | 6 支视频各 2 帧截图（30%、75%），已去除插件角标 |
| data/study/agents/out/P01..P08.json | 8 个 agent 的原始作答 |
| scripts/study/agents/assemble.py | 组装 questionnaire.csv + raw session JSON（盲输出 ↔ 真实条件对齐） |
| scripts/study/agents/make_figure.py | 画 Figure 3（四条件均值 + 被试级 bootstrap 95% CI） |
| data/study/questionnaire.csv | 48 行问卷（analyze.py 输入） |
| data/study/raw/dmf-study-*.json | 48 个 session 导出（含 counts/events） |
| data/study/analysis.json / analysis.md | analyze.py 输出（4 条件、6 对比） |
| docs/study/results-simulated.md | 结果摘要与局限 |

## 复现

~~~bash
# 1. 48 块分配表
python3 scripts/study/agents/build_run_sheet8.py

# 2. 重生成 48 块材料（先删旧目录，避免 stale 文件）
rm -rf data/study/agents/blocks
python3 scripts/study/agents/build_materials.py

# 3. 重新组装（读 out/*.json + 材料索引 + preflight 延迟）
python3 scripts/study/agents/assemble.py

# 4. 分析 + 图
python3 scripts/study/analyze.py --bootstrap 5000
python3 scripts/study/agents/make_figure.py
~~~

## 设计与脚本 bug（两轮共修掉）

1. **拉丁方不平衡（v1）**：scripts/study/session_url.py 原表第 3/4 行相同、第 5/6 行相同，
   BV1Ho4y1D7Wz 在 A 出现 4 次、C 出现 0 次，条件与视频混淆。改成 cyclic + anti-cyclic 6 行后，
   每视频在每条件恰好 2 次。
2. **familiar 编码冲突（v1）**：协议 1 = 没看过、2 = 片段、3 = 完整看完，但 analyze.py 把它当 0/1 标志，
   导致 6 名被试全部被 familiarity 过滤掉。assembler 现在写 0/1（1 → 0，2/3 → 1）。
3. **延迟匹配落空（v1）**：原按 (participant, condition, bvid) 匹配 preflight，常得 0 ms；
   改为按条件取实测分布的稳定采样。
4. **材料泄露组别（v1→v2）**：v1 的材料直接写「当前是 C 条件、会过滤什么」，导致需求特征；
   v2 全部改成只描述可见事实，并新增 sham 条件来量化这一偏差（见 results-simulated.md §4）。

## 已知偏差（阅读结果时必须记住）

- LLM 被试**没有真的看视频**：它们读的是两张截图 + 弹幕样例 + 计数，所以对「理解」和「共看感」的
  判断比真人弱，comp 量表被改成「5 个理解子项自评 1–7」。
- 8 个 agent 的人格是写死的，离散度比真人小，也更容易被材料里给出的计数锚定。
- 低密度抽稀不在面板统计里（设计如此），有 agent 注意到「少了 541 条但面板显示 0/0」并降低控制感；
  真人实验里主试脚本需要说明这一点。
- 因此本数据**只能当管线验证与示意**，不能当作支持/否定 H1–H6 的证据。
