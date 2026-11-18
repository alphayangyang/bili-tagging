# Pilot study（先导试验）：可行性验证

> 对应作业：10/29 布置的 “revised introduction + pilot study”，交付文件
> `submission/ZonE + Introduction + pilot study.docx`（生成脚本 `scripts/build_pilot_doc.py`）。
> 先导试验只验证可行性，不检验研究假设（m00839 确认的定位）。

## 三个可行性问题

1. **流水线**：离线打标能否在真实中文弹幕上稳定产出可解析的 relevance 向量，成本是否可接受？
2. **表示**：一条弹幕一个 dominant label 是否够用？
3. **拦截**：浏览器插件能否在播放器之前改写弹幕流而不破坏播放？

## 语料与方法

- 语料：`BV1GJ411x7h7`（音乐/梗视频），1,407 条滚动弹幕（mode 1/4/5）。
- 两轮打标：先 keyword 规则基线，再用混合打标器（每批 100 条发给 chat completions 端点，
  要求返回 JSON relevance 向量；`DM_LLM_MODEL=deepseek-flash`）。
- 审核（audit）：分层抽 29 条（每个可用标签 6 条 + other），一位作者按标注指南盲判，
  与模型 dominant tag 及强度比较。样本与人工标签已入库：`data/pilot/audit_sample.json`、
  `data/pilot/audit_gold.json`，复核命令 `python3 scripts/audit_score.py`。

## 结果（2026-11-04）

| 指标 | 结果 |
|---|---|
| 打标成本 | 0.53 s/条（batch 100），1,407 条 12.5 分钟（752 秒） |
| dominant 分布 | humour 902（64%）、emotional resonance 233（17%）、information 200（14%）、irrelevant repetition 65（5%）、spoiler 5、conflict 1、attack 0、other 1 |
| 两个标签都 ≥0.5 的弹幕 | 11 / 1,407（0.8%） |
| Schema 合法率 | 100%（无重试） |
| 规则基线 other 占比 | 94.1%（1,324 / 1,407） |
| 审核样本 | 29 条，单标注者 |
| dominant tag 一致 | 26 / 29（89.7%） |
| 强度平均绝对误差 | 0.05 |
| 拦截验证 | 扩展 40 条单测断言全过，含 protobuf 改写与 fail-open |

三条不一致集中在：问官方字幕来源（人工 information / 模型 humor 0.25）、裸参数串
（人工 irrelevant_repetition / 模型 humor 0.55）、既是玩笑又是夸奖（人工 humor 0.5 /
模型 emotional_resonance 0.75）→ 错误集中在「语义依赖上下文」的短弹幕。模型对短而正面的弹幕默认给 humour，这是提示词调优的第一优先级。

## pilot 改掉的五个设计决定

1. 单 dominant label → 标签词表 + relevance 向量 + 用户逐标签阈值；
2. LLM 调用移出播放时 → 离线缓存 + 本地桥接（决策只查表）；
3. canvas 无法逐条点击 → 剔除 + 侧边栏「已折叠 N 条（可展开）」；
4. 标注协议简化为 1–3 个标签 + 三级强度、单屏完成；
5. 密度（volume）成为 policy 层的独立维度，对应 RQ1 的 volume 操纵。

## 未做 / 局限

- 只有一个视频、一个审核者、离线语料，尚无真实用户。
- 可用性 session（3 位被试，见 docx Table 3）安排在正式研究第一周，主实验与两周
  field study 在其后。
