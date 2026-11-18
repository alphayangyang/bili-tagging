# 弹幕语义过滤：决策器消融、环境适应与部署级联实验报告

- 报告版本：v1.0（2026-11-05）
- 对应代码仓库：/home/alphayang/bili-tagging
- 冻结数据集：data/experiments/dataset.jsonl（sha256 b182f3cd…，6 个视频 / 2,457 条弹幕）
- 所有指标文件：data/experiments/results/*.json；分实验详版报告：docs/experiments/e2-context.md、docs/experiments/e3-topicality.md

---

## 0. 摘要

本研究要回答一个工程问题：在浏览器端对 B 站弹幕做「按社会功能标签」的实时过滤，**用什么决策器、按什么协议部署、在什么条件下会失效**。我们在 6 个真实视频、2,457 条真实弹幕上，对 6 类决策器（手写规则、两种线性原型分类器、TF-IDF+LinearSVC、bge-m3 标签锚点、端侧 qwen3.5:4b）做了留出与跨视频评估，并补做了上下文特征、环境相关性向量、bootstrap 规模、置信升级、延迟、先验漂移六组专项实验。

五条主要结论：

1. **手写规则太窄，已量化**：同视频留出 131 条上 agreement 仅 0.168、macro-F1 0.122，96/131 条落进 other；人工金标 29 条上 0.345。规则不能作为主决策器（H1 成立）。
2. **同视频线性模型能打**：用同视频 200 条 teacher 标签训练 hashed-TF-IDF 线性模型，音乐/梗视频（N=1,407）上 agreement 0.662；但同样协议在时政视频只 0.317、游戏 0.354、剧情 0.379。**收益来自「同视频训练」，不是模型类别**。
3. **跨视频不迁移**：留一视频（LOVO）协议下，线性模型 5 个视频平均 agreement 0.244–0.303，端侧 LLM 0.417，规则 0.213。跨视频时 4B LLM 反超线性模型，但都远低于同视频水平。
4. **「环境」不能靠拼特征补**：把标题/分区/位置/邻条拼进词面模型输入，cross-video 平均 agreement 从 0.330 降到 0.257–0.280（负结果）；用 bge-m3 环境向量做相关性轴，人工 180 条标注 AUC 0.571、95% CI 含 0.5（不显著）。两条「让 NLP 懂环境」的捷径都被证伪（H4 不成立）。
5. **部署形态应当是级联**：L1 同视频线性模型（0.13–0.27 ms/条）+ L2 低置信升级到 LLM，升级阈值 θ=0.3–0.45 时可以把 L1 上限从 0.33–0.69 抬到 0.81–0.99（teacher 完美假设下的上界），代价是 22%–88% 的条目要过 LLM。线上实测 300 条决策 HTTP 端到端 0.401 ms/条。

一句话：**「环境」不是靠通用语义模型自动感知的，而是靠每个视频的少量 teacher 标注 + 用户策略显式注入的**；LLM 负责 zero-shot 兜底与新标签扩展，线性模型负责同视频的吞吐，规则只剩高精度 cue 的最后一道闸。

---

## 1. 研究问题与假设

| 编号 | 问题 | 假设 | 结论 |
|---|---|---|---|
| RQ1 | 手写关键词/句式规则能否覆盖弹幕的社会功能分类？ | H1：不能，规则覆盖率与同义表达不足 | 成立（§3、§4） |
| RQ2 | 用 teacher（LLM）标签训练的轻量线性模型，能否在同视频达到可用质量？ | H2：可以，且延迟比 LLM 低 4–5 个数量级 | 成立（§4、§7、§9） |
| RQ3 | 跨视频（换领域）时线性模型能否直接迁移？ | H3：不能，需按视频重新 bootstrap | 成立（§4、§7、§10） |
| RQ4 | 把视频环境（标题、分区、位置、上下文）喂给文本模型，能否补上跨域差距？ | H4：能 | **不成立**（§5、§6） |
| RQ5 | 置信门控 + LLM 升级能否以可接受的成本逼近 teacher 质量？ | H5：能，成本随 θ 可调 | 部分成立（§8） |

---

## 2. 实验设置

### 2.1 任务定义

每条弹幕映射到一个**标签相关度向量** s ∈ [0,1]^11，标签空间 10 个社会功能标签 + other：

information / humor / emotional_resonance / irrelevant_repetition / attack / conflict / spoiler / political / inflammatory / other

- dominant 标签 = argmax s，允许多标签（同分按标签表顺序，other 最后）。
- 报告主指标：**dominant agreement**（与标签源一致的条数占比）、**macro-F1**（只在 gold 中出现过的标签上平均）、**tag-MAE**（11 维向量平均绝对误差）。
- agreement 的 95% 置信区间由 1,000 次 bootstrap 重采样得到。
- macro-F1 对「预测塌缩到多数类」非常敏感，因此与 agreement 一起看；两者背离时以 macro-F1 为准（见 §5 的塌缩假象）。

### 2.2 数据（表 1）

标签源：3 个视频复用离线缓存中的 deepseek-flash 标签（llm），3 个新增视频用 deepseek-flash 现场打标（llm-llm）。全数据集只有 1 个标注源，没有双人标注（这正是 §13 的局限之一）。

| 视频 | 领域 | 条数 | 标签来源 | dominant 分布 |
|---|---|---|---|---|
| BV1GJ411x7h7 | 音乐/梗（Rick Roll） | 1,407 | llm | humor 902、emotional_resonance 233、information 200、irrelevant_repetition 65、spoiler 5、conflict 1、other 1 |
| BV1w5xh6sEEz | 音乐/梗 | 173 | llm | humor 66、emotional_resonance 45、information 43、irrelevant_repetition 14、other 4、attack 1 |
| BV1rCxe6wEN9 | 时政 | 472 | llm | information 206、irrelevant_repetition 84、humor 63、political 53、emotional_resonance 37、other 11、attack 11、conflict 6、inflammatory 1 |
| BV1bnxr6cEJ9 | 时政/对喷 | 67 | llm-llm | humor 15、political 13、irrelevant_repetition 11、information 10、attack 9、emotional_resonance 8、conflict 1 |
| BV1mV4F6mE5Z | 游戏 | 174 | llm-llm | humor 69、information 52、emotional_resonance 25、other 16、irrelevant_repetition 6、conflict 3、inflammatory 2、attack 1 |
| BV1W18Y61Ee5 | 剧情/故事 | 164 | llm-llm | humor 60、emotional_resonance 47、information 22、other 17、irrelevant_repetition 12、conflict 4、attack 2 |
| **合计** | 4 类领域 | **2,457** | — | humor 1,175、information 533、emotional_resonance 395、irrelevant_repetition 192、political 66、other 49、attack 24、conflict 15、spoiler 5、inflammatory 3 |

另有两份人工标注：

- **人工金标 29 条**（data/pilot/audit_sample.json + data/pilot/audit_gold.json）：从视频 A 分层抽样、由 1 名标注者按标注指南盲判，用于校验 teacher 本身的质量（§3）。
- **环境相关性标注 180 条**（data/experiments/topicality/gold.json）：6 视频 × 30 条，标「是否与该视频主题相关」，单标注者，用于 §6。

### 2.3 被比较的决策器（表 2）

| 代号 | 实现 | 训练信号 | 说明 |
|---|---|---|---|
| rules | danmaku_filter/classify/rules.py | 人工 cue 列表 | 高精度关键词/句式，无命中给 other 0.5 |
| linear_centroid | danmaku_filter/classify/linear.py | teacher 标签（k 条） | hashed char 1–3gram TF-IDF（26.2 万桶）→ 每标签原型向量，dot(x,w)×10 → softmax |
| linear_softmax | 同上 | teacher 标签（k 条） | 同上但用 softmax 权重矩阵拟合（训练 4.7 s / 1,400 行） |
| tfidf_svm | sklearn TfidfVectorizer(char_wb 1–3) + LinearSVC(C=0.5, balanced) | teacher 标签 | **仅作上界对照**，生产不依赖 sklearn |
| bge_anchor | bge-m3 句向量 + 每标签 5 条中文描述/例句锚点，取 max 余弦 | 零样本（无训练） | 检验「纯语义相似度」够不够 |
| qwen35_4b | qwen3.5:4b（Ollama native /api/chat，num_ctx 8192, temp 0.2, batch 8） | 零样本 prompt（矢量化打分） | 端侧 LLM 代表 |
| deepseek_flash | deepseek-flash（云端） | 零样本 prompt | 只用于产出标签源与 29 条人工对照，不作 LOVO 被测者（否则自己考自己） |

### 2.4 三种评估协议

1. **同视频留出（in-domain）**：视频 A 随机留出 131 条作测试，其余 1,276 条作训练。测「同一视频内够用吗」。
2. **同视频 bootstrap（部署协议）**：从视频内随机抽 k ∈ {10,25,50,100,200,400} 条作 teacher 标注集 → 训练 → 测剩余 80 条。测「上线时最少要标多少条」。
3. **留一视频（LOVO，跨域）**：用其余 5 个视频的全部标签作训练，测目标视频 100 条（分层抽样）。测「能不能跨领域复用」。

所有抽样固定 seed，测试集按 dominant 分层，且训练集中剔除与测试集同文本的条目（避免近重复泄漏）。

### 2.5 环境与复现

- 硬件：RTX 5060 Laptop 8 GB（WSL2），Python 3.14，sklearn 1.9.1（仅实验）。
- 端侧 LLM 走 Windows Ollama（127.0.0.1:11434，mirrored networking），bge-m3 1.16 GB，qwen3.5:4b 3.32 GB。
- 复现命令见附录 B；每个实验的产物 JSON 见附录 A。

---

## 3. E0：teacher（deepseek-flash）标签质量

脚本：scripts/audit_score.py；结果：data/experiments/results/label_quality.json。

| 指标 | 值 |
|---|---|
| n | 29 |
| dominant 一致 | 26 / 29 = **0.8966** |
| 强度（0–1）平均绝对误差 | **0.052** |

3 条不一致：

| 弹幕 | teacher | 人工 | 读法 |
|---|---|---|---|
| 这个翻译是官方的吗？！ | humor 0.25 | information | 疑问句被当成玩梗；幽默标签在低分区分辨率不足 |
| ["0.05","0.1","1-1",…,"宋体",0] | humor 0.55 | irrelevant_repetition | 裸参数串被当成梗；符号/参数刷屏的识别不稳 |
| 太好听了，感谢我的好兄弟骗我 | emotional_resonance 0.75 | humor | 反讽/玩梗 vs 真情实感的边界 |

**读法**：teacher 在本任务上可信（≈90%），但它的错误集中在「幽默 ↔ 共鸣 ↔ 信息」三者的边界，且这些边界本身就是标注争议点。后续所有以 teacher 为 gold 的 agreement 都带有这一层噪声上限（≈0.9），不能当成 1.0 来读。

---

## 4. E1：决策器横评（主实验）

脚本：scripts/experiments/run_matrix.py；结果：data/experiments/results/matrix.json。

### 4.1 主表（表 4，agreement / macro-F1 / tag-MAE）

| 决策器 | 人工金标 29 | 同视频留出 131 | LOVO 5 视频均值 | 延迟 ms/条（进程内） |
|---|---|---|---|---|
| rules | 0.345 / 0.257 / 0.788 | 0.168 / 0.122 / 0.517 | 0.213 / 0.177 / 0.436 | 0.060 |
| linear_centroid | 0.759 / 0.551 / 0.250 | 0.595 / 0.336 / 0.218 | 0.244 / 0.134 / 0.139 | 0.133 |
| linear_softmax | **0.828** / **0.717** / 0.250 | 0.557 / 0.311 / 0.202 | **0.303** / **0.192** / **0.114** | 16.57 |
| tfidf_svm（上界对照） | **0.828** / **0.717** / — | **0.595** / **0.342** / 0.522 | 0.283 / 0.185 / 0.599 | 2.71 |
| bge_anchor（零样本） | 0.276 / 0.177 / 0.531 | 0.229 / 0.111 / 0.478 | 0.216 / 0.144 / 0.469 | 22.94 |
| qwen35_4b（零样本） | 0.690 / 0.366 / 0.461 | 0.527 / 0.318 / 0.378 | 0.417 / 0.302 / 0.334 | ≈800（含网络） |

注：linear_softmax 的 16.57 ms/条是纯 predict 延迟（1000×262144 权重矩阵）；E1 表里 143–238 ms/条是把训练时间摊进单条，不代表线上。tfidf_svm 无强度输出，tag-MAE 不可比（给 1.0 是缺省）。

### 4.2 逐视频（表 5，agreement，LOVO 训练）

| 视频（领域） | rules | centroid | softmax | svm | bge | qwen3.5:4b |
|---|---|---|---|---|---|---|
| BV1w5xh6sEEz（音乐） | 0.190 | 0.280 | 0.340 | 0.270 | 0.230 | **0.440** |
| BV1rCxe6wEN9（时政） | 0.170 | 0.200 | 0.140 | 0.210 | 0.180 | **0.340** |
| BV1bnxr6cEJ9（时政/对喷） | 0.224 | 0.299 | 0.403 | 0.343 | 0.328 | **0.493** |
| BV1mV4F6mE5Z（游戏） | 0.250 | 0.260 | 0.320 | 0.330 | 0.110 | **0.350** |
| BV1W18Y61Ee5（剧情） | 0.230 | 0.180 | 0.310 | 0.260 | 0.230 | **0.460** |

### 4.3 结论

- **C1（H1 成立）**：规则在同视频留出 131 条（按 dominant 分层，每类约 31 条）上 96 条判 other，agreement 0.168，甚至低于「全判 humor」的平凡基线 0.237——因为规则把 humor 也大量丢进 other。规则只能做高精度前置闸（如符号刷屏、露骨攻击），不能当主分类器。
- **C2（H2 成立）**：同视频内，线性模型持平甚至超过 4B 零样本 LLM（131 条上 centroid 0.595 vs qwen 0.527；人工金标上 softmax 0.828 vs qwen 0.690），而延迟低 4–5 个数量级。**「用 teacher 标签蒸馏出小模型」在同视频维度上是有效的。**
- **C3（H3 成立）**：换成 LOVO 协议后，所有决策器都掉到 0.14–0.49，且排序反转：qwen3.5:4b（0.417）> softmax（0.303）> svm（0.283）> centroid（0.244）> rules（0.213）> bge（0.216）。**跨领域时零样本 LLM 更稳，线性模型必须按视频重训。**
- **C4**：bge_anchor 在两种协议下都接近或低于 rules，说明「句向量 + 标签描述」这种零训练语义匹配抓的是话题而非社会功能，不能当分类器（与 §6 互相印证）。

---

## 5. E2：视频上下文特征（负结果）

脚本：scripts/experiments/run_context.py；结果：data/experiments/results/context.json；详版：docs/experiments/e2-context.md。

协议：LOVO 6 折，冻结 TF-IDF 向量器（char 1–3），把环境信息拼进每条弹幕的输入文本，比较 5 个变体。

| 变体 | 输入 | 跨视频平均 agreement | macro-F1 |
|---|---|---|---|
| V0 | 仅弹幕文本 | **0.3296** | **0.1336** |
| V1 | +视频标题 | 0.2567（−7.29pp） | 0.0804 |
| V2 | +标题+分区 | 0.2635（−6.61pp） | 0.0758 |
| V3 | +标题+时间位置 | 0.2801（−4.95pp） | 0.0878 |
| V4 | +标题+前后各 1 条弹幕 | 0.2590（−7.06pp） | 0.0630 |

补充变体（不含标题）：S2 仅分区 −7.27pp、S3 仅位置 −2.38pp、S4 仅邻条 −7.18pp，全部无正收益。

**机制诊断（关键）**：拼入上下文后，TF-IDF 对整段输入做 L2 归一化，上下文 token 占输入 tf-idf 质量的 78%–84%（V1 实测 0.785）；而 held-out 视频的标题/分区在训练语料中是 OOV，其哈希方向≈噪声且在同一视频内恒定 → 变成一个常数偏置，小 margin 分类器（max prob 0.11–0.21）被这个偏置整体推向同一个标签。典型塌缩：A 视频折 V1 agreement 0.6411 ≈ 其 humor 占比 0.64，98% 的行预测 humor，macro-F1 反而从 0.1408 掉到 0.0881。加权平均下 V1 会「显得」比 V0 高（0.4335），那是最大视频塌缩押中多数类的假象，不能作为正结果。

对照实验排除了实现问题：把 OOV 的 idf 改成训练集中位数后仍 86% 预测 humor；把 held-out 标题换成训练集标题仍 100% 单标签。

**结论**：**上下文/环境不能以「拼字符串」的方式进入词面模型**。要用上下文必须换融合方式（独立通道加权、分数层融合、或在 bootstrap 协议里让上下文在 fit 时可见）。当前生产环境线性模型保持 text-only，上下文交给 policy 层与 LLM。

---

## 6. E3：bge-m3 环境向量的相关性轴（负结果）

脚本：scripts/experiments/run_topicality.py；结果：data/experiments/results/topicality.json；详版：docs/experiments/e3-topicality.md。

假设：把「视频环境」（标题 + 分区）用 bge-m3 编码成一个环境向量，再算每条弹幕与它的余弦，就能判断「这条弹幕是否在聊本视频」（on-topic），从而处理跨领域。

| 方法（同一 180 条人工标注） | ROC-AUC | 95% CI |
|---|---|---|
| bge-m3 环境向量（标题+分区） | 0.5705 | [0.4777, 0.6501]（含 0.5） |
| 字符 bigram 重合基线 | 0.5545 | [0.5205, 0.5887] |
| TF-IDF 与标题余弦基线 | 0.5550 | [0.5214, 0.5890] |

外部效度（用 teacher 的 other / irrelevant_repetition 作负类，n=2,457，负类 241 条）：title-env AUC 0.5165（CI 含 0.5）；探索性 LOO 评论质心 0.5528；两者平均 0.5380。

逐视频手标 AUC 波动极大：Rick MV 0.692、故事视频 0.784、两个玩梗视频 0.440 / 0.450、时政 0.535、游戏 0.551。

诊断：正类余弦均值 0.4107 vs 负类 0.3966，差值仅 0.014；bge-m3 句向量存在强各向异性，全部分数被挤压在 0.257–0.568；与文本长度相关 r=0.12。

**结论**：标题+分区的环境向量**不足以支撑相关性过滤轴**，不建议上线为 hide/collapse 触发条件；弱证据的「评论质心」最多做 tie-break。换句话说，弹幕的「相关性」是视频语境 + 梗文化 + 时序共同决定的，不是标题语义相似度能覆盖的。

---

## 7. E4：同视频 bootstrap 规模曲线（部署核心）

脚本：scripts/experiments/run_scaling.py；结果：data/experiments/results/scaling.json。每个 k 跑 3 个 seed，测剩余 80 条。

| 视频（条数） | k=10 | k=25 | k=50 | k=100 | k=200 | k=400 |
|---|---|---|---|---|---|---|
| BV1GJ411x7h7（1,407，音乐） | 0.425 | 0.537 | 0.542 | 0.617 | **0.662** | 0.654 |
| BV1rCxe6wEN9（472，时政） | 0.138 | 0.208 | 0.233 | 0.279 | 0.317 | — |
| BV1mV4F6mE5Z（174，游戏） | 0.312 | 0.329 | 0.354 | — | — | — |
| BV1W18Y61Ee5（164，剧情） | 0.329 | 0.346 | 0.379 | — | — | — |
| BV1w5xh6sEEz（173，音乐） | 0.333 | 0.379 | 0.417 | — | — | — |

训练开销：centroid 在 1,407 行上 fit+predict 约 3–15 ms（曲线 predict_s 0.0011–0.0148 s，含 fit）；模型 80–3,000 KB（见 §9）。

**结论**：
1. 同视频 50 条 teacher 标签就能把 agreement 从「跨域 0.24」抬到 0.35–0.54，200 条到 0.66（音乐视频）。**冷启动成本很低**（50 条 LLM 标注 = 40 s 端侧 / 26 s 云端）。
2. 收益随 k 单调但边际递减，100–200 条是性价比拐点；1,407 条的视频 k=400 不再涨（0.654 vs 0.662），说明 teacher 噪声与标签难度成为瓶颈。
3. 视频越小、类别越偏（时政 0.138 起），bootstrap 越吃力；时政视频即使 k=200 也只有 0.317，提示「领域难度」比「模型容量」更决定上限。

---

## 8. E5：置信门控 + LLM 升级的成本/上界

脚本同上（scaling.json 的 escalation 段）。协议：L1 用同视频 200 条训练，测试 80 条；对 L1 分数 < θ 的条目改由 teacher 决定。表中「上限」= 假设 teacher 100% 正确时的 agreement（**这是上界，不是实测**，实测还要乘 teacher 质量 ≈0.9）。

| 视频 | L1 基线 | θ=0.2 | θ=0.3 | θ=0.4 | θ=0.45 | θ=0.5 | θ=0.6 |
|---|---|---|---|---|---|---|---|
| BV1GJ411x7h7 | 0.688 | 0.688 / 0% | 0.812 / 22.5% | 0.963 / 62.5% | 0.988 / 72.5% | 1.000 / 75% | 1.000 / 84% |
| BV1rCxe6wEN9 | 0.325 | 0.463 / 17.5% | 0.925 / 77.5% | 0.975 / 87.5% | 0.988 / 88.8% | 0.988 / 90% | 1.000 / 94% |

（每格：上限 agreement / 升级比例）

成本换算：升级比例 × 条数 × 0.8 s/条（qwen3.5:4b 端侧）。1,407 条视频在 θ=0.3 时 22.5% 升级 ≈ 317 条 ≈ 254 s；θ=0.45 时 72.5% ≈ 1,020 条 ≈ 816 s。云端 deepseek-flash 0.53 s/条则减半。

**读法**：升级确实能把质量推高，但**代价是大多数条目仍要过 LLM**——在时政视频上 θ=0.3 就要 77.5%。因此升级只应发生在「用户可见路径之外」（预取/后台），不能作为同步路径的依赖。线上当前实现取 θ=0.45，且只在未缓存条目上异步升级（先返回 L1 标签，后台替换）。

---

## 9. E6：延迟与模型体积

脚本：scripts/experiments/run_latency.py；结果：data/experiments/results/latency.json。

| 决策器 | 进程内 ms/条 | 说明 |
|---|---|---|
| rules | 0.060 | 200 条 |
| linear_centroid | 0.133 | 200 条 |
| linear_softmax | 16.57 | 200 条，1000×262144 权重 |
| tfidf_svm | 2.71 | 200 条，sklearn |
| bge_anchor | 22.94 | 40 条，含 bge-m3 前向 |
| qwen35_4b | ≈800 | E1 实测 772–964 ms/条（batch 8，含网络） |
| deepseek_flash | ≈530 | E1 实测 0.53 s/条 |

HTTP 端到端（服务端 /api/v1/decisions，300 条未缓存弹幕，linear 后端）：总 120.2 ms，**0.401 ms/条**。

模型体积（data/models/）：

| 文件 | KB |
|---|---|
| _global.json（2,815 行，11 标签） | 7,156.7 |
| BV1GJ411x7h7.json | 2,995.6 |
| BV1rCxe6wEN9.json | 1,269.0 |
| BV1w5xh6sEEz / BV1PF4g6ME26 / BV1mV4F6mE5Z / BV1bnxr6cEJ9 | 79.9 / 80.8 / 123.3 / 196.3 |
| 600 行训练的 centroid 对照 | 501.9 |

**结论**：L1 的延迟比「规则级」差 2–4 倍（0.13 ms vs 0.06 ms），但相对 LLM 低 4 个数量级，对 300ms 的 DOM 兜底 tick 完全够用；体积 80 KB–3 MB，可随扩展下发。softmax 的 16 ms 属于可接受但不再划算（centroid 质量差 3–6 pp，延迟差 125 倍）。

---

## 10. E7：跨视频失败是不是「标签先验漂移」？

脚本：scripts/experiments/run_adaptation.py；结果：data/experiments/results/adaptation.json。协议：训练 = 其余 5 个视频（上限 2,000 条），测试 = 本视频 120 条，比较 4 种适应手段。

| 视频（条数） | L1 基线 | oracle 先验校正 | few-shot 20 条先验校正 | EM 自训练 | 多数类 oracle 基线 |
|---|---|---|---|---|---|
| BV1GJ411x7h7（1,407） | 0.275 | 0.233 | **0.367** | 0.000 | 0.233 |
| BV1w5xh6sEEz（173） | 0.275 | 0.275 | 0.283 | 0.000 | 0.283 |
| BV1rCxe6wEN9（472） | 0.208 | 0.150 | 0.150 | 0.150 | 0.150 |
| BV1mV4F6mE5Z（174） | 0.233 | 0.017 | 0.283 | 0.017 | 0.283 |
| BV1W18Y61Ee5（164） | 0.275 | 0.142 | 0.267 | 0.000 | 0.258 |

**结论**：
1. 只用先验校正（即使给了真实先验）不但没救，常常更差——跨视频失败**不只是先验漂移**，而是「同一个词在不同领域的社会功能不同」+ 词表 OOV。
2. few-shot 20 条先验校正最多回到「多数类基线」附近（0.28 vs 0.28），说明 20 条标签主要编码了先验，没教会语义。
3. EM 自训练在强分布不匹配下崩塌（预测全塌到同一类，0.0）。
4. 因此**必须换训练数据，而不是调决策规则**：这就是 §7 的按视频 bootstrap（50–200 条）成为唯一有效路径的原因。

---

## 11. 错误分析（定性）

脚本：scripts/experiments/dump_errors.py；结果：data/experiments/results/error_dump.json。取同视频 200 条训练的 centroid 模型在 120 条测试上的错误样例。

**模式 A：幽默 ↔ 共鸣 ↔ 信息的三角边界**（占多数）

| 弹幕 | gold | 预测 |
|---|---|---|
| 转眼90年代上学单身狗变身中老年社畜了…… | emotional_resonance | humor 0.275 |
| 这首歌很适合写作业时听！让人专心 | information | emotional_resonance 0.357 |
| 这首歌是哪年的？ | other | emotional_resonance 0.379 |
| 真的有这么好笑吗 | conflict | information 0.545 |

**模式 B：符号/排版类无意义重复与幽默混淆**

| 弹幕 | gold | 预测 |
|---|---|---|
| 愿   者   上   钩 | humor | irrelevant_repetition 0.368 |
| 范特西~ | irrelevant_repetition | humor 0.277 |
| C y t u s 3 | humor | irrelevant_repetition 0.335 |
| 哈哈哈哈哈 | irrelevant_repetition | humor 0.516 |

**模式 C：时政里的攻击/立场与「信息」混淆**（跨域，最严重）

| 弹幕 | gold | 预测 |
|---|---|---|
| 满脑子炒股，活该输光 | attack | humor 0.231 |
| 欧洲之春 | political | humor 0.207 |
| 美国真是个世界毒瘤，搞的油价飙升 | attack | information 0.283 |
| 一言堂 | political | irrelevant_repetition 0.235 |

**模式 D：游戏域短句缺乏上下文**

| 弹幕 | gold | 预测 |
|---|---|---|
| 架水桶 | information | humor 0.548 |
| 老黑有队友 | information | humor 0.579 |
| 再戴个机枪兵的自闭头，完美 | humor | information 0.517 |

四类模式对应四个改进方向：模式 A/B 是标签定义边界问题（prompt 与标注指南要更严）；模式 C 是领域词表/先验问题（要按视频 bootstrap 或加领域样本）；模式 D 是「短句无上下文」问题（需要邻条或画面信息，而 §5 表明拼接无效，须换融合方式）。

---

## 12. 讨论：那「NLP 不懂环境」到底怎么解？

这是本报告要回答的核心问题。实验给出的答案分三层：

**第一层：环境不能被「猜」出来，只能被「给」进去。**
- 拼标题/分区（§5）：有害，−5 到 −7pp。
- 环境向量相似度（§6）：AUC 0.571，不显著，且低于平凡基线组合。
- 原因：弹幕的「相关性/社会功能」由 视频语境 × 梗文化 × 时间点 共同决定，而标题只是其中极弱的一个投影；用通用句向量去对齐标题，本质是在测「话题相似」而不是「这条弹幕在干什么」。

**第二层：真正有效的是「每个视频的少量标注」+「显式的用户策略」。**
- 同视频 50–200 条 teacher 标签 → 线性模型 agreement 0.35–0.66，延迟 0.13 ms（§7、§9）。
- 用户策略（每个标签的 显示/折叠/隐藏、阈值、密度）是「环境」的显式注入：同一个 political 标签在时政视频里用户可能想折叠，在别的视频里可能想显示——这不该由模型猜，应该由用户设。
- 这也是 relevance 向量（而非单标签）的意义：用户能对「我不关心的维度」设阈值，而不需要模型理解「我不关心什么」。

**第三层：LLM 负责 zero-shot 兜底与新标签扩展，不负责线上吞吐。**
- 零样本时 LLM 是跨域最稳的决策器（LOVO 0.417 vs 线性 0.303）（§4）。
- 新增一个标签（如 resource_request）只需在 taxonomy 加定义 + 几条例子，实测 8 条里 3 条命中、其余不变——**不需要重新训练**。
- 但 0.8 s/条 不能进同步路径，只能进预取/后台升级（§8）。

**落地架构（已在仓库实现）**：
1. 打开视频 → 拦截 segment → 若已有该 bvid 模型，L1 打分（0.4 ms/条 HTTP）；
2. 若无模型，规则先行 + 入队 bootstrap（抽 50–200 条 → teacher → 训练 → 落盘）；
3. L1 低置信（<0.45）条目异步交 teacher 升级，下次请求替换；
4. policy 层按用户的 per-tag 策略 + 密度窗口做 show/hide/collapse；
5. 反馈闭环：用户的「恢复显示/改标签」写回 overrides，作为下一轮训练的优先样本。

---

## 13. 威胁与局限

1. **标签源单一**：全数据集 teacher = deepseek-flash，无独立双人标注；teacher 与人工一致率 0.897（29 条），因此所有以 teacher 为 gold 的数字都有 ≈10% 的上限噪声，且 prompt 偏差会被下游模型继承。
2. **人工样本小**：29 条（标签质量）、180 条（相关性）、单标注者、无 κ；180 条的 95% CI 约 ±0.18，逐视频结论不能过度解读。
3. **视频数少**：6 个视频、4 类领域、单平台（B 站）、中文；LOVO 的「跨域」只覆盖 4 类，不能外推到全站。
4. **升级上界是模拟**：§8 的 ceiling 假设 teacher 全对，实测会低 ≈10%（§3）。
5. **密度/策略层的效果未做用户实验**：本报告只到「打标准确率 + 延迟」，没有测用户主观体验、误杀率、信任度（属于 Phase 1/3 的工作）。
6. **时间戳缺失**：B 视频的 time_ms 多为 0（早期 rules-online 缓存），位置特征只能用行序代理（§5 的 V3 因此只算弱证据）。
7. **bge-m3/LLM 版本漂移**：端侧模型与云端模型都会更新，重复实验需固定版本（见附录 B 的环境变量）。

---

## 14. 结论与后续工作

**结论**：
1. 关键词规则不足以做弹幕社会功能分类（同视频 0.168），只能做高精度前置闸。
2. 用 LLM teacher 标签蒸馏出的线性模型，在**同视频**维度上质量接近甚至超过 4B 零样本 LLM，而延迟低 4 个数量级——这是可部署的形态。
3. 跨视频不迁移，且不能靠「上下文特征」或「环境向量」补救；必须按视频 bootstrap 50–200 条。
4. 置信门控升级可以把质量推向上界，但成本高，只能放在预取/后台。
5. 「环境」的正解是「每视频标注 + 用户显式策略 + LLM 零样本兜底」三者组合，而不是让通用 NLP 模型自己理解环境。

**后续工作（按优先级）**：
1. 难例补标：把升级比例（当前 θ=0.45 时 62%–88%）用 active learning 降下来（挑 margin 最小/分歧最大的条目补标，目标同一质量下升级比例减半）。
2. 每视频模型与 _global 融合：小视频（k<50）时用 _global 先验 + 视频内微调权重，当前二者是硬切换。
3. 上下文用「分数层融合」重做一遍（而非拼字符串），验证 §5 的负结果是否只是融合方式问题。
4. 相关性轴改用「评论区质心 + 时间窗」组合（§6 唯一有弱证据的方向），并做双人标注验证。
5. 用户实验：密度/折叠策略对体验与误杀的影响（Phase 1/3）。

---

## 附录 A：产物清单

| 文件 | 内容 |
|---|---|
| scripts/experiments/prepare_dataset.py | 冻结数据集（6 视频 / 2,457 行） |
| scripts/experiments/common.py | 指标、抽样、6 类决策器、LLM 缓存 |
| scripts/experiments/run_matrix.py | E1 决策器横评 |
| scripts/experiments/run_context.py | E2 上下文特征（server-dev 交付） |
| scripts/experiments/run_topicality.py | E3 环境向量（userscript-dev 交付） |
| scripts/experiments/run_scaling.py | E4 bootstrap 曲线 + E5 升级阈值 |
| scripts/experiments/run_latency.py | E6 延迟与体积 |
| scripts/experiments/run_adaptation.py | E7 先验漂移/适应 |
| scripts/experiments/dump_errors.py | 错误样例导出 |
| data/experiments/dataset.jsonl | 冻结数据（sha256 b182f3cd…） |
| data/experiments/results/matrix.json | E1 结果 |
| data/experiments/results/context.json | E2 结果 |
| data/experiments/results/topicality.json | E3 结果 |
| data/experiments/results/scaling.json | E4 + E5 结果 |
| data/experiments/results/latency.json | E6 结果 |
| data/experiments/results/adaptation.json | E7 结果 |
| data/experiments/results/error_dump.json | 错误样例 |
| data/experiments/results/label_quality.json | E0 结果 |
| docs/experiments/e2-context.md | E2 详版报告 |
| docs/experiments/e3-topicality.md | E3 详版报告 |

## 附录 B：复现命令

~~~bash
cd /home/alphayang/bili-tagging
export PYTHONPATH=.

# 0) 端侧 LLM（可选，E1 的 qwen / E3 的 bge-m3 需要）
#    Windows Ollama 已监听 127.0.0.1:11434；WSL2 可直接访问
export DM_LLM_BASE_URL=http://127.0.0.1:11434/v1
export DM_LLM_MODEL=qwen3.5:4b
export DM_LLM_OLLAMA=1 DM_LLM_NUM_CTX=8192 DM_LLM_BATCH=8 DM_LLM_TEMP=0.2
export DM_LLM_TIMEOUT=600
export DM_LLM_ENV_FILE=/nonexistent/dmf.env   # 避免读到 .env.local 里的云端 key

python3 scripts/experiments/prepare_dataset.py     # 冻结数据集（需要云端 key 才重打标）
python3 scripts/experiments/run_matrix.py --stage local   # E1（约 25 分钟，qwen 部分走缓存后 <5 分钟）
python3 scripts/experiments/run_context.py         # E2（约 6 秒）
python3 scripts/experiments/run_topicality.py      # E3（约 1 分钟）
python3 scripts/experiments/run_scaling.py         # E4+E5（约 2 分钟）
python3 scripts/experiments/run_latency.py         # E6（约 1 分钟）
python3 scripts/experiments/run_adaptation.py      # E7（约 1 分钟）
python3 scripts/experiments/dump_errors.py         # 错误样例
~~~

## 附录 C：指标定义

- agreement = (1/N) Σ 1[pred_i = gold_i]
- macro-F1 = 对 gold 中出现的每个标签计算 F1，再取算术平均（未预测的标签 F1=0）
- tag-MAE = (1/N) Σ_i (1/11) Σ_l |pred_i(l) − gold_i(l)|
- intensity MAE = 只对 dominant 标签的强度比较（E0）
- ROC-AUC（E3）= 以人工 on-topic 标注为 gold，分数为余弦相似度
- 95% CI = 1,000 次 bootstrap 重采样（E1/E3）或 3 seed 的 min/max（E4）
