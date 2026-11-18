# AI 被试模拟运行（S2 dry run）

本目录是一次**模拟**受控实验的完整产物：6 个 LLM 子智能体扮演 6 名有不同人格倾向的被试，
按 docs/study/protocol.md 的设计看 6 支材料视频（只能看截图与弹幕样例，不能真的播放），
填写每块 7 点量表与 debrief 记录。**这些数据不是人类被试数据**，只用于：

1. 跑通「材料生成 → 被试作答 → 组装 → analyze.py → IMR 结果」整条分析管线；
2. 给 IMR 文档一个可引用的示意结果（明确标注 simulated）；
3. 找出设计/脚本/组装里的 bug（实际找出了 3 个，见下）。

任何对外提交的文档都必须写明 participants are simulated LLM agents，不得冒充真人。

## 文件

| 路径 | 内容 |
| data/study/agents/personas.json | 6 个人格设定与评分基线 |
| data/study/agents/PROTOCOL.md | 发给每个 agent 的中文作答手册（题面、量表锚点、输出 JSON 格式） |
| data/study/agents/run-sheet-6.csv | 36 块分配（participant, condition, pair, block, bvid, genre, density, study_id, url） |
| scripts/study/agents/build_materials.py | 由 run-sheet + report.json + 标签模型生成每块材料 |
| data/study/agents/blocks/P01..P06/ | 每块 Markdown 材料（事实、截图、池量、显示/隐藏/折叠计数与样例） |
| data/study/agents/frames/ | 6 支视频各 2 帧截图（30%、75%），已去除插件角标 |
| data/study/agents/out/P01..P06.json | 6 个 agent 的原始作答 |
| scripts/study/agents/assemble.py | 组装 questionnaire.csv + raw session JSON |
| data/study/questionnaire.csv | 36 行问卷（analyze.py 输入） |
| data/study/raw/dmf-study-*.json | 36 个 session 导出（含 counts/events） |
| data/study/analysis.json / analysis.md | analyze.py 输出 |
| docs/study/results-simulated.md | 结果摘要与局限 |

## 复现

~~~bash
# 1. 重生成 36 块材料（先删旧目录，避免 stale 文件）
rm -rf data/study/agents/blocks
python3 scripts/study/agents/build_materials.py

# 2. 重新组装（读 out/*.json + 材料索引 + preflight 延迟）
python3 scripts/study/agents/assemble.py

# 3. 分析
python3 scripts/study/analyze.py --bootstrap 5000
~~~

## 设计与脚本 bug（本轮修掉）

1. **拉丁方不平衡**：scripts/study/session_url.py 原表第 3/4 行相同、第 5/6 行相同，
   BV1Ho4y1D7Wz 在 A 出现 4 次、C 出现 0 次，条件与视频混淆。改成 cyclic + anti-cyclic 6 行后，
   每视频在每条件恰好 2 次，每被试看全部 6 支视频一次。
2. **familiar 编码冲突**：协议 1 = 没看过、2 = 片段、3 = 完整看完，但 analyze.py 把它当 0/1 标志，
   导致 6 名被试全部被 familiarity 过滤掉。assembler 现在写 0/1（1 → 0，2/3 → 1）。
3. **延迟匹配落空**：原按 (participant, condition, bvid) 匹配 preflight，常得 0 ms；
   改为按条件取实测分布的稳定采样（同一块重跑结果稳定）。

## 已知偏差（阅读结果时必须记住）

- LLM 被试**没有真的看视频**：它们读的是两张截图 + 弹幕样例 + 计数，所以对「理解」和「共看感」的
  判断比真人弱，comp 量表被改成「5 个理解子项自评 1–7」。
- 6 个 agent 的人格是写死的（学者型、娱乐型、敏感型、路人、共看型、效率型），离散度比真人小，
  也更容易被材料里显式给出的计数（显示/隐藏/折叠）锚定。
- 条件 C 的评分差异很大程度来自「材料里写了多少攻击性弹幕被挡住」，不是真实观看体验。
- 因此本数据**只能当管线验证与示意**，不能当作支持/否定 H1–H5 的证据。