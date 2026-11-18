# bili-tagging · ZonE 弹幕过滤器

**Filtering Danmuku: How Comment Type and Volume Shape Viewer Experience**（ZonE 小组项目）

Bilibili 观众侧弹幕过滤器 MVP：离线把弹幕按「社会功能」分成 7 类并缓存，运行时用
Tampermonkey 脚本在播放器的 `seg.so` 网络层改写弹幕流，按类别 show / hide / collapse。
右下角角标显示过滤计数；点「控制台」打开面板（标签策略 / 折叠记录 / 反馈日志三个
tab），可逐条「恢复显示」「改标签」、按标签切显示/折叠/隐藏、调阈值与密度参数；
分类与偏好都在本地，任何异常一律放行（fail-open），不阻断播放。

## MVP 现在能做什么

| 能力 | 入口 | 状态 |
| --- | --- | --- |
| 拉全量弹幕（seg.so 全分段，非采样） | `danmaku_filter/fetch.py`、`dmproto.py` | ✅ |
| 7 类社会功能打标 + 多标签分数（规则基线 / OpenAI 兼容 LLM） | `classify/`、`pipeline.py` | ✅（云端需 API key；本地 Ollama 免 key） |
| 离线预计算 → `data/tags/<bvid>.json`（schema v2，原子写） | `python3 -m danmaku_filter.cli precompute` | ✅ |
| 本地决策服务（decisions / miss / feedback / tags，带 overrides） | `python3 -m danmaku_filter.server` | ✅ |
| userscript：XHR+fetch hook（含 wbi seg.so）、hide/collapse、角标、控制台面板、偏好、密度 | `extension/dist/bili-danmaku-filter.user.js` | ✅（ego-browser 真实页跑通；真机清单待用户） |
| 解释 / 纠正 / 双通道反馈（策略层 + 分类层） | 面板按钮 → `/api/v1/feedback` | ✅ |
| 离线评测（P/R/F1、未匹配诊断） | `python3 -m danmaku_filter.cli eval` | ✅ |

自动化验收：Python 119 个单测、JS 40 个单测、离线端到端 smoke、真实 seg.so 抓取与预计算全部通过
（见 [docs/design/contracts.md](docs/design/contracts.md) §11）。浏览器侧用 ego-browser
在真实 B 站视频页端到端跑通（见下节）；真机行为见
[extension/README.md](extension/README.md) 的 15 条手动测试清单。

## 30 秒演示（已带示例缓存）

仓库自带 `data/tags/BV1GJ411x7h7.json`（deepseek-flash 打标，1407 条；humour 64%、emotional resonance 17%、information 14%），可直接演示：

```bash
# 1. 启动本地决策服务（默认 127.0.0.1:8765）
python3 -m danmaku_filter.server

# 2. 另开终端：确认服务与缓存正常（不需要浏览器）
python3 scripts/smoke_test.py

# 3. 浏览器安装 Tampermonkey → 安装 extension/dist/bili-danmaku-filter.user.js
# 4. 打开 https://www.bilibili.com/video/BV1GJ411x7h7 播放
#    右下角出现「弹幕过滤 已隐藏 N · 折叠 M」，攻击/争论/剧透不再出现，
#    无意义重复变成【已折叠·无意义重复】，点角标打开面板可撤销或改标签。
```

想换视频：`python3 -m danmaku_filter.cli precompute --bvid <BV号> --backend rules`，再刷新对应视频页。

## 任意视频都能用：无离线缓存时规则实时打标

离线缓存（`data/tags/<bvid>.json`）目前只有演示视频 BV1GJ411x7h7 是 LLM 全量打好的。
为了让**任何视频**都有可见效果，服务端在收到未缓存的 dmid 时会用高精度规则基线
（攻击/争论/剧透/无意义重复关键词）**当场打标**，把结果写回 `data/tags/` 并返回给
脚本；命中规则的弹幕立刻按策略隐藏/折叠，其余仍按默认放行。

自检两个用法：

1. 面板标题下方显示 `v0.2.0`（看到 `vdev` 说明浏览器里还是旧脚本，需要重新导入
   `dist/bili-danmaku-filter.user.js`）。状态点应为绿色「本地服务已连接」。
2. **想看管线是否真的生效**：把「置信度阈值」拉到最低并把所有标签都设成「隐藏」，视频里
   的弹幕应当整屏消失；如果这时仍然一条不少，说明拦截或决策链路没通，打开面板底部
   「高级 · 本地服务」看诊断计数（拦截 / 决策成功 / 失败 / 缓存命中 / 最近错误）。

### 两层过滤：segment 拦截 + DOM 兜底

播放器一次只请求当前段（约 2 分钟）的弹幕，换策略时已经下载进播放器的弹幕不会重来，
所以只有 segment 层时「改策略要等下一段」才生效。脚本因此有两层：

1. **segment 层**（/x/v2/dm/{web,wbi/web}/seg.so）：在 protobuf 进播放器前改写，
   隐藏的弹幕根本不占带宽、不上屏，是省流量与「hide 真隐藏」的来源。
2. **DOM 兜底层**（extension/src/main.js 的 liveTick）：每 300 ms 扫一遍已经渲染的
   弹幕节点（.bili-danmaku-x-dm），按当前策略即时处理——该隐藏的直接移除，该折叠的
   把文本换成「已折叠·幽默」这类占位（opacity:.4），改回「显示」就还原原文。
   未见过文本会带 ephemeral:true / labels:true 请求本地服务拿标签，不写缓存
   （避免把 DOM 里的伪 dmid 污染 data/tags/）。

所以现在**切换策略 1 秒内就能看到变化**，面板底部「高级 · 本地服务」里会多一行
DOM 兜底 隐藏 N · 折叠 M。真实页验证（未缓存视频 BV1w5xh6sEEz）：
默认策略 11 个已渲染节点中 8 个被折叠；全改「折叠」后节点文本变成「已折叠·××」；
再全改「显示」后全部还原；全改「隐藏」后渲染层 0 条。截图在
docs/screenshots：live-1-default.png、live-2-allcollapse.png、live-3-allshow.png、
live-4-allhide.png。

## v0.4 全量弹幕池：进视频先整理整池，再开播

逐段过滤只能做到「改策略要等下一段」，且密度抽稀对整段不公平。v0.4 改成**整池预取**：

1. 进入视频页并行拉 `/x/v1/dm/list.so?oid=<cid>`（整池 1,200–3,600 条，带真实绝对时间，实测加载 232–720 ms）；
2. 用该视频的线性模型在浏览器本地打分（`extension/lib/linear.js`，无模型时按 300 条一批调本地服务）；
3. 一次算出整池 dmid→动作表（含密度抽稀：每 10 s 窗口最多 30 条，information/other 豁免）；
4. 播放器请求 `seg.so` 时按表改写 protobuf：被隐藏/折叠的弹幕**整条不进播放器**（折叠=不渲染）；
5. 被折叠/隐藏的条目统一进面板「折叠记录」（以整池为真相源），可逐条「恢复显示 / 改标签」；
6. 改策略立即重算整池并修正当前 DOM，角标与面板统计显示整池数字。

等待策略：最多等 2.5 s，屏幕顶部胶囊显示「正在整理弹幕池 x/y 段 · N 条 · 打分中」；超时先边播边补。

实测（BV1Js411o76u，151 万弹幕 / 2,651 万播放）：整池 3,600 条、动作表 0.66 ms；
默认策略显示 1,456 / 折叠 2,144；全折叠后播放器 0 条弹幕、面板 2,139 张折叠卡；
密度 30 条/10 s 时折叠 2,139 条。截图：docs/screenshots/pool-v4-folded.png、
pool-v4-strict-hide.png、pool-v4-density-folded.png。

「全局参数」新增**低置信放行**开关（`prefs.fail_open`，默认开）：关闭后低于阈值的条目也按标签策略处理，
「全部隐藏 + 关闭低置信放行」会让整池 3,600 条全部隐藏、渲染层 0 条。

注意：未签名的 `seg.so` 只返回第 1 段（seg2 起 304），所以整池主源是 legacy `list.so`；
`list.so` 单次上限约 1,200–3,600 条，超大池（>4 万）只取前 4 万条。

## 三级级联：LLM teacher 离线训练 + 微秒级本地线性模型

规则太窄（人工金标一致率 0.345），4B 端侧 LLM 够准但一条要 750–820 ms。现在的默认路线是
**让 LLM 当 teacher 离线打一小批标签，训练一个每视频的线性模型，运行时分秒级出结果**：

| 层 | 做什么 | 成本 |
| --- | --- | --- |
| L0 缓存 | `data/tags/<bvid>.json` 命中直接用 | 0 ms |
| L1 线性模型 | hashed char 1–3gram TF-IDF + centroid/softmax，每视频一个模型 | **0.27 ms/条**（HTTP 往返 + 策略，1,400 条约 0.4 s） |
| L2 LLM teacher | 只处理 L1 低置信（< 0.45）的弹幕、冷启动视频、新标签；prompt 默认 v4（判定顺序 + 软锚点，见 docs/experiments/e8-boundary.md） | 后台异步，用户不等待 |

用法三步（模型训练 < 0.1 s，一个视频只需 80–150 条 teacher 标签）：

```bash
# 1) 给某个新视频 bootstrap 一个线性模型（先拉弹幕，再用 LLM 标 80 条）
python3 scripts/bootstrap_video.py --bvid BV1PF4g6ME26 --sample 80

# 2) 用已有缓存全量重训（按 bvid 一个模型 + 一个跨视频 _global.json）
python3 scripts/train_models.py

# 3) 服务端切到线性后端（启动时读 data/models/，模型热加载）
python3 -m danmaku_filter.cli serve --online-backend linear
```

实测（真实 B 站数据、`/api/v1/decisions` 端到端）：

- 1,407 条同一视频、400 条批量请求：**0.274 ms/条**，与该视频 LLM 标签一致率 0.92；
- 冷启动视频（71 条 teacher 标签训练、模型 80.8 KB）：首次请求 105 ms（含模型加载），
  之后 6 条 6.4 ms；低置信的「up主就是个傻逼」首轮被线性模型判成 information 0.27，
  后台 teacher 6 秒内升级为 **attack 1.00**；
- 跨视频直接迁移会崩（留一视频验证 0.32–0.44），所以必须每视频 bootstrap —— 这正是 L2 存在的原因。

纯标准库实现（`danmaku_filter/classify/linear.py`），不需要 sklearn / numpy / GPU；模型是 JSON，
`extension/lib/linear.js` 是 JS 版打分器，与 Python 逐位一致（误差 3e-16），必要时可把权重直接
下发给油猴脚本离线打分。
## 浏览器内验证（ego-browser，无需 Tampermonkey）

本机没有浏览器自动化环境里的 Tampermonkey，验证时用 ego-browser 打开真实 B 站视频页，
先用 CDP `Page.addScriptToEvaluateOnNewDocument` 注入一个 `GM_xmlhttpRequest`
shim（内部用 `fetch`，只对 bilibili 域带 credentials，避免 `127.0.0.1:8765`
的 `ACAO:*` 冲突），再注入 `extension/dist/bili-danmaku-filter.user.js`。
验证脚本在 `/tmp/ego-bili*.mjs`（一次性，不入库），结论：

- 真实播放器请求的是 **`/x/v2/dm/wbi/web/seg.so`**（2023 年后的签名路径），脚本的
  `SEG_PATHS` 同时匹配旧/新路径后拦截生效：`connected=true`、同一段解析
  ~5.9k 条、`failures=0`。
- 默认策略下该视频折叠 52 条无意义重复/剧透，徽标显示「已隐藏 0 · 折叠 52」；把 spoiler
  改成「显示」后折叠数立即下降，改标签（无意义重复 → 共鸣）后条目状态变「已显示」。
- 截图（已入库）：[docs/design/screenshots/](docs/design/screenshots/)
  `bilibili-panel-folded-52.png`（折叠记录 + 角标）、`bilibili-panel-strategy.png`
  （标签策略 10 行 + 全局参数）、`bilibili-relabel-popover.png`（改标签 popover）、
  `bilibili-panel-log.png`（反馈日志 + toast）。
- 极端密度压测：BV1Js411o76u（151 万弹幕，单段 5,886 条）在 ego lite 里端到端跑通，
  密度关/开两档对比见 `docs/screenshots/e9-density-off.png` 与 `e9-density-on.png`；
  数字、7 个修复项与遗留问题见 [docs/experiments/e9-stress.md](docs/experiments/e9-stress.md)。

## 快速开始（Python 侧）

需要 Python ≥ 3.10；只做 userscript 开发/测试时需要 Node ≥ 22。全部零第三方依赖。

```bash
# 0. 单测（119 个）
python3 -m unittest discover -s tests -v

# 1. 抓弹幕（seg.so 全分段；--source list 可退回旧的 list.so 采样接口）
python3 -m danmaku_filter.cli fetch --bvid BV1GJ411x7h7 --out data/raw/BV1GJ411x7h7.jsonl

# 2. 预计算标签缓存（--limit 0 = 全量；LLM 后端见下方「本地模型」或云端 key）
python3 -m danmaku_filter.cli precompute --bvid BV1GJ411x7h7 --backend rules

# 3. 起服务（userscript 的数据源）
python3 -m danmaku_filter.cli serve --port 8765 --tags-dir data/tags

# 4. 离线评测：gold 每行 {"text": "...", "label": "<7 类之一>"}
python3 -m danmaku_filter.cli classify --input examples/danmaku_example.jsonl \
  --out /tmp/pred.jsonl --backend rules
python3 -m danmaku_filter.cli eval --gold examples/gold_example.jsonl --pred /tmp/pred.jsonl

# 5. userscript 开发：改 extension/src 或 extension/lib 后
node extension/build.mjs
node --check extension/dist/bili-danmaku-filter.user.js
node --test extension/test/
```

LLM 只在离线预计算时调用，运行时零 LLM 请求。任何 OpenAI 兼容端点都能接，环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DM_LLM_BASE_URL` | `https://api.deepseek.com/v1` | 端点根路径（会拼 `/chat/completions`） |
| `DM_LLM_MODEL` | `deepseek-flash` | 模型名，如 `lfm2:latest`；DeepSeek 另有 `deepseek-v4-pro` |
| `DM_LLM_API_KEY` | 空 | 云端必填；本地/内网端点可省（自动用占位 key） |
| `DM_LLM_BATCH` | `30` | 每次请求多少条弹幕（本地小模型建议 8–15） |
| `DM_LLM_TIMEOUT` | 云端 `120` / 本地 `600` 秒 | 单请求超时 |
| `DM_LLM_TEMP` | 云端 `0` / 本地 `0.3` | 采样温度；本地 MoE 模型贪心解码偶发空输出，重试会自动升温 |
| `DM_LLM_RETRIES` | `2` | 网络/5xx/空响应时的额外重试次数 |

### 本地模型（Ollama，免 API key）

作者在 Windows 上跑 Ollama、在 WSL2 里跑本项目。Ollama 默认只监听 Windows 本机
`127.0.0.1:11434`，WSL 访问不到，所以另起一个专用实例（不动桌面 app）：

```powershell
# Windows PowerShell：模型目录按实际改（本机是 F:\Models），监听所有网卡 11435
$env:OLLAMA_HOST="0.0.0.0:11435"; $env:OLLAMA_MODELS="F:\Models"
Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" -ArgumentList "serve"

# 管理员 PowerShell 一次性放行 WSL 网段
netsh advfirewall firewall add rule name="Ollama WSL 11435" dir=in action=allow protocol=TCP localport=11435 remoteip=10.211.0.0/16
```

注意：两个环境变量缺一不可 —— 只设 `OLLAMA_HOST` 会看不到模型列表。

然后在 WSL 里（`<Windows-IP>` 用 `ip route` 之外的 Windows 虚拟网卡地址，常见是
`10.x.x.x`；从 WSL 里 `curl http://<Windows-IP>:11435/api/tags` 验证）：

```bash
export DM_LLM_BASE_URL=http://<Windows-IP>:11435/v1
export DM_LLM_MODEL=lfm2:latest      # 或 qwen3.8:27b
export DM_LLM_BATCH=8
python3 -m danmaku_filter.cli precompute --bvid BV1GJ411x7h7 --backend llm
python3 scripts/bench_labeler.py --gold examples/gold_example.jsonl --backends rules,llm

# 不重拉网络，只把已有缓存重标（换后端 / 调提示词后用；--limit 先小样本试，--dry-run 只看分布）
python3 -m danmaku_filter.cli reclassify --bvid BV1GJ411x7h7 --backend llm --limit 60 --dry-run
```

本机实测（RTX 5060 8GB 笔记本 + LFM2-23.8B-A2B Q4_K_M，张量部分 offload 到 CPU）：
批量 8 条约 0.8–1.5 s/条；贪心解码偶发空响应，已用「非零温度 + 重试升温」兜底。
没装 Ollama 也可以先用 `--backend rules`，完全离线。

## 研究产出（课程作业）

本项目同时是 ZonE 小组的研究与写作仓库，课程作业产物都在 `submission/` 与 `docs/` 下：

| 产出 | 位置 | 说明 |
| --- | --- | --- |
| Revised introduction + pilot study（10/29 作业） | `submission/ZonE + Introduction + pilot study.docx` | 标题与作者、specialized scope、前人局限、目标 + RQ/H1–H4、四阶段研究设计（Figure 1）、先导试验（Table 2/3）、6 篇 APA 参考文献；TNR 11、双倍行距、两端对齐 |
| 先导试验说明 | `docs/pilot/README.md` | 三个可行性问题、语料、结果（0.53 s/条、29 条审核 89.7%、MAE 0.05）、局限 |
| 先导试验数据 | `data/pilot/audit_sample.json`、`data/pilot/audit_gold.json` | 29 条分层样本与人工标签，`python3 scripts/audit_score.py` 可复核 |
| Introduction Part 1–5 | `docs/intro/` | 500 词版本与 Part 5 的技术方案（早期作业） |
| 设计图/文档生成脚本 | `scripts/make_figure.py`、`scripts/build_pilot_doc.py` | `PYTHONPATH=scripts python3 scripts/build_pilot_doc.py` 重新生成 docx |
| 实验报告（E0–E7 全量消融） | docs/experiments/REPORT.md | 6 视频 / 2,457 条真实弹幕；决策器横评、上下文与环境向量负结果、bootstrap 曲线、置信升级、延迟、先验漂移、错误分析 |
| 标签边界实验（E8） | docs/experiments/e8-boundary.md | prompt v2→v3→v4 对比：判定顺序 + 46 条最小对立对；deepseek-flash audit 0.759→0.897、边界 must-hit 0.913→0.957、information 召回 0.774→0.839；本地 4B 持平，边界收益靠云端 teacher |
| 压测报告（E9） | `docs/experiments/e9-stress.md` | 单段 5,886 条 / 全站 151 万弹幕视频：服务端 0.007 ms/条（修复前 0.39 ms/条）、客户端单段 30–55 ms、帧率 p95 14 ms；密度旋钮把 16,000 条压到 30 条常驻；暴露并修好 6 个问题（缓存重解析、队列上限、密度覆盖显式折叠、暂停无提示、面板统计口径、BrokenPipe） |
| 正式实验（S2）材料与工具 | docs/study/ | 6 支真实刺激视频（信息 3 + 娱乐 3、密度高/低配对）；teacher 抽样标注 988 行、每视频线性模型；研究模式 A/B/C（A/B 只记录、C 整池过滤）+ 低密度格抽稀（30 条/分）；session_url/run-sheet、知情同意、主试逐字稿、问卷、analyze.py；分析管线已用真实导出 + 合成问卷干跑（data/study/dryrun）；统计口径已与 scipy/参考实现逐值对齐（Wilcoxon p、Holm、Cronbach、Krippendorff α 有限样本修正）并做功效模拟（scripts/study/power_sim.py：N=24 对 d≥0.7 有 80% power） |
| S1 金标 + 主试工具 | docs/study/gold-annotation.md、docs/study/examiner-checklist.md、docs/study/results-template.md、scripts/study/precheck.py | 120 条单标注金标（6 视频各 20 条，与 teacher 一致率 0.575）；analyze --gold 出 per-tag P/R/F1：dominant 0.558、macro-F1 0.496、other recall 0.147（模型几乎从不判 other）；主试清单（含 B 条件平台屏蔽词步骤与降级方案）/结果模板/一键自检 7 项（服务、材料、模型、报告、链接表、金标、脚本版本）；招募文案与排期模板 docs/study/recruitment.md |


## 目录结构

```text
bili-tagging/
├── danmaku_filter/            # Python 侧：抓取 → 打标 → 缓存 → 决策服务
│   ├── types.py               # Danmaku / ClassificationResult(tags)
│   ├── taxonomy.py            # 9 类评分标签 + other（唯一来源）
│   ├── dmproto.py             # seg.so protobuf 编解码 / 删除 / 折叠（保留未知字段）
│   ├── fetch.py               # view API + seg.so 全分段拉取
│   ├── policy.py              # prefs + resolve_item + decide
│   ├── cache.py               # data/tags|overrides|pending 原子读写
│   ├── pipeline.py            # precompute：拉取 → 分类 → schema v2 缓存；reclassify：只重标已有缓存
│   ├── server.py              # 本地 HTTP API（stdlib ThreadingHTTPServer）
│   ├── feedback.py            # 旧版纠正存储（CLI classify --feedback 用）
│   ├── metrics.py / cli.py    # P/R/F1；fetch/classify/eval/precompute/reclassify/serve
│   └── classify/{base,rules,llm}.py
├── extension/                 # Tampermonkey userscript（零依赖，node:test）
│   ├── lib/{dmproto,policy}.js  # 与 Python 同语义的 JS 编解码/决策
│   ├── src/main.js            # hook、UI、偏好、反馈
│   ├── build.mjs              # 拼接为单文件 userscript
│   └── dist/bili-danmaku-filter.user.js   # 安装这个文件
├── scripts/
│   ├── build_submission.py    # 生成 Introduction Word 提交稿
│   └── smoke_test.py          # 离线端到端验收（无网络、无浏览器）
├── tests/                     # Python 单测 + 真实 seg.so fixture
├── docs/
│   ├── project-brief.md       # 一页纸：动机、gap、路线、指标、参考文献
│   ├── taxonomy.md            # 标签定义与标注指南（v0.3：+political/inflammatory）
│   ├── BACKLOG.md             # 活文档：TODO / 已做 / 新需求（MVP 结束时清空）
│   ├── intro/                 # Introduction 五段合并稿 + Part 5 梳理
│   └── design/
│       ├── plan-v1.md         # 插件开发计划
│       ├── plan-v2.md         # 小模型蒸馏 + 校准概率 + 预取（回应 11/4 意见）
│       ├── contracts.md       # 冻结契约 + 实现偏差 + 验收快照
│       ├── prd-v0.1.md        # 早期 PRD（用户提供）
│       └── reconciliation.md  # PRD × slides × 实现的对齐决策
├── submission/                # 作业 Word（Group X + Summary writing）
├── config/taxonomy.json       # 由 taxonomy.py 导出，勿手改
└── data/                      # tags/ overrides/ pending/ feedback/（raw、private 不入库）
```

## 已知限制（MVP 范围）

- 只支持 B 站 PC 视频页（`/video/*`）；直播、手机端、番剧播放器未处理；只拦 `seg.so`。
- rules 后端高精度低召回：演示缓存里大多数弹幕是 `other`，过滤效果偏保守；想要覆盖率需配 LLM 后端重跑 precompute。
- 本地 LLM 后端目前是「能跑通、质量待评」：LFM2 在 9 条构造样例上 acc 0.78，标签边界（纯笑/情绪、攻击/争论、信息/主观）仍需提示词或 few-shot 调优，等人工标注 pilot 后再定。
- 之前的验证只在**已缓存**的演示视频上做过（ego-browser + GM shim），没覆盖「打开任意视频」
  这个最常见场景——那时未缓存的弹幕全部返回 miss，页面看起来完全没被过滤。现已加在线规则
  兜底与面板诊断（见上节），但真机 Tampermonkey 仍需按清单复核。
- 浏览器内 hook/面板/fail-open 已用 ego-browser + GM shim 在真实 B 站页跑通（见上节），但真机
  Tampermonkey 环境（界面、权限、CSP、不同播放器版本）仍需按 [extension/README.md](extension/README.md)
  的 15 条清单验收。
- 密度控制按契约只改写「服务端非 show 决策」的条目（show 类不受密度影响），所以它只降级
  hide→collapse，不真正削减可见弹幕总量——这与 RQ1 的「volume」变量有缺口，是否扩展需单独拍板。
- 未做：自定义标签、共享缓存、Sarcasm/Irrelevance 拆类、实时逐条 LLM 补标（miss 进 `data/pending/` 等离线补）。

## 路线图

- [x] 选题、Introduction Part 1–4、技术路线与仓库骨架
- [x] 500 词 Introduction + Part 5，生成 Word 提交稿（`submission/`）
- [x] M0 seg.so 数据源 + protobuf 编解码 + 真实 fixture
- [x] M1 多标签 item + policy
- [x] M2 本地服务 + 缓存 + precompute
- [x] M3 userscript：hook / hide / collapse / 角标 / 面板
- [x] M4 纠正 + 双通道反馈
- [x] M5 密度控制 + 偏好 UI
- [x] M6 离线评测 → 演示说明（本 README）
- [x] 真实 B 站页端到端跑通（ego-browser + GM shim；含 wbi seg.so、面板三 tab、改标签/恢复显示）
- [ ] 真机浏览器验收 15 条手动清单（需用户）
- [x] 本地 LFM2/Ollama 接入（本地免 key、超时/重试/升温、`scripts/bench_labeler.py`）
- [ ] 弹幕采样 + 7 类人工标注 pilot（Cohen's κ ≥ 0.7）
- [ ] LLM few-shot 对比规则基线；用标注集报 P/R/F1 与 p50/p95 延迟
- [ ] 用户研究：设置时间、纠正次数、NASA-TLX、discomfort / control / FOMO
- [ ] 信息类视频理解/记忆任务

## 约定

- 标签 id 以 `danmaku_filter/taxonomy.py` 为准（v0.3：9 个评分标签 + `other`）；`config/taxonomy.json` 由它导出。
- 运行时只在网络层改 `seg.so` 响应，不改 DOM、不自绘弹幕；任何异常 fail-open。
- 数据放 `data/`；`data/raw/` 与含个人信息的文件不入库；`data/tags/` 保留一份示例缓存用于演示。
- 论文/写作术语统一用 social function（v0.2 的 7 类在 v0.3 扩为 9 类 + other，见 `docs/taxonomy.md` §8）。
