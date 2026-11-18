# 方案 v2：小模型 + 校准概率 + 预取（回应 2026-11-04 的 5 点意见）

> 状态：已按 v2 落第一版代码（taxonomy v0.3 + 批量分类），蒸馏/校准/预取为下一阶段实现。
> 关联：[plan-v1.md](plan-v1.md)、[contracts.md](contracts.md)、[../taxonomy.md](../taxonomy.md)、[../pilot/README.md](../pilot/README.md)。

## 0. 一句话结论

把「运行时逐条调 LLM」改成「离线蒸馏 → 本地小模型批量打分 → 概率校准 → 打开页面即预取」，
LLM 只做冷启动的 teacher；标签体系从 7 类扩到 9 类 + 正式化的 other；验证数据从「友善的音乐/梗视频」扩到「节奏/时政视频 + 评论区」，
并用校准后的概率替代 LLM 自报 confidence。

| 维度 | v1（现在） | v2（本方案） |
|---|---|---|
| 打标 | deepseek-flash 逐批调用（0.53 s/条，云端） | 本地小模型（目标 2–10 ms/条，离线可跑） |
| 调用方式 | precompute 已 batch，但 `classify` 子命令仍逐条 | 全部 batch；批大小按端点/显存自适应 |
| 置信度 | LLM 自报分数（不是概率） | sigmoid + temperature scaling，报 ECE/Brier |
| 标签 | 7 类 + other 兜底 | 9 类评分 + other 作为独立类正式化 |
| 验证集 | 1 个音乐/梗视频的 1,407 条弹幕 | + 5–8 个视频（含时政/竞技/辩论）+ 300–500 条评论 |
| 部署 | 打开视频后现场打标或读离线缓存 | document-start 预取 + miss 走 rules 兜底 |

## 1. 五个问题逐条回应

### 1.1 不要逐条调 LLM → 蒸馏成小模型，batch + 预取

**问题**：cloud LLM 每条 0.5 s、按条计费、有网络依赖，不可能做运行时过滤；`classify` 子命令还是逐条请求。

**方案（teacher → student 蒸馏）**

1. **teacher 只做冷启动**：deepseek-flash 对 6,000–10,000 条弹幕批量打弱标签（batch 40–100，一次请求多标签向量 + note），保留现有 prompt 与容错。
2. **student 学的是「teacher 的标签分布」**，不是原始 LLM：
   - 方案 A（推荐）：embedding + 多标签线性头。用 Ollama 拉 `bge-m3`（568M，输出 1024 维）冻结 embedding，对每个标签训一个 logistic 回归 / 小 MLP；`other` 单独一个头。
   - 方案 B（备选）：LoRA 微调 Qwen3-0.6B 或 LFM2-1.2B（用户本地已有 lfm2 系列），llama.cpp/GGUF 部署，输入 4K ctx 足够。
   - 方案 C（兜底）：难例/低置信度仍回 teacher 复核（离线队列，不阻塞播放）。
3. **为什么 4K–8K 够**：蒸馏路线里 student 一次只吃「一条弹幕 + 可选上下文」，不需要长上下文；embedding 模型甚至没有生成过程。
4. **batch + 预取**：
   - 离线：`embed_corpus.py` 一次 256–512 条送 Ollama `/api/embed`；训练与推理都按 batch。
   - 运行时：`/decisions` 已支持批量；新增 `/api/v1/prefetch`，打开视频页即在后台把整段弹幕打完并原子写缓存。
   - 播放器开始请求弹幕时若缓存未就绪：全部放行（fail-open），同时后台继续补；下次播放/刷新就命中。

**验收**：同一段 1,407 条，student 端到端（取弹幕→打分→写缓存）≤ 30 s CPU / ≤ 5 s GPU；`classify` 子命令请求数 = ceil(N / batch)。

### 1.2 弹幕验证力度不够 → 加评论作为更难的外部效度检验

**问题**：弹幕短、模板化、梗多，标签边界本来就靠上下文；只用弹幕证明分类器有效，审稿人/老师会问「评论能不能行」。

**方案**

- 新增评论数据集：B 站评论区 API（`/x/v2/reply?type=1&oid=<aid>&sort=2`）抓 3–5 个视频各 100 条（长文本、论证型、更容易出现 attack/conflict/political）。
- 评论上跑同一套 tagger（teacher + student），抽 100 条双人标注，报告同一指标的**域迁移差**：
  - 弹幕 macro-F1 vs 评论 macro-F1，差值 ≤ 0.10 视为可接受；
  - 分标签看：information/political/attack 在评论上应更高（文本更长），humor/IR 应更低（评论里少）。
- 论文写法：把评论集定位为 external validity check（RQ2 的边界条件），不改变主研究单位（viewer-side 弹幕过滤）。

**待定**：评论是否算入 Phase 3 主评测（建议只做 secondary analysis，避免 scope 膨胀）。

### 1.3 视频太友善 → 换节奏/时政视频，并加 1–2 个标签

**已做**（taxonomy v0.3，代码已改）：新增两个 provisional 标签，默认策略已进 policy：

| id | 中文 | 定义 | 示例 | 默认动作 |
|---|---|---|---|---|
| `political` | 政治表达 | 对公共事务/政策/机构/人物/国族地域身份的立场表达，含反讽 | 这政策早该改了 / 懂的都懂 / 又开始赢麻了 | show（用户可折叠） |
| `inflammatory` | 引战 | 在性别、地域、饭圈、道德等争议议题上刻意站队/拱火，未必辱骂具体人 | 这也能洗？/ 两边都别装了 / 不服来辩 | hide |

- 与既有类别的关系：`attack`（人身辱骂）> `inflammatory`（拱火）> `conflict`（两人抬杠）——按「是否需要被处理」排序。
- 视频选择：音乐/梗（基线）+ 竞技或游戏对喷 + 辩论/吐槽 + 时政新闻切片/时事评论 + 影视剧透，共 5–8 个；每个视频分层抽 20 条做人工检查，确认新标签真的出现。
- **伦理**：政治内容只本地处理、不公开原文与用户名、报告只给聚合统计；双人标注；论文里不引用具体弹幕原文，只写类别比例。

**待拍板**：`inflammatory` 是否改名 `controversy`（更中性、更贴「争议」）；或只保留 `political` + 复用 `conflict`。

### 1.4 other 正式化：不是垃圾桶，是「不可判定」类

**问题**：现在 `other` 是 argmax 兜底，prompt 还写「只有都不合适才 other=1.0」——模型会把它当安全出口。当前缓存 other 只有 1/1,407（0.07%），几乎等于没标。

**方案（代码已改）**

1. `taxonomy.py` 里 `OTHER_DEF` 有正式定义、示例、`fallback: true` 和使用规则；`spec()` 的 categories 里包含它。
2. prompt 明确：other 只在**所有标签 < 0.2** 时给高分；只是「模糊」时给最接近的标签 0.2–0.4 并在 note 说明。
3. student 里 other 是**独立头**（不是 argmax 兜底），其概率直接可比。
4. 诊断指标：`other 率`（模型 vs 人工）、`other 上的误判成本`（被判 other 的样本里人工给了实质标签的比例）。目标：|模型 other 率 − 人工 other 率| ≤ 5pp。

### 1.5 confidence 不是真概率 → 用校准后的概率

**问题**：LLM 输出的 0.9 是「下一 token 的采样倾向」，随 prompt 措辞漂移，不是 P(标签正确)。

**方案**

1. student 输出 per-tag sigmoid 概率 p。
2. 在 held-out 20%（按视频划分，防泄漏）上做 **temperature scaling**（每标签一个 T，或全局 T + per-tag bias），最小化 log-loss / ECE。
3. 报告 **ECE、Brier、reliability diagram**；ECE 目标 < 0.05（5% 分箱）。
4. policy 的 `min_confidence` 改为「校准后概率阈值」，并按每标签设不同阈值（信息类可以低，attack 可以高），阈值用 held-out 上的 precision 目标选（例如 attack precision ≥ 0.9）。
5. 可选：conformal prediction 给「保证召回」的阈值，作为论文里的加分项。

**论文写法（Phase 2 一句）**：We distil the LLM's relevance judgements into a calibrated local scorer: per-tag probabilities are fitted on a held-out split with temperature scaling (ECE = X), and the viewer-facing thresholds act on these calibrated probabilities rather than on LLM self-reports.

## 2. 目标架构

```
离线（一次）
  B站 view API / seg.so ──► 弹幕 + 评论语料 ──► teacher (deepseek-flash, batch)
                                                        │ 弱标签 6–10k
                                                        ▼
                                     embedding 冻结 ──► 多标签头 + 校准（held-out）
                                                        │
                                                        ▼
                                          student 权重 + 校准参数 + 每标签阈值

运行时（每次播放）
  document-start ──► 识别 bvid ──► POST /api/v1/prefetch
                                        │
                                        ▼
                               worker: 取全量弹幕 → student batch 打分
                                        │ 原子写 data/tags/<bvid>.json (schema v3)
                                        ▼
  seg.so 响应 ──► userscript 拦截 ──► /decisions（读缓存）──► show/hide/collapse
                                        │ 缓存未就绪：fail-open + 本段用 rules 兜底
                                        ▼
                              侧边栏：已折叠 N 条 / 原因 / 纠正
```

## 3. 模型选型与成本（数字为待实测目标）

| 路线 | 模型 | 每 1,407 条 | 显存/内存 | 概率质量 | 风险 |
|---|---|---|---|---|---|
| teacher（现状） | deepseek-flash | 752 s（0.53 s/条） | 云端 | 无校准 | 网络/成本 |
| A 推荐 | bge-m3 (568M) + 线性头 | 目标 ≤ 30 s CPU / ≤ 5 s GPU | < 2 GB | sigmoid，可校准 | 语义细节（反讽）弱 |
| B 备选 | Qwen3-0.6B / LFM2-1.2B + LoRA | 目标 ≤ 60 s CPU（GGUF） | ~1–3 GB | 需 logits 或再校准 | 生成式输出不够稳 |
| C 兜底 | 现有 rules | < 0.1 s | 0 | 规则分数 | 召回低（94% other） |

实现约束：主仓库保持纯标准库；训练/推理脚本放 `scripts/` 并列入 `requirements-ml.txt`（numpy 必选，训练可选 torch）；部署优先走 Ollama（已有环境），避免在 WSL 里装 CUDA 栈。

## 4. 标签体系 v0.3（已落代码）

- 评分标签 9 个：information, humor, emotional_resonance, irrelevant_repetition, attack, conflict, spoiler, political, inflammatory。
- 兜底/不可判定 1 个：other（独立头 + 独立评估）。
- 标注单位仍是「一条弹幕 → 1–3 个标签 + 0–1 强度」，但评测用 per-tag 概率/阈值，不再只看 dominant。
- 代码已同步：`danmaku_filter/taxonomy.py`（唯一来源）、`policy.py` 默认策略、`annotation.py` 配额、`extension/lib/policy.js` + 重建 dist、`config/taxonomy.json`。

## 5. 数据计划

| 数据 | 规模 | 用途 | 采集/标注 |
|---|---|---|---|
| 弹幕弱标签 | 6,000–10,000（5–8 视频） | 训练 student | teacher 批量打标 |
| 弹幕人工 gold | 240 条双人（每人 ≤3 标签 + 强度） | 一致性 α + 评测/校准 | 分层抽样，双人独立 |
| 弹幕补充标注 | 300–600 条单人 | 难例分析 | 我 + 组员 |
| 评论 | 3–5 视频 × 100 条 | 外部效度 | API 抓取，100 条双人 |

- 划分：按视频分 train/val/test（避免同一视频弹幕泄漏）。
- 一致性：多标签用 Krippendorff's α（α_U）或 ICC，替代 Cohen's κ；dominant 一致率作为辅助。

## 6. 评测矩阵

| 层 | 指标 | 数据 | 目标 |
|---|---|---|---|
| 标签质量 | per-tag P/R/F1、macro-F1、dominant 一致率 | 240 人工 gold | macro-F1 ≥ 0.70、一致率 ≥ 0.85 |
| 相关度 | MAE、Spearman（预测 vs 人工强度） | 240 | MAE ≤ 0.15、ρ ≥ 0.6 |
| 校准 | ECE、Brier、reliability diagram | held-out | ECE ≤ 0.05 |
| 域迁移 | 评论 vs 弹幕 macro-F1 差 | 100 评论 | ≤ 0.10 |
| 系统 | p50/p95 打分延迟、预取完成率、拦截成功率、fail-open 率 | 真机 | p95 ≤ 50 ms/条、预取 ≥ 90% 命中 |
| 体验 | 误杀率（纠正数/10 min）、NASA-TLX、理解/记忆 | 3–5 人可用性 | 误杀 ≤ 2 条/10 min |
| 消融 | rules / teacher / student / student+校准 | 同一 test | 报告每档增益 |

## 7. 预取与缓存 schema v3

- 新增 endpoint：`POST /api/v1/prefetch {bvid}` → 202 + 任务状态；`GET /api/v1/prefetch/{bvid}` 轮询；去重、单 worker 队列、失败回退 rules。
- 缓存 v3：新增 `scores`（校准后概率）、`other_score`、`model`、`calibration`（方法/版本/ECE）、`prefetch`（状态/时间）；旧 v2 缓存可读，缺失字段按 0 处理。
- userscript：document-start 取 `location.pathname` 的 bvid，`fetch` 发预取（失败静默）；SPA 切视频时重发；面板显示「打标中 / 已就绪」。
- CLI：`prefetch --bvid`（单视频）、`prefetch --file bvids.txt`（批量）、`--backend student|rules|teacher`。

## 8. 两周排期（11/5 起，可压缩）

| 天 | 事项 | 产出 |
|---|---|---|
| D1 | 拍板 6 项 + 定视频清单 + 评论抓取脚本 | `docs/design/plan-v2.md` 定稿、视频表 |
| D2–D3 | teacher 打标 6–10k + other 规则验证 | `data/weak/`、other 率报告 |
| D4–D5 | embedding + 多标签头 + temperature scaling | `scripts/train_student.py`、ECE 报告 |
| D6 | 240 条双标 + 100 条评论标 | `data/gold/`、α、域迁移数字 |
| D7 | bench + 失败案例分析 | per-tag 表、Figure 2 |
| D8–D9 | prefetch endpoint + userscript + 真机 | `/api/v1/prefetch`、真机记录 |
| D10–D11 | 3–5 人可用性 session | 误杀/TLX 数字 |
| D12–D13 | 并入 docx v2 + 图表 | 更新版 docx + 2 图 |
| D14 | 复盘/提交 | 最终版 + BACKLOG 清空 |

## 9. 待拍板（需要用户/组内决定）

1. 新标签命名与数量：`political` + `inflammatory`（本方案）还是 `political` + `controversy`，或只加 1 个？
2. student 路线：先 A（embedding + 线性头，快、可校准）还是直接 B（LoRA 微调 0.6B）？
3. 评论数据：只做 secondary analysis，还是进主评测？
4. 政治/节奏视频清单与可接受边界（哪些视频能进仓库、是否需要老师/组内同意）。
5. 双人标注的人员（目前只有我 + 用户；240 条需要第二人不少于 8 小时）。
6. 是否把「蒸馏 + 校准」写进作业 docx 的 Phase 2（我建议写，作为 RQ2 的方法贡献）。

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| teacher 偏差（humour 64%）被 student 继承 | prompt v2 + 重采样 + 每标签阈值 + 人工 gold 校准 |
| 反讽/上下文依赖样本 linear head 学不会 | 难例回 teacher；必要时升 B 路线或加规则特征（引号、反问、梗表） |
| 8 GB 显存跑 bge-m3 + Ollama 并发 | CPU 推理 + 小 batch；或换 bge-small-zh（24M） |
| 预取来不及（播放器先发请求） | fail-open + rules 兜底 + 第二次播放命中；面板提示「本次为快速模式」 |
| 政治数据敏感/合规 | 本地存储、不公开原文、聚合报告、双人标注、可随时删除 |
| 时间不够 | A 路线最小闭环（embedding + 头 + 校准）优先，LoRA 与 conformal 为加分项 |

