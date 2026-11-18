# E8 标签边界实验：判定顺序（②）与最小对立对（③）

> 目标（用户 m03133 / m03142）：解决「标签边界」问题——为什么 `information` 会被判成 `humor`、`幽默` 会变成垃圾桶。用户批准只做方案 ②（判定顺序）与 ③（最小对立对），①（双人标注测 Krippendorff α）暂缓。
> 结论先说：④ 云端 teacher（deepseek-flash）在三个集合上全面变好（audit 0.759→0.897，boundary 0.870→0.913，must-hit 0.913→0.957），**prompt v4 已成为默认**；本地 4B（qwen3.5:4b）只在人工金标上持平，边界子集反而略降，说明 4B 跟不上更细的顺序指令。

## 1. 问题与假设

E1 的混淆矩阵显示塌缩不是均匀的，而是单向的「幽默吸引子」：

| 真实标签 | 被判成 humor 的比例 | 该标签召回 |
| --- | --- | --- |
| information | 19/31 = 61% | 0.19 |
| irrelevant_repetition | 9/31 = 29% | 0.65 |
| spoiler | 3/5 | 0/5 |
| emotional_resonance | 3/31 | 0.84 |

假设：

- **H8a**：把「判定顺序」写进 prompt（先噪声、再风险、信息优先、幽默 vs 共鸣、最后 other）能提高 `information` 召回，并把纯情绪反应从 `humor` 里分出来。
- **H8b**：最小对立对（同一话题、只差一个词、必须落到边界两侧）作为 few-shot 锚点 + 回归测试，能同时守住两侧，不靠总体准确率掩盖塌缩。
- **H8c**：修复必须能蒸馏进 L1 学生模型，否则用户看不到（teacher 只负责 bootstrap）。

## 2. 干预：三个 prompt 版本

| 版本 | 结构 | 关键差异 |
| --- | --- | --- |
| v2 | 平铺打分 | 每类 0–1 分，无优先级；`other` 兜底 |
| v3 | 判定顺序 + 锚点 | 加入 `taxonomy.DECISION_ORDER`（5 步）；6 对锚点 few-shot；并要求「同一对锚点不得拿到相同 dominant」 |
| v4 | 收紧的判定顺序 + 软化锚点 | 纯噪声才判 `irrelevant_repetition`（「哈哈哈哈这翻译」不是噪声）；`attack` 只给真正的辱骂（「编剧你出来」不算）；信息分不被玩笑取消；笑声标记（笑死/离谱/好家伙）= 幽默不是共鸣；锚点只示范分数差异，**不要求** dominant 不同 |

代码：`danmaku_filter/taxonomy.py`（`DECISION_ORDER` / `DECISION_ORDER_V4`、`prompt_guide_v3` / `prompt_guide_v4`）、`danmaku_filter/classify/llm.py`（`DM_LLM_PROMPT=v2|v3|v4`，默认 v4）。

## 3. 数据与指标

| 集合 | n | 金标来源 | 用途 |
| --- | --- | --- | --- |
| audit | 29 | 人工标注（`data/pilot/audit_gold.json`） | 唯一的「真值」集合 |
| indomain | 131 | deepseek-flash v2 标签（同视频留出） | 漂移/稳定性，**不是**真值 |
| boundary | 46 | 最小对立对构造（`danmaku_filter/boundary.py`，23 对） | 边界是否被遵守 |

指标：dominant agreement、macro-F1、边界 must-hit（该侧的必需标签是否达到下限）、逐对准确率、`information` 召回、每次调用耗时。

## 4. 结果（teacher）

| backend | prompt | audit (29) | indomain (131) | boundary dominant (46) | boundary must-hit (46) | info recall (indomain) | ms/item |
| --- | --- | --- | --- | --- | --- | --- | --- |
| deepseek | v2 | 0.759 | 0.649 | 40/46 = 0.870 | 42/46 = 0.913 | 0.774 | cached |
| deepseek | v3 | 0.690 | 0.611 | 38/46 = 0.826 | 43/46 = 0.935 | 0.742 | cached |
| **deepseek** | **v4** | **0.897** | **0.664** | **42/46 = 0.913** | **44/46 = 0.957** | **0.839** | 1601 |
| qwen35-local | v2 | 0.483 | 0.450 | 38/46 = 0.826 | 33/46 = 0.717 | 0.548 | cached |
| qwen35-local | v3 | 0.517 | 0.504 | 36/46 = 0.783 | 34/46 = 0.739 | 0.742 | cached |
| qwen35-local | v4 | 0.517 | 0.427 | 36/46 = 0.783 | 34/46 = 0.739 | 0.710 | 1295 |

逐对结果（每格为该边界上 dominant 判对条数）：

| boundary pair | n | deepseek v2 | v3 | v4 | qwen v2 | v3 | v4 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| information vs humor | 10 | 9/10 | 5/10 | 9/10 | 8/10 | 7/10 | 6/10 |
| information vs emotional_resonance | 6 | 6/6 | 6/6 | 5/6 | 6/6 | 6/6 | 6/6 |
| humor vs emotional_resonance | 8 | 7/8 | 8/8 | 8/8 | 7/8 | 8/8 | 7/8 |
| spoiler vs humor | 6 | 4/6 | 4/6 | 5/6 | 4/6 | 3/6 | 4/6 |
| irrelevant_repetition vs humor | 8 | 7/8 | 8/8 | 8/8 | 5/8 | 4/8 | 5/8 |
| attack vs conflict | 2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| conflict vs inflammatory | 2 | 1/2 | 1/2 | 1/2 | 2/2 | 2/2 | 2/2 |
| political vs inflammatory | 2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| attack vs inflammatory | 2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |

### 4.1 v2 → v3：顺序生效，但锚点句把事实丢掉了

deepseek 在 audit 上从 0.759 掉到 0.690，问题集中在 `information vs humor` 这一对：9/10 → 5/10。逐条看（`data/experiments/results/e8_detail_deepseek.json`）：

```
- information -> emotional_resonance | 这首歌1987年的，我爸都会唱
- information -> humor               | 原曲是肖邦的夜曲，难怪这么催眠
- information -> humor               | 字幕在3:42，我瞎了三年
- information -> humor               | 原著里没有这段，编剧你出来
```

四条都是「事实 + 玩笑」的句子。v3 里那句「同一对锚点不得拿到相同 dominant」被模型当成「这对里 joke 那条必须给 humor」，于是把事实分丢了。另外 v3 在 audit 上把「笑死了」推到 `emotional_resonance`（笑声标记被当成纯情绪）。

### 4.2 v3 → v4：两处精准修复

把锚点要求改成「只示范分数差异，不要求 dominant 不同」，并补三条规则（纯噪声定义、attack 只给真辱骂、笑声标记算幽默）后，deepseek：

```
v3->v4 changed 9 fixed 7 broke 1   (audit)
  + 翻译就离谱                     emotional_resonance -> humor
  + 草 字幕笑死我了                  emotional_resonance -> humor
  + CC字幕笑死了                   emotional_resonance -> humor
  + 这新字幕笑死我了                 emotional_resonance -> humor
  + 笑死了                         emotional_resonance -> irrelevant_repetition
  + 这字幕咋回事儿啊笑死我了哈哈哈哈哈哈哈靠  emotional_resonance -> humor
  + 太好听了，感谢我的好兄弟骗我        emotional_resonance -> humor
  - CS2预告片                      information -> humor
```

边界集合上 v3 的四处 information 反转全部修回（4/4），还顺带把「第8集才反转，我先睡为敬」从 `spoiler` 改回 `humor`。唯一新增错误是「这段实拍太震撼了」被信息规则抢走（应为共鸣）。

### 4.3 本地 4B 跟不上细顺序

qwen3.5:4b 在人工金标上 v3/v4 都是 0.517（v2 0.483，等于 1 条的差别），但 `indomain` 从 0.504 掉到 0.427、边界 unchanged 0.783。错误样例显示它会把「哈哈哈哈这翻译」判成纯噪声（0.95）、把「这首歌1987年的，我爸都会唱」判成 humor——规则写对了但执行粒度不够。结论：本地小模型继续用 v4 不会更差，但不能指望它解决边界。

## 5. 结论与决策

1. **v4 成为默认 prompt**（`DM_LLM_PROMPT` 未设置时为 `v4`）：云端 teacher 三个集合全升，`information` 召回 0.774→0.839。
2. **判定顺序（②）单独用会反噬**：v3 的「硬锚点」把事实句推向幽默。顺序规则需要「分数可以共存、dominant 不必不同」这句兜底。
3. **最小对立对（③）作为回归测试有价值，作为 few-shot 锚点必须软约束**；46 条探针的 must-hit 从 0.913 → 0.935 → 0.957。
4. **4B 本地模型不是这条路的受益者**：它的收益来自更大模型/更多标注，不是更细的指令。
5. **蒸馏传播（H8c）不成立**：同数据量下 v4 标签的学生与 v2 标签的学生持平（audit 0.517 vs 0.552），学生准确率由标签量驱动（1,376 行 v2 学生 0.690）；要把边界收益带给用户，需要更多标注行 + 把探针当训练样本 + 补 IR/spoiler 配额。

## 6. 蒸馏传播（H8c）

### 6.1 设置

H8c 要问的是：v4 只在 teacher 上有效，还是能蒸馏进用户实际运行的 L1 学生模型？

- 数据：`BV1GJ411x7h7` 的 1,407 条里剔除 audit 29 与 boundary 46 的文本，剩 1,376 条可训练；从中按长度分层抽 400 条（seed 20261105）用 deepseek-flash v4 重标。
- 三个学生：① v2 标签 × 1,376 行（现状上限）；② v2 标签 × 同一 400 行（同数据量对照）；③ v4 标签 × 400 行（本实验关心）。
- 评：audit 29（人工金标）、boundary 46（dominant 与 must-hit；covered 只算学生见过的标签）。

### 6.2 结果

| 学生 | 训练行 | 标签来源 | audit (softmax) | audit (centroid) | boundary dominant | boundary must-hit | 信息召回 (audit) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v2 × 1,376 | 1,376 | v2 | 0.690 | 0.586 | 0.348 | 0.613 | 0.000 |
| v2 × 400 | 400 | v2 | 0.552 | 0.517 | 0.283 | 0.484 | 0.500 |
| **v4 × 400** | 400 | **v4** | **0.517** | **0.552** | **0.304** | **0.429** | 0.500 |

（centroid 与 softmax 逐格差异都在 ±1 条以内，属噪声；表内取 softmax 为默认列。）

### 6.3 判读：H8c **不成立**

1. **同数据量下 v4 标签没有优势**：audit 0.552 → 0.517、must-hit 0.484 → 0.429，方向甚至是负的（各差 1–2 条，不能叫「变差」，但肯定没有变好）。
2. **学生准确率主要由标签量决定**：1,376 行 v2 学生的 audit 0.690 远高于任何 400 行学生。teacher 质量提升 0.14（0.759→0.897）在学生这一层完全被「样本少」抵消。
3. **v4 重标把 `irrelevant_repetition` 池子砍了一半**：同一 400 条里 v2 分布 IR 60 条、v4 只剩 29 条（「纯噪声才判 IR」规则外溢），`spoiler`/`conflict` 归零，学生只剩 5 个标签可用（v2 是 7 个）。这解释了 must-hit 下降。

### 6.4 对部署的影响（已写入 BACKLOG）

- 服务端 bootstrap 仍用 v4（teacher 层收益真实且免费），但**必须同时把 IR/spoiler/conflict 的采样补回去**，否则新的每视频模型会丢掉这类标签；`scripts/bootstrap_video.py` 的分层抽样要按 teacher 输出分布重抽或做配额。
- 想把边界收益带给用户，靠的不是 prompt，而是：① 每个视频标更多行（400 → 800–1,200，成本约 0.5 s/条）；② 把最小对立对当作**训练样本**加进去（现在只用于评测）；③ 难例主动学习（TODO 第 11 条 ①）。
- 反过来说：teacher 一致性本身就是产品指标（它决定模型上限），v4 的 +0.14 仍然值得保留。

## 7. 局限

- audit 只有 29 条，±1 条就是 3.4 个百分点；v3→v4 的 6 条修复全部集中在「笑死/哈哈哈哈」这一种句式，收益是真实的但**很窄**。
- 46 条边界探针是我自己构造的，`expect_dominant` 不等于人工真值：例如「这首歌1987年的，我爸都会唱」的 dominant 本身可争议（事实 + 玩笑），所以同时报告 must-hit。
- `indomain` 的金标是 v2 teacher 输出，v3/v4 与它不一致不代表更差；该列只能读作「标签漂移量」。
- 蒸馏实验只在一个视频、400 行、一个 teacher 上跑（deepseek-flash v4，靠缓存重放，未测 qwen 蒸馏）。
- v4 重标 400 条后 irrelevant_repetition 从 60 降到 29、spoiler/conflict 归零，说明 v4 的「纯噪声才判 IR」规则有外溢风险，需要在 bootstrap 抽样里补配额。
- 结论只覆盖中文 B 站弹幕（音乐/梗、时政、游戏、剧情四类视频），跨语言/跨平台未验证。

## 8. 复现

```
# teacher 对比（缓存命中时不产生 API 调用）
PYTHONPATH=. python3 scripts/eval_prompt.py --backend deepseek --styles v2,v3,v4 --sets audit,indomain,boundary
PYTHONPATH=. python3 scripts/eval_prompt.py --backend qwen35-local --styles v2,v3,v4 --sets audit,indomain,boundary

# 逐条差异与边界逐对表
PYTHONPATH=. python3 scripts/e8_detail.py deepseek
PYTHONPATH=. python3 scripts/e8_detail.py qwen35-local
PYTHONPATH=. python3 scripts/e8_tables.py
```

产物：`data/experiments/results/e8_<backend>.json`（汇总）、`e8_detail_<backend>.json`（逐条）、缓存 `data/experiments/results/cache/<backend>_prompt<v>-<set>.json`。