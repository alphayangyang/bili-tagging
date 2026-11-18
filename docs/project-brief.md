# 项目简报 — Filtering Danmuku (ZonE)

## 1. 基本信息

- 组名：ZonE（zeal / on / exploration）
- 题目：Filtering Danmuku: How Comment Type and Volume Shape Viewer Experience
- 研究对象：Bilibili 中文弹幕（bullet comments）
- 交付物 A：Introduction（500 词，按 slide 7 的五段漏斗结构；组内分工见 `docs/intro/README.md`）
- 交付物 B：viewer-side 弹幕过滤原型 + 技术评测 + 用户研究

## 2. 动机、已有工作与缺口

**动机**：弹幕是 B 站年轻用户观看体验的核心，能提供信息、幽默与情绪共鸣；但也会引发冲突与攻击，而这些影响并非所有观众都能接受。平台只给了三种粗糙选择：全开、全关、少量平台级关键词屏蔽——缺少观众侧、个性化的弹幕过滤。

**已有工作**（详见 slide 3 / Introduction Part 3–4）：

| 研究 | 关键发现 | 局限 |
|---|---|---|
| Mu, Zou & Chen (2025) | 场依存型学习者在有弹幕时保持率下降、注意力向弹幕区偏移；场独立型基本不受影响 → 干扰通过注意力分配起作用 | 教学视频；只比较弹幕「有/无」，未做个性化过滤 |
| Jhaver et al. (2023) | 个性化审核界面需要可理解、有上下文、低负担；用户担心过度过滤，要求类别定义、示例与合理默认值 | 小样本目的抽样；短小的模拟信息流；只做文本毒性；无长期行为与准确率测试 |
| Wang et al. (2025) | LLM prompting 起步性能最快（尤其 recall / F1）；但三种方法（example labeling / rule writing / prompting）都难以持续优化 | 以大学生为主；英文 YouTube 评论；二元 Keep/Remove 分类 |

**缺口**：没有研究直接把弹幕类型与数量同观看体验联系起来，也没有面向中文弹幕的可理解、低负担、个性化过滤器设计。

## 3. 技术路线（一页纸）

1. **研究设计**：控制实验，操纵弹幕类型（7 类）与数量（密度），测量 discomfort、perceived control、FOMO、co-viewing feel。
2. **分类体系**：按 social function 分 7 类——information / humor / emotional resonance / irrelevant repetition / attack / conflict / spoiler。定义与标注指南见 `docs/taxonomy.md`。
3. **系统**：viewer-side 插件（浏览器扩展或油猴脚本）。按类别 show / hide / collapse；每条被过滤的弹幕给出一句解释；支持一键纠正（误杀/漏杀）。
4. **分类方法（hybrid）**：LLM few-shot prompting 冷启动 → 用户纠正 + 规则沉淀做 refinement，直接回应 Wang et al. (2025) 的「持续优化难」。
5. **上下文**：spoiler 类天然依赖视频时间点与后续内容，需要视频标题/时间戳/摘要等上下文，不能只看单条文本。
6. **评测**：技术性能 + 用户负担 + 观看体验 + 任务结果。

## 4. 评测设计

| 维度 | 指标 | 采集方式 |
|---|---|---|
| 技术性能 | precision / recall / F1（每类 + macro）、p50/p95 latency | 人工标注 gold set，逐条比对 |
| 用户负担 | 初始设置时间、纠正次数 / 纠正耗时、NASA-TLX | 操作日志 + 问卷 |
| 观看体验 | discomfort、perceived control、FOMO、co-viewing feel | 量表（前后测） |
| 任务结果 | 信息类视频的理解测验 / 记忆测验 | 选择题 + 自由回忆 |

## 5. 参考文献（APA，来自 slide 5）

- Jhaver, S., Zhang, A. Q., Chen, Q. Z., Natarajan, N., Wang, R., & Zhang, A. X. (2023). Personalizing content moderation on social media: User perspectives on moderation choices, interface design, and labor. *Proceedings of the ACM on Human-Computer Interaction, 7*(CSCW2), Article 289, 1–33. https://doi.org/10.1145/3610080
- Mu, D., Zou, M., & Chen, Y. (2025). How do different cognitive styles learners deal with the bullet screen interruption in instructional videos? An eye-tracking study. *BMC Psychology, 13*, Article 961. https://doi.org/10.1186/s40359-025-03182-1
- Wang, L., Yurechko, K., Dani, P., Chen, Q. Z., & Zhang, A. X. (2025). End user authoring of personalized content classifiers: Comparing example labeling, rule writing, and LLM prompting. In *Proceedings of the 2025 CHI Conference on Human Factors in Computing Systems*. Association for Computing Machinery. https://doi.org/10.1145/3706598.3713691

## 6. 写作与仓库约定

- 论文/写作里统一用「social function 七类」这一说法（slide 4 的表述），避免与技术细节中的关键词/情感信号混用。
- 标签 id 以 `danmaku_filter/taxonomy.py` 为准，`config/taxonomy.json` 由它导出。
- 原始弹幕、标注、预测文件放 `data/`，其中 `data/raw/` 与含个人信息的内容不入库。
- 产品/架构细化见 `docs/design/prd-v0.1.md`；与 slide 方案的对齐决策见 `docs/design/reconciliation.md`。
