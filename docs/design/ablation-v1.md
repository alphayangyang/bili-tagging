# 消融 v1：规则基线 vs 端侧 LLM（qwen3.5:4b）

> 目的：回应用户 m02118「不要做规则匹配，因为太窄了，做一下消融试试？为什么不能用端侧的 llm，比如 Windows 的 ollama 拉个 qwen 3b」。
> 结论先行：规则确实太窄（一致率 0.19–0.35），4B 端侧模型明显更好（0.44–0.59），但离云端大模型（0.897）仍差一截，且延迟 0.75–0.83 s/条——蒸馏到 embedding + 小头才有速度优势。

## 1. 数据

| 集合 | 规模 | 来源 | 用途 |
|---|---|---|---|
| human | 29 条 | `data/pilot/audit_gold.json`（人工盲判：标签 + 强度 0–1），经 `scripts/make_ablation_sets.py` 转成 `{text,label,intensity}` | 真实人工金标，测「像不像人」 |
| teacher | 131 条 | `data/tags/BV1GJ411x7h7.json`（deepseek-flash 标签）按 dominant 分层抽样 | 测「像不像云端 teacher」，蒸馏的代理目标 |

两套标签集由 `scripts/make_ablation_sets.py` 生成到 `/tmp/ablation/{human,teacher}.jsonl`；评测脚本 `scripts/ablation.py`。

## 2. 运行环境（关键坑）

- 本机：WSL2 Ubuntu 26.04 + RTX 5060 Laptop 8 GB；Ollama 跑在 Windows，WSL2 mirrored networking 下直接访问 `http://127.0.0.1:11434`（不需要另开 11435 实例）。
- 模型：`qwen3.5:4b`（3.32 GB Q4）、`DM_LLM_NUM_CTX=8192`、`batch 8`、`temp 0.2`、`think false`。
- **必须走 Ollama 原生 `/api/chat`**：OpenAI 兼容的 `/v1` 忽略 `options`，一律按模型默认上下文（qwen3 是 40k）加载 → KV cache 4 GB+ → 8 GB 显卡 `cudaMalloc failed: out of memory`。代码用 `DM_LLM_OLLAMA=1`（或自动识别 11434/11435 端口）切原生通道。
- **native 请求不要带 `format: "json"`**：Ollama 会把输出约束成「单个 JSON 值」，小模型只返回第 1 条，其余全兜底成 other 0.5。改为 prompt 里要求「长度 = 输入条数的 JSON 数组」。

复跑：

```bash
cd /home/alphayang/bili-tagging
export DM_LLM_BASE_URL=http://127.0.0.1:11434/v1 DM_LLM_MODEL=qwen3.5:4b \
       DM_LLM_OLLAMA=1 DM_LLM_NUM_CTX=8192 DM_LLM_BATCH=8 DM_LLM_TEMP=0.2 \
       DM_LLM_TIMEOUT=600 DM_LLM_ENV_FILE=/nonexistent/dmf.env
python3 scripts/ablation.py --set /tmp/ablation/human.jsonl --backends rules,llm \
  --batch 8 --context 'Bilibili music/meme video, Chinese danmaku' \
  --out /tmp/ablation/human_qwen35.json
```

## 3. 结果

### 3.1 human（29 条人工金标）

| 后端 | 主标签一致率 | macro-F1 | 延迟 |
|---|---|---|---|
| rules | 0.345 | 0.180 | 0 ms（纯字符串匹配） |
| qwen3.5:4b（prompt v1） | 0.586 | 0.226 | 799 ms/条 |
| qwen3.5:4b（prompt v2） | 0.517 | 0.235 | 752 ms/条 |
| deepseek-flash（对照，此前 audit） | **0.897** | — | 530 ms/条（batch 100） |

### 3.2 teacher（131 条 deepseek 标签）

| 后端 | 一致率 | macro-F1 | 标签 MAE | Spearman | 延迟 |
|---|---|---|---|---|---|
| rules | 0.191 | 0.106 | 0.503 | 0.074 | 0 ms |
| qwen3.5:4b（prompt v1） | 0.443 | 0.219 | 0.392 | 0.266 | 821 ms/条 |
| qwen3.5:4b（prompt v2） | **0.488** | 0.219 | **0.364** | **0.301** | 825 ms/条 |

注：29 条样本上 ±2 条即 ±0.07，prompt v1/v2 的差异在人金标上基本是噪声；teacher 集上 v2 稳定更好（一致率 +0.045、MAE −0.028、ρ +0.035）。

### 3.3 prompt v2 改了什么（据 29 条逐条错误分析）

1. 幽默需要真的好笑（梗/双关/反转/荒诞比喻）；纯正面反应（太好听了 / 好感动 / 泪目）算共鸣不算幽默。
2. 符号刷屏（？？？、。。。。、1111、kkkk、纯 emoji）算无意义重复 ≥0.8，不许丢给 other。
3. 剧透只在「说出具体剧情/结局（谁死了、凶手是谁、谁客串）」时给分；只把片名和「大结局 / 预告 / 剧透」当梗说不算。

## 4. 错误分析（29 条，prompt v1 逐条 dump）

- **剧透过标 5 条**：《龙族》大结局 / 星海镖师大结局 / 名侦探柯南大结局 … 人工判 humor（玩梗），模型给 spoiler 0.4。
- **标点刷屏丢给 other 2 条**：`？？？？？？？`、`..................................` → other 0.5（应为无意义重复）。
- **幽默当垃圾桶**：`这个翻译是官方的吗？！`（人工 information）→ humor 0.2；`CS2预告片`（人工 information）→ humor 0.3；`我是自愿的`（人工 humor）→ other 0.3。
- **短正面情绪**：`太好听了，感谢我的好兄弟骗我`（人工 humor）→ 共鸣 0.8（这条人工也在调侃，属边界）。
- **纯参数串**：`["0.05","0.1","1-1","4.02",...]`（人工无意义重复）→ other 0.9。

## 5. 结论与下一步

- 规则基线被证伪：teacher 集 131 条里 94 条（72%）落 other，人工集一致率仅 0.345 —— 用户「规则太窄」的判断成立。
- 4B 端侧可用（标签质量、JSON 100% 合法、显存 3.34 GB 全 GPU），但精度 0.44–0.59，且 0.8 s/条并不比云端快（qwen3.5 的 decode 只有 ~80 tok/s，batch 8 也只有 0.75 s/条）。
- 所以真正的速度/精度双赢只有路线 A 蒸馏：teacher（deepseek-flash）标签 → bge-m3 embedding + 每标签小头，CPU 上目标是 ms 级。当前 teacher 只有 ~1.9k 条（音乐视频 1,407 + 时政视频 472），不够训 9 个头，先扩到 6–10k。
- 4B 目前的正确定位：本地兜底/隐私场景的在线打标（`serve --online-backend llm`），不是主路径。
## 6. 零样本补充实验（2026-11-05，回应「不想训练、要 zero-shot」）

用户要求：不希望训练，否则用户新增标签就要重新训练。因此额外测了两件事。

### 6.1 bge-m3 标签描述零样本（不训练，纯 embedding 相似度）

- 做法：每个标签写 5 条中文描述/例句做锚点，弹幕与锚点取 max 余弦，argmax 作为主标签。
- 结果：`human` 29 条一致率 **0.310**、`teacher` 131 条 **0.290**（正确标签相似度均值 0.588 vs 其它 0.496，区分度只有 0.09）。
- 结论：**不够用**。句向量抓的是话题，不是「社会功能」；精度与规则基线（0.19–0.35）同档，不能当主决策器。
- 它的价值在速度：bge-m3 1024 维，30 条批量 **0.23 s（7.6 ms/条，含 HTTP 开销）**。

### 6.2 LLM prompting 天然是 zero-shot（新增标签实测）

- 在 prompt 里临时加第 11 个标签「resource_request 求资源（求BGM/原图/字幕/片名/链接）」，**不改代码、不训练**。
- 8 条测试弹幕：3 条求资源全部命中（1.0），其余 5 条原有标签也全对（humor / spoiler / 共鸣 / 无意义重复 / attack）。
- 成本：qwen3.5:4b 8 条 9.4 s（本次 prompt 未省略低分项，生产 prompt 会省略，速度更快）。
- 结论：**用户自定义标签 = 往 taxonomy/prompt 里加一行定义 + 两三个例子**，即时生效，无需重新训练。

### 6.3 对路线的修正

- 取消「训练 per-tag 头」的蒸馏步骤（原 plan-v2 路线 A）：它与 zero-shot 扩展性冲突。
- 保留 LLM zero-shot 作为唯一决策器；速度靠 **离线批量 + 缓存 + 预取 + 运行时异步升级**，而不是靠训练小头。
- bge-m3 降级为可选加速（例如给「是否无意义重复」这类粗活做快速先验），不进主链路。
## 7. 速度实验（2026-11-05，回应「LLM 延迟高，要接近规则级」）

### 7.1 延迟的根因是「输出 token 数」，不是模型大小

qwen3.5:4b 在 8 GB 笔记本 GPU 上 decode 约 80 tok/s，prompt 只占 90 ms，**时间几乎全花在生成输出上**：

| 输出格式 | 每条输出 token | 延迟（warm） | human 一致率 | teacher 一致率 |
|---|---|---|---|---|
| 多标签 JSON 向量（现有） | ~40 | 750–820 ms/条 | 0.517–0.586 | 0.488 |
| 单标签（一行一条 `1.humor`） | ~3 | **66–72 ms/条** | 0.483 | 0.221 |
| 前二标签紧凑格式 `1.humor:0.9,共鸣:0.3` | ~10 | 180–220 ms/条 | 0.483 | 0.221 |

- **12 倍加速**来自把输出从「整条 JSON 向量」压成「一个标签名」；但多标签信息丢失，teacher 集一致率从 0.488 掉到 0.221。
- 折中格式（带分数）没有变好，反而更慢 —— 只要模型要逐个吐标签名和数字，token 数就上去了。

### 7.2 并发无效

4 个并发请求跑 29 条：workers=1 → 67 ms/条，workers=2 → 65 ms/条，workers=4 → 65 ms/条。**GPU decode 已饱和，别指望并行提速**。

### 7.3 上下文大小基本不影响（但会触发模型重载）

num_ctx 2048 vs 8192：prompt eval 都 ~93 ms、8 条输出 22 tok；差异只在切换 ctx 会触发一次模型重载（~1.5 s）。固定 8192 即可。

### 7.4 更小的模型实测更差

qwen3:0.6b（已拉，0.6B）单标签模式：**416 ms/条**、还输出了不存在的标签名 `informative` → 不可用（它是 thinking 模型，输出推理过程）。
要试更小的，得选非 thinking 的小模型（qwen2.5:0.5b / 1.5b 之类）；Ollama 的 `think:false` 对 qwen3 系列不一定生效。

### 7.5 结论

- 规则是 0.011 ms/条，LLM 最快 66 ms/条，**物理上不可能做到规则级**（差 6000 倍）。
- 但「感知上接近规则级」可以做到：**所有用户可见路径都命中缓存/预取（0 ms），LLM 只在后台填缓存**。
- 因此运行时用「单标签 fast 模式 + 语义近邻缓存 + 预取」；离线预计算保留「向量模式」（质量更高，一次标好长期用）。
## 8. 传统 NLP 对照实验（回应「眼光打开」，2026-11-05）

同一批数据、同一批测试集，比较三类决策器：

| 决策器 | teacher 131 条（留出训练） | human gold 29 条（留出） | 延迟 |
|---|---|---|---|
| 手写 rules | 0.191 / macroF1 0.106 | 0.345 / 0.180 | 0.011 ms |
| qwen3.5:4b 向量 prompt | 0.488 / 0.219 | 0.586 / 0.235 | 821 ms |
| qwen3.5:4b 单标签 fast | 0.221 / — | 0.483 / — | 66 ms |
| **TF-IDF(char 1-3) + LinearSVC** | **0.611 / 0.399** | **0.690 / 0.376** | **~1.4 us（微秒）** |
| TF-IDF + LogisticRegression | 0.626 / 0.410 | — | 1.4 us |
| kNN（TF-IDF 余弦检索，无训练） | 0.191（跨视频） | 0.788（同视频 5 折） | 20–90 us |

注：teacher 集训练时已把 131 条从训练集剔除；human gold 训练时剔除 29 条，均为真留出。上限（deepseek-flash vs 人工）= 0.897。

### 8.1 结论一：同视频/同领域内，传统 NLP 打败 4B LLM，而且快 5 个数量级

1,407 条标注训练 **10 毫秒**（LinearSVC），预测 **1.4 微秒/条**，teacher 留出一致率 0.611 > LLM 0.488。
同视频 5 折 CV：同意率 0.807、macro-F1 0.683。

### 8.2 结论二：跨视频就崩

- A（音乐/梗视频）训练 → B（时政视频）测试：0.186–0.322；
- B 训练 → 人工 29 条（音乐视频）：0.241–0.276。

原因：弹幕词汇高度视频相关（梗、角色名、剧情、up 主），且标签分布差异巨大（A humor 64%，B information 44%）。
**传统 NLP 的代价不是算力，是「每个领域都要标签」。**

### 8.3 结论三：只要很少的同视频标签就能启动

用 LLM 标签做 bootstrap（LinearSVC，char 1-3gram）：

| 同视频标签数 | A 音乐 | B 时政 |
|---|---|---|
| 25 | 0.734 | 0.443 |
| 50 | 0.752 | 0.528 |
| 100 | 0.750 | 0.522 |
| 200 | 0.766 | 0.618 |
| 400 | 0.784 | 0.597 |
| 5 折上限 | 0.807 | 0.586 |

→ 一个新视频只需 LLM 标 100–200 条（fast 模式 6.6–13 秒），训练 10 ms，其余全部微秒级。

### 8.4 由此确定的三级级联架构

- L0 精确/近重复缓存：TF-IDF 余弦 ≥0.9 直接命中（同视频 5 折 0.788，纯检索无训练，20–90 us）。
- L1 每视频轻量线性模型：用 LLM 标的前 100–200 条训练（LinearSVC / 纯 numpy softmax，训练 10 ms），之后 microsecond 级打分，反馈可增量重训。
- L2 LLM zero-shot：冷启动、新标签、L1 低置信、以及 L0/L1 都没见过的文本；fast 模式 66 ms，向量模式 800 ms。
- 可选 L3 bge-m3 近邻：跨视频相似弹幕检索，7.6 ms。

### 8.5 副产品：线性模型的权重就是「自动学出来的规则」

每个标签 top char n-gram（训练于 A）：

- spoiler: 同框 三代 三代同框
- irrelevant_repetition: 2333 嘤 ～～ ... 开头
- emotional_resonance: 好听 帅 真 笑
- information: 原 词 里 00
- humor: 《 》 第 季 哈哈哈

这正好解决「手写规则太窄」：不再人工列 cue，而是让带权线性模型自己学 cue 权重，且可打印成词表供人审阅。
模型权重可剪枝后导出成 JSON（每类 top 2–3k 特征），**油猴脚本可以直接在浏览器里打分**，连本地服务都不用。

### 8.6 实验环境与复跑

- sklearn 1.9.1（系统 python3 用 --break-system-packages 安装）；实验脚本 /tmp/nlp_clf1.py ~ /tmp/nlp_clf4.py。
- 生产实现不引入 sklearn：TF-IDF 哈希 + 稀疏点积纯 Python/numpy 即可，或直接导出权重给 JS。

### 8.7 局限（必须写进论文/答辩）

- 线性模型继承了 teacher（deepseek-flash）的偏差；上限就是 teacher 与人工的一致性 0.897。
- 跨视频不迁移，必须每视频有标签；标签来源仍是 LLM。
- 只能学已出现过的标签；全新自定义标签仍需 LLM few-shot 或用户给 10–20 例后重训（10 ms）。

## 9. 三级级联已落地（2026-11-05）

§8 的方案已经实现并跑通真实链路，不再是原型。

### 9.1 代码

- `danmaku_filter/classify/linear.py`：纯标准库 `HashingTfidf`（char 1–3gram，crc32 分桶，dim 262144，tf=1+ln(桶计数)，idf=ln((1+N)/(1+df))+1，L2 归一化）+ `LinearTagger`（kind=centroid / softmax），模型 JSON 剪枝到 |w|>0.005，save/load 原子写。
- `danmaku_filter/server.py`：`_ModelStore`（按 bvid 读 `<bvid>.json`，回退 `_global.json`，mtime 热加载）、`_linear_online_item()`、`--online-backend linear`、`--models-dir`；低置信（< 0.45）标签自动进 LLM worker 队列，下一次 DOM tick（300 ms）或下次请求拿到 teacher 向量。
- `danmaku_filter/cli.py`：`serve --online-backend {rules,llm,auto,linear} --models-dir`。
- `scripts/train_models.py`：从 `data/tags/*.json` 的 LLM 标签训练每视频模型 + `_global.json`（centroid/softmax 自动选 dev 准确率高的）。
- `scripts/bootstrap_video.py`：按长度分层抽 N 条弹幕 → LLM teacher 打标 → 训练并写 `data/models/<bvid>.json`（新视频冷启动一条命令）。
- `extension/lib/linear.js`（零依赖 ESM 打分器，与 Python 逐位一致，误差 ≤3.3e-16）+ `extension/tools/score-cli.mjs`：需要时可把模型下发给浏览器本地打分。

### 9.2 实测（真实 B 站数据）

| 场景 | 结果 |
| --- | --- |
| 同一视频 400 条，HTTP 端到端 | **0.274 ms/条**，与该视频 LLM teacher 一致率 **0.92** |
| 冷启动视频（71 条 teacher 标签，模型 80.8 KB） | 首次 105 ms（含模型加载），随后 6.4 ms/6 条；低置信标签 6 s 内被 teacher 升级（information 0.27 → attack 1.00） |
| 留一视频验证（用其他视频训练，跨域预测该视频） | centroid 0.319、softmax 0.440 —— **跨视频不迁移，必须每视频 bootstrap** |
| 手工留出（1,235 训练） | centroid teacher 0.664 / gold 0.586；softmax teacher 0.580 / gold 0.690 |
| 训练/推理成本 | 1,400 条训练 < 0.1 s（centroid）/ 4.8 s（softmax）；推理 22–28 µs/条（纯 Python），模型 0.59 MB / 2.69 MB |

### 9.3 结论

1. 用户要的「接近规则级的速度」达到了：用户可见路径 0.274 ms/条，是 LLM 向量的约 3000 倍加速；
2. 质量不是靠线性模型单打独斗：同视频 0.92 一致率的前提是有 teacher 标签 bootstrap，冷启动/低置信由 L2 LLM 兜底；
3. 成本从「每个视频每条弹幕一次 LLM」降到「每个视频 80–150 条 LLM + 其余微秒级」，按 deepseek-flash 计费约 1/10；
4. 仍需论文里交代的局限：teacher 偏差可继承、跨视频必须重新 bootstrap、全新自定义标签要重新采样训练（训练本身 0.1 s，瓶颈是采样）。

### 9.4 测试

`python3 -m unittest discover -s tests` → **178 tests OK**（含 `tests/test_linear.py` 26 个、`tests/test_online_linear.py` 6 个）；`node --test extension/test/` → **57 pass**。
