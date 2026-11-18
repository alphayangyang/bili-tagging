# BACKLOG（活文档）

> 用户要求（m00190）：「做的同时维护一个文档记录 todo/已做/产生的新的需求，直至清空文档」。
> 规则：每完成一项 → 移到 DONE 并写证据（命令/结果）；发现新需求 → 先写 NEW REQ 再决定做不做；
> 本文件**只由 lead 编辑**，其他人通过 `send_message` 汇报。MVP 结束时本文件应无未决 TODO（做不了的要写明原因并移入「明确不做」）。
> 契约见 [contracts.md](contracts.md)，总计划见 [plan-v1.md](plan-v1.md)。

## 状态表（MVP 关闭：全部 DONE）

| ID | 任务 | 状态 | 负责人 | 验收 / 证据 |
|---|---|---|---|---|
| B0 | 接口契约 + 本活文档 | DONE | lead | contracts.md / 本文件 |
| M0 | `dmproto.py`：seg.so protobuf 解析/过滤/重编码 + `fetch.py` 改 seg.so 数据源 + 真实样本 fixture | DONE | lead | `tests/test_dmproto.py` 8 用例 + `tests/test_fetch.py` 7 用例（重试/304/遇空停）；全套 91 tests OK |
| M1 | `policy.py` + `types.ClassificationResult.tags`（多标签）+ 单测 | DONE | lead | `tests/test_policy.py` 13 条 decide 表格 + 4 条 normalize；全套 91 tests OK |
| M2 | 本地服务 `server.py` + 缓存 `cache.py` + 预计算 `pipeline.py` + CLI `precompute/serve` | DONE | server-dev | `tests/test_{server,cache,pipeline}.py` 47 用例绿；`precompute --bvid BV1GJ411x7h7` 生成 data/tags（1 段/1407 条）；smoke_test 覆盖全部端点 |
| M3 | userscript：seg.so hook、decisions 应用、hide/collapse、角标、面板、偏好、密度 | DONE | userscript-dev | `node --test extension/test/` 40 pass；dist 2288 行通过 `node --check`；集成验证 2 hide + 2 collapse + INTEGRATION_OK；浏览器内待用户真机清单 |
| M4 | 纠正 + 双通道反馈（服务端 endpoint / 本地覆盖 / 面板按钮） | DONE | server-dev + userscript-dev | smoke_test 验证 label_correction 翻转后 decisions 立即变 evidence=user correction；三类事件分文件落盘（label_corrections/policy_events/prefs_events.jsonl） |
| M5 | 密度控制（客户端窗口计数）+ 偏好 UI | DONE | userscript-dev | `test/density.test.js` 覆盖窗口/升级规则；面板含每类动作/置信度/密度开关/服务地址/清空 |
| M6 | 评测脚本/演示文档（标注 pilot 移入研究阶段） | DONE | lead | `eval` 用 gold/danmaku 示例跑通（n=9, acc=1.0, macro_f1=1.0，构造样本仅证链路）；README 30 秒演示 |
| D1 | README 安装/运行/演示说明 + 目录结构更新 | DONE | lead | README 全量重写（能力表/演示/quickstart/目录/限制/路线图） |
| L1 | 本地模型接入：`llm.py` 本地端点免 key、超时/重试/温度、空响应兜底 + `tests/test_llm.py` 7 用例 | DONE | lead | 全套 98 tests OK；LFM2 22 条抽样端到端跑通（1.5 s/条，batch 8） |
| L2 | `scripts/bench_labeler.py`：rules vs LLM 的 acc / macro-F1 / 延迟对比 | DONE | lead | 9 条构造 gold：llm acc 0.778、845 ms/条；rules acc 1.0（构造集偏向 rules，等真实标注） |
| L3 | `pipeline.reclassify` + CLI `reclassify`：不重拉网络、原地重标已有 schema v2 缓存（`--limit` 小样本、`--dry-run` 只看分布、原子写、记录 relabeled_from）+ 6 个单测 | DONE | lead | `tests/test_reclassify.py` 6 用例；全套 105 tests OK |
| L4 | LLM JSON 容错：`json.loads(strict=False)` 接受 rationale 内裸换行（LFM2 实测触发）+ 单测 | DONE | lead | `tests/test_llm.py::test_raw_newlines_inside_rationale_are_tolerated`；105 tests OK |
| L5 | 默认模型切到 `deepseek-flash`（DeepSeek V4.1 Flash；`deepseek-chat` 已下线）+ 文档同步 | DONE | lead | `/v1/models` 实测只有 `deepseek-flash` / `deepseek-v4-pro`；改 `.env.local`、`llm.py:43`、README:78、`plan-v1.md:79`；119 tests OK |
| L6 | 先导试验：29 条分层样本人工盲判 + `scripts/audit_score.py` 复核 | DONE | lead | agreement 26/29 = 0.897、MAE 0.052；`data/pilot/audit_{sample,gold}.json`、`docs/pilot/README.md` |
| L7 | 10/29 作业文档：revised introduction + objectives/RQ/H1–H4 + 四阶段设计 + Figure 1 + pilot study | DONE | lead | `submission/ZonE + Introduction + pilot study.docx`（2,430 词、6 篇 APA、3 表 1 图）；`scripts/build_pilot_doc.py`、`scripts/make_figure.py` |
| L8 | 全量 LLM 重标 `data/tags/BV1GJ411x7h7.json`（deepseek-flash、batch 100） | DONE | lead | 1,407 条 / 752 s = 0.53 s/条；分布 humour 902、emotional resonance 233、information 200、irrelevant repetition 65、spoiler 5、conflict 1、attack 0、other 1；rules 备份 `data/backup/BV1GJ411x7h7.rules.json` |
| L9 | taxonomy v0.3：新增 `political`（政治表达）与 `inflammatory`（引战）两个 provisional 标签，`other` 正式化为独立「不可判定」类（OTHER_DEF + 使用规则），prompt 同步改规则 | DONE | lead | `danmaku_filter/taxonomy.py`、`spec()` 版本 0.3、`config/taxonomy.json` 重导出；`tests/test_taxonomy.py` 4 用例；124 tests OK |
| L10 | CLI `classify` 批量化（不再逐条请求）+ 回归测试 | DONE | lead | `danmaku_filter/cli.py` 用 `classify_batch`；`tests/test_cli.py` 断言 single_calls=0 / batch_calls=1；124 tests OK |
| L11 | 方案 v2 设计文档：回应 11/4 的 5 点意见（蒸馏、评论外部效度、节奏/时政视频与新标签、other 正式化、校准概率），含架构/选型/评测矩阵/两周排期 | DONE | lead | `docs/design/plan-v2.md` |
| L12 | userscript UI 重写（角标 + 三 tab 控制台：标签策略/折叠记录/反馈日志、10 标签三态、全局参数、改标签 popover）+ 真实 B 站页端到端验证 + **wbi seg.so 路径兼容** | DONE | lead | `extension/src/main.js`（2729 行 dist）；40 js tests OK；ego-browser 真实页：connected=true、解析 ~5.9k 条/段、折叠 52、改标签→已显示、spoiler 切显示→折叠数下降；截图 `C:/Users/alphayang/ego/proto/bili-*.png` |
| L13 | 让脚本对「任意视频 + 随时切策略」都立刻生效：① rules 覆盖率重写（other 98.9%→~80%）② 密度语义改为窗口满即 exceed 且不保护 show ③ 默认值调回 max_visible=30 / exceed=hide / min_confidence=0.3 ④ 服务端 ephemeral + labels 协议 ⑤ **DOM 兜底层**（300 ms 扫描已渲染弹幕，即时 hide/折叠/还原） | DONE | lead | danmaku_filter/server.py、extension/src/main.js（liveTick）、extension/lib/policy.js；Python 131 tests OK、JS 41 pass；真实未缓存视频 BV1w5xh6sEEz：默认 11 节点折叠 8、全折叠后显示「已折叠·××」、全显示全还原、全隐藏渲染层 0 条；截图 docs/screenshots/live-*.png |
| L14 | 时政视频实跑：给 rules 基线的 political/inflammatory/conflict 扩词（地缘政治实体、机构、财政词汇），并用 deepseek-flash 对时政视频 BV1rCxe6wEN9 全量重标 472 条 | DONE | lead | danmaku_filter/classify/rules.py（POLITICAL_CUES +33 项、CONFLICT_CUES +6、INFLAMMATORY_CUES +3）、tests/test_rules.py +3 用例；BV1rCxe6wEN9 分布 information 206 / irrelevant_repetition 84 / humor 63 / **political 53** / emotional_resonance 37 / other 11 / attack 11 / conflict 6 / inflammatory 1；真实页截图 docs/screenshots/politic-*.png（政治 62 条折叠、证据显示 matched cue: 国家） |
| L17 | 三级级联落地：L1 线性模型（纯标准库 hashed TF-IDF + centroid/softmax）+ 每视频 bootstrap + 服务端 `--online-backend linear`（模型热加载、低置信自动交回 LLM teacher 升级）+ JS 打分器 | DONE | lead + server-dev + userscript-dev | `danmaku_filter/classify/linear.py`（26 测试）、`scripts/train_models.py`、`scripts/bootstrap_video.py`、`danmaku_filter/server.py`（`_ModelStore`/`_linear_online_item`/`LOW_CONFIDENCE_UPGRADE=0.45`）、`tests/test_online_linear.py` 6 用例；178 tests OK + 57 js pass |
| L15 | 消融 v1：规则基线 vs 端侧 LLM（qwen3.5:4b），回应 m02118「规则太窄」 | DONE | lead | `docs/design/ablation-v1.md`；`scripts/make_ablation_sets.py` + `scripts/ablation.py`；human 29 条 rules 0.345 / qwen3.5 0.517–0.586，teacher 131 条 rules 0.191 / qwen3.5 0.488（MAE 0.364、ρ 0.301），deepseek-flash 对照 0.897；同步实现 Ollama 原生 `/api/chat` + num_ctx + `DM_LLM_OLLAMA`/`DM_LLM_NUM_CTX`；`tests/test_online_llm.py` 9 用例、`tests/test_llm.py` native 通道用例；146 tests OK |
| L18 | 全量实验 + 标准实验报告：E0 标签质量、E1 决策器横评（6 类 × 4 标签集）、E2 上下文特征、E3 bge-m3 环境向量、E4 bootstrap 曲线、E5 置信升级、E6 延迟、E7 先验漂移、错误分析 | DONE | lead + server-dev + userscript-dev | scripts/experiments/（common/prepare_dataset/run_matrix/run_context/run_topicality/run_scaling/run_latency/run_adaptation/dump_errors）；data/experiments/dataset.jsonl（6 视频 2,457 条）+ results/*.json；**docs/experiments/REPORT.md**（v1.0）；关键数字：rules 同视频 0.168 / 线性同视频 200 条 0.662 / LOVO 线性 0.24–0.30 vs qwen3.5:4b 0.417 / 上下文拼特征 −5~−7pp / 环境向量 AUC 0.571 CI 含 0.5 / HTTP 0.401 ms/条 |
| L19 | 标签边界实验（②判定顺序 + ③最小对立对）：taxonomy.DECISION_ORDER / DECISION_ORDER_V4、boundary.py 23 对 46 条探针 + 回归测试、prompt v4（纯噪声才判 IR / attack 只给真辱骂 / 信息分不被玩笑取消 / 笑声标记算幽默 / 锚点只示范分数差）、默认 prompt 切 v4 | DONE | lead | docs/experiments/e8-boundary.md；deepseek-flash：audit 0.759→0.897、boundary 0.870→0.913、must-hit 0.913→0.957、information 召回 0.774→0.839；qwen3.5:4b 持平 0.517（本地小模型跟不上细顺序）；蒸馏传播不成立（同 400 行：v4 学生 audit 0.517 vs v2 0.552，学生准确率由标签量驱动 1,376 行 0.690）；189 tests OK |
| L20 | 压测（151 万弹幕视频 BV1Js411o76u，单段 5,886 条）+ 暴露问题修复：`_TagStore` 内存标签缓存（mtime 热加载、写盘后回填）、LLM teacher 队列上限 4,000 + `llm_dropped`、密度只对 show 竞争槽位、暂停时角标提示、decisions 带 `labels` 修正面板统计、BrokenPipe 静默 | DONE | lead | `docs/experiments/e9-stress.md`；服务端 5,886 条 41.1 ms（0.007 ms/条，修复前 2,288 ms / 56×）、并发 10,006 条/s 0 错误、冷启动写盘 0.046 ms/条、protobuf 解析 26 ms、浏览器帧 p95 14 ms；密度开 30 条/10 s 把 16,161 条压到 30 条常驻；193 python tests + 58 js pass；截图 `docs/screenshots/e9-density-{off,on}.png` |

| L21 | v0.4 整池预取：进视频先拉整池（list.so 主源 + seg 兜底）→ 本地线性模型打分 → 整池动作表 → 播放器按表改写（折叠=不渲染）→ 面板折叠记录以整池为真相源 → 偏好即时重算；新增 `GET /api/v1/model`、`prefs.fail_open`、顶部进度胶囊 | DONE | lead | `danmaku_filter/server.py`、`extension/lib/pool.js`、`extension/lib/poolweb.js`、`extension/lib/policy.js`、`extension/src/main.js`；实测 BV1Js411o76u 整池 3,600 条 / 加载 720 ms / 动作表 0.66 ms；strict hide 整池 0 条显示；99 js tests + 259 python tests；截图 `docs/screenshots/pool-v4-*.png` |
| L22 | 正式 S2 研究的材料与工具链：6 支真实刺激视频（信息/娱乐各 3、密度格高/低配对）、teacher 抽样标注 988 行、每视频模型重训、report.json ready、研究模式 A/B/C 冒烟、低密度格抽稀操纵、session 工具与 demo 分析链路 | DONE | lead | data/study/stimuli.json（P1 Ho4高/18E低、P2 VMR低/X7z高、P3 Z4高/CzW低）、data/study/teacher_sample.jsonl 988 行、data/study/report.json（6/6 ok、ready=true）、data/study/models/ 6 个新模型；scripts/study/{screen_candidates,label_stimuli,prepare_stimuli,session_url,demo_data}.py；extension/lib/study.js + lib/pool.js 的 thinSegmentDrop（low = 30 条/分、10 s 窗、稳定 hash，三条件都生效）、docs/study/{stimuli-selection,session-kit}.md、data/study/run-sheet.csv（180 URL 含 dmf_density）；263 python tests + 103 js pass |
| L23 | S2 收尾工具：S1 单标注金标 120 条（analyze --gold 出 per-tag P/R/F1）、主试清单 + 结果报告模板、一键自检脚本 precheck.py（服务/材料/模型/报告/链接表/金标/版本 7 项） | DONE | lead | data/study/gold_sheet.jsonl + gold.jsonl（120 行，6 视频各 20 条，与 teacher 一致率 0.575）；脚本 scripts/study/precheck.py + tests/test_study_precheck.py（16 用例）；文档 docs/study/{gold-annotation,examiner-checklist,results-template}.md；RQ2 金标结果 dominant 0.558 / macro-F1 0.496 / other recall 0.147 / attack recall 1.0（support 6）；docs/study/recruitment.md（招募/排期/提醒）、protocol §14 改为「6 件事当前状态」；281 python tests + 103 js pass |
| L24 | 分析口径 QA：Krippendorff α 有限样本修正（原实现系统性低估）、Wilcoxon/Holm/Cronbach 与 scipy/参考值逐值对齐、macro-F1 并集口径写明、功效模拟脚本 | DONE | lead | analyze.py 的 krippendorff_alpha_nominal 改为 (N²−Σn²)/(N(N−1))，与 krippendorff 0.9.0 在 4 组固定样例上逐值一致（0.620/0.240/0.298/0.294）；tests/test_study_analyze.py 新增 scipy 参考值与 Holm 参考例；scripts/study/power_sim.py + tests/test_study_power.py；protocol §2.4 补「N=24 对 d≥0.7 有 80%+ power、d=0.6 约 .78，Holm 后 .61/.74」、§8.2 补 α 与 macro-F1 口径、results-template §2.5 分析口径；README S2 行同步；286 python tests |

| L25 | S2 模拟运行（AI 被试）+ IMR 结果更新：6 个 LLM 子智能体按人格作答 36 块 → assemble → analyze.py 出表；拉丁方修正（每视频每条件 2 次、每被试看全 6 支）；familiar 编码/延迟匹配两个 bug 修掉；IMR 数字改去泄漏口径并补统计方法段与 §2.6 模拟小节 | DONE | lead | `data/study/agents/`（run-sheet-6.csv、blocks/、frames/、out/、README.md）、`scripts/study/agents/{build_materials,assemble}.py`、`data/study/{questionnaire.csv,raw/,analysis.json,analysis.md}`、`docs/study/results-simulated.md`、`docs/study/imr-methods-results.md` + `submission/Class X + ZonE + IMR.docx`（2,532 词 / 9 表 / 1 图）；去泄漏金标：agreement .525、macro-F1 .350（CI [.255,.430]）、3 表逐标签；模拟结果 C vs A 不适感 −1.02（Holm p=.344，n=6 不足以显著）；297 python tests + 104 js pass |

## TODO（未开始，按序）

阶段：方案 v2 落地（详见 `docs/design/plan-v2.md`）。

0. **拍板 6 项**（plan-v2 §9）：标签命名（inflammatory vs controversy）、student 路线（embedding 头 vs LoRA）、评论是否进主评测、视频清单与边界、双人标注人员、是否把蒸馏/校准写进 docx Phase 2。
1. **扩语料（蒸馏前必做）**：现有 teacher 标签只有 ~1.9k 条（`data/tags/BV1GJ411x7h7.json` 1,407 + `BV1rCxe6wEN9.json` 472），不够训 9 个头；
   先扩到 5–8 个视频、弹幕弱标签 6–10k（deepseek-flash 约 0.53 s/条，6k 条约 55 min）；评论 300–500 条（B站 reply API）；按视频划分 train/val/test。
2. ~~蒸馏 student（bge-m3 + 头）~~ **已被三级级联取代**（用户 m02498 要求 zero-shot、不训练；见 L17）：现路线是 LLM teacher 离线标注 → 每视频线性模型（0.27 ms/条）。剩下的是质量工程：
   ① bootstrap 采样从「按长度分层 80 条」改成「先跑一次全局模型找低置信，再补标难例」；
   ② 每视频模型与 `_global.json` 融合（现为纯覆盖，冷启动视频跨域只有 0.32–0.44）；
   ③ 低置信阈值 0.45 与 teacher 升级延迟（首轮 < 6 s）做参数扫描。
3. **提示词（已到 v4，见 L19）**：v4 已修好「笑死=共鸣」「事实被玩笑吞掉」两类边界；剩下 humour 仍占 902/1407=64%（过标）、attack=0 需换视频、other 率与人工差 ≤ 5pp（当前 other 仅 1/1407）。现有 data/tags/*.json 是 v2 标签，要用 v4 收益需重标（约 0.53 s/条）。
4. **人工标注**：240 条双人（≤3 标签 + 强度）算 Krippendorff alpha；评论 100 条双标；冻结评测集。
5. **bench_labeler 正式版**：per-tag P/R/F1、macro-F1、MAE、Spearman、ECE、p50/p95，附弹幕 vs 评论域迁移差。
6. **预取 + 运行时**：`/api/v1/prefetch`、userscript document-start 预取、缓存 schema v3、fail-open 真机验收。
7. **可用性 session**（3–5 人）：误杀率、NASA-TLX、理解/记忆；更新 docx Phase 2 与 Figure 2。
8. **docx 收尾**：按用户要求去掉 `[Third Author]` 占位符（保留 Zhuang Qian, Tian Muyuan）；如需 PDF 再找转换工具（本机无 LibreOffice）。
9. **密度语义拍板**：现契约「show 类不受密度影响」使密度只降级 hide→collapse、不削减可见量，
   与 RQ1 的 volume 变量有缺口；要么扩展为「show 也可按密度折叠」，要么在论文里把密度限定为
   「重复/攻击类降级」并另设 volume 操作化。
10. **面板体验收尾**：真机检查面板在 1080p/2K/缩放 125% 下的位置与遮挡、popover 边界翻转、
    日志长文本换行（已改 `overflow-wrap` 待验证）；把 5 张真实页截图放进
    `docs/design/` 或 README。

12. **整池预取的收尾**（v0.4 已落地，见 L21）：
    ① `list.so` 单次上限 1,200–3,600 条，超长视频只有前 4 万条；要不要用登录态 wbi seg 拼全池需拍板；
    ② 整池打分 3.6 万条时 `labelPool` 会退化成服务批量（无每视频模型），冷启动首屏等待可能 > 2.5 s；
    ③ 低置信放行开关默认开，已确认关掉后「全隐藏」能整池清零，但真机上要不要默认关需可用性测试；
    ④ study 模式已验证：A/B record-only 走原始段并记录（只记录策略会做什么），C 走整池过滤；三条件都应用密度格抽稀（low = 30 条/分）。

12. **S2 真人执行**（模拟管线已通，见 L25）：按 `data/study/run-sheet.csv`（30 被试 × 6 块 = 180 URL）招人；
    A/B 条件用带屏蔽词账号，C 条件确认整池加载；每块导出 session JSON 到 `data/study/raw/`、问卷填 `data/study/questionnaire.csv`，
    然后 `python3 scripts/study/analyze.py --bootstrap 5000`。注意：模拟运行已证明 n=6 时 Wilcoxon 最小 p≈.031、Holm 后不可能显著，N=16 才够。
13. **IMR 提交收尾**：`submission/Class X + ZonE + IMR.docx` 里的班级号占位 `Class X` 待用户给；
    若还要交 PDF 需另找转换工具（本机无 LibreOffice）。文中标注器数字已改为去泄漏的 .525 / .350（.558/.496 只作为 shipped 模型的乐观对照写在 results-simulated.md）。

11. **按报告结论做质量工程**（见 docs/experiments/REPORT.md §12/§14）：
    ① active learning 降 teacher 升级比例（现状 θ=0.45 时 62–88% 要过 LLM，目标同质量下减半）；
    ② 小视频用 _global.json 先验 + 视频内微调融合（现为硬切换）；
    ③ 上下文改「分数层融合」重做 E2（当前拼字符串为负结果）；
    ④ 相关性轴改「评论区质心 + 时间窗」（E3 唯一弱证据方向，需双人标注验证）；
    ⑤ 边界感知指标已落地（E8 的 must-hit + 逐对表），后续评测默认带这一列。

12. **E8 蒸馏传播为负结果 → 三条落地动作**（见 docs/experiments/e8-boundary.md §6）：
    ① bootstrap 抽样补配额：v4 把同 400 条里的 irrelevant_repetition 从 60 砍到 29、spoiler/conflict 归零，
       scripts/bootstrap_video.py 的分层抽样要按 teacher 输出分布重抽或设最低配额（否则每视频模型丢类）；
    ② 标注量优先于 prompt：400 行学生的 audit 只有 0.517–0.552，1,376 行是 0.690 → 每视频标 800–1,200 行（约 0.5 s/条）；
    ③ 把 46 条最小对立对当训练样本（现在只用于评测），并接 TODO 11① 的难例主动学习。
 13. **_global 冷启动标签不要污染缓存**（压测暴露）：全局模型给 BV1Js411o76u 打了 64% humor 并落盘成 `linear-online`；建议 `_global` 结果只在会话内用，或落盘标 `provisional` 并在面板提示「该视频尚未校准」。
 14. **改偏好立即回放**：reapplyPrefs 目前只重算面板与 DOM，改密度/策略后要等下一段才生效（长视频可达数分钟）；可在偏好变更时对当前 segment 重放 plan 并清理已渲染节点。

 15. **正式实验执行（真人被试）**：材料/模型/埋点/run-sheet/分析脚本已就绪（见 L22 与 docs/study/），剩下按 §10 排期组织被试（N=24，招 30）、场地与伦理审批、主试培训、回收 session JSON 与问卷 CSV 后跑 analyze.py；分析管线已用真实页面导出 + 合成问卷干跑验证（data/study/dryrun/README.md，6 blocks / 1 participant），剩下只需替换成真人数据。S1 金标已有 120 条单标注（data/study/gold.jsonl），analyze.py --gold 已出 per-tag P/R/F1（准确率 0.558、macro-F1 0.496、other recall 0.147）；能补第二标注者时按 docs/study/gold-annotation.md §7 生成 gold_double.jsonl 并报 Krippendorff α。实验前跑 scripts/study/precheck.py 自检 7 项。

## NEW REQ（用户新需求台账）

| 日期 | 来源 | 需求 | 决定 |
|---|---|---|---|
| 2026-10-21 | m00190 | 油猴脚本、先 PC 视频页、现在开工 | 做，M3 |
| 2026-10-21 | m00190 | one-take 客观 MVP，注意可维护/可扩展 | 架构：分层模块 + 契约冻结 + 单测；不做一次性脚本 |
| 2026-10-21 | m00190 | 维护 todo/已做/新需求活文档直至清空 | 本文件；已清零 |
| 2026-10-21 | 设计输入（PRD/reconciliation） | 多标签中间表示、双通道反馈、密度控制、可解释 | 进 MVP（M1/M4/M5/M3 面板） |
| 2026-10-21 | 设计输入 | 自定义标签、共享缓存、Sarcasm/Irrelevance 拆类、审核 | 明确不做（future work） |
| 2026-11-04 | m00475 | 本地 Windows 上的 LFM2/Ollama 接进离线打标链路并调试 | 做，见 L1/L2 |
| 2026-11-04 | m00517 | 模型在 F 盘 | 记进 README（Ollama 必须同时设 OLLAMA_HOST + OLLAMA_MODELS=F:\\Models） |

| 2026-11-04 | m00839 | 先导试验只验证可行性（不是验证假设）；分类器改「标签词表 + relevance 向量」；优先做油猴脚本 | 已按此改设计：矢量 prompt、pilot 定位 feasibility、M3 userscript |
| 2026-11-04 | m00842 | 先跑通再优化；提供 DeepSeek key（存本地、不进库） | 做：`.env.local`（已 gitignore）+ 云端批量打标 |
| 2026-10-29 | m00870 | 作业：revised introduction + pilot study（>5 篇来源、6 项内容、TNR 11、文件名 Group + Introduction + pilot study） | 做，L7 |
| 2026-10-29 | m00871 | 「把这里面提到的预研究的文档也写了」 | 做：pilot 数据 + `docs/pilot/README.md` + docx |
| 2026-11-04 | m00893 | 模型名 `deepseek-chat` → `deepseek-flash`（官网已改名） | 做，L5 |
| 2026-11-04 | m01115 | 5 点方案优化：①不要逐条调 LLM，换小模型 + batch + 预取；②弹幕验证不够，需评论；③视频太友善，要节奏/时政 + 新 tag；④other 正式化；⑤confidence 要真概率（小模型） | 做：plan-v2.md + taxonomy v0.3 + classify 批量化；蒸馏/校准/预取进 TODO |
| 2026-11-04 | m01294 / m01302 | 「前端」指的是油猴脚本面板，不是独立 prototype 网页；先做面板 | 做，L12：prototype 降级为视觉稿，UI 搬进 `extension/src/main.js` 并在真实 B 站页验证 |
| 2026-11-05 | m02118 / m02245 / m02357 | 不要规则匹配（太窄），做消融；改用端侧 Ollama 小模型（用户自己拉模型） | 做，L15：消融已完成（rules 0.19–0.35 vs qwen3.5:4b 0.44–0.59）；Ollama 原生 /api/chat + num_ctx 打通；蒸馏扩语料进 TODO 1 |
| 2026-11-05 | m02498 / m02558 / m02613 | 要 zero-shot、不要训练（标签由用户事后添加）；眼光打开看传统 NLP；授权「LLM 做 teacher 提前训练小模型」 | 做，L16 + L17：三级级联落地（L0 缓存 / L1 每视频线性模型 0.27 ms 条 / L2 LLM teacher 异步补标）；原蒸馏方案作废 |
| 2026-11-05 | m03133 / m03142 | 问标签边界怎么解；批准只做 ②判定顺序 + ③最小对立对（①双人标注暂缓），要求「来个 goal」 | 做，L19 + goal-4ef262ed：prompt v4 成为默认；teacher audit 0.759→0.897、must-hit 0.913→0.957；结论写成 docs/experiments/e8-boundary.md |
| 2026-11-05 | m02908 / m02911 | 问「NLP 无法理解环境（不同领域视频）怎么办」；要求把全部数据、消融对比实验做一遍并写标准格式 markdown 实验报告 | 做，L18：6 视频 2,457 条 / 9 组实验 / REPORT.md；结论：环境不能靠拼特征或环境向量补，必须每视频 bootstrap + 用户策略 + LLM 零样本兜底 |
| 2026-11-06 | m03456 |「给你来个压测」+ 151 万弹幕视频（BV1Js411o76u） | 做，L20 + `docs/experiments/e9-stress.md`：整条链路可用（0.007 ms/条、帧 p95 14 ms），顺带修 7 个问题；遗留「改偏好立即回放」「_global 落盘」进 TODO 13/14 |


## DONE 台账

| 日期 | 内容 | 证据 |
|---|---|---|
| 2026-11-18 | v0.4 整池预取（L21）：`/api/v1/model` + `pool.js` + `poolweb.js` + 面板折叠记录改为整池真相源 + `fail_open` 开关 + 整池统计上角标 | 99 js tests、259 python tests；BV1Js411o76u 整池 3,600 条 / 720 ms / 动作表 0.66 ms；strict hide 渲染层 0 条；截图 `docs/screenshots/pool-v4-{folded,allhide,allshow,strict-hide,density-folded}.png` |
| 2026-11-18 | S2 正式研究材料与工具链（L22）：6 支真实刺激视频定稿 + 材料级 social 校验；teacher 抽样标注 988 行、每视频线性模型重训、report ready；研究模式 A/B/C 端到端冒烟（A/B 只记录、C 真过滤）；低密度格抽稀（thinSegmentDrop，30 条/分、10 s 窗、稳定 hash，三条件生效）；session_url/run-sheet（180 URL）、demo 分析链路、session-kit | 263 python tests + 103 js pass；A-high 972 shown / A-low 60 shown（thinned 931）/ C-low 60 / C-high 925；`data/study/report.json` ready=true；修 3 个 bug：study 条件 A/B/C 别名、thinned 未透传、GM shim 经页面 fetch 导致同一段被二次过滤（新增 state.nativeFetch 与 record 合并取最大） |
| 2026-11-18 | S2 收尾工具（L23）：单标注金标 120 条 + analyze --gold 打通 per-tag P/R/F1；主试清单/结果模板；precheck.py 一键自检（7 项） | 金标：dominant 0.558 / macro-F1 0.496 / other recall 0.147（模型从不判 other）；precheck 在真实仓库 7/7 PASS；281 python tests + 103 js pass |
| 2026-11-18 | S2 分析管线 dry-run（真实导出 + 合成问卷）：条件标识兼容（all_on/keyword/prototype ↔ A/B/C，新增 condition_label）、重复记录延迟取最大、清洗规则说明；session-kit 补每块导出/命名流程 | 265 python tests + 103 js pass；dry-run `analysed 6 blocks / 1 participants`，A/B/C 各 2 块，RQ1/RQ2 表正常产出；C 条件 6 支实测过滤占比 10.1–51.6%（`c-condition-check.json`）；数据在 `data/study/dryrun/`（合成问卷，不可当结果引用） |
| 2026-11-19 | S2 模拟运行（L25）：6 个 LLM 被试 36 块 + 拉丁方修正 + 组装/分析全链路；IMR 更新为去泄漏数字并补统计方法段与 §2.6 模拟小节 | `python3 scripts/study/analyze.py --bootstrap 5000` → analysed 36 blocks / 6 participants；A/B/C 不适感 3.65/3.43/2.63、控制感 2.67/2.92/4.89；拉丁方修正后每视频每条件 2 次；去泄漏 labeler agreement .525 / macro-F1 .350；docx 2,532 词 / 9 表 / 1 图，TNR 11 双倍行距 |
| 2026-11-18 | 统计口径 QA（L24）：α 有限样本修正 + Wilcoxon/Holm/Cronbach 参考值回归 + 功效模拟 | Krippendorff α 原实现低估（0.6 vs 0.62、0.2 vs 0.24），改为 (N²−Σn²)/(N(N−1)) 后与 krippendorff 0.9.0 四组样例一致；Wilcoxon 与 scipy 逐值一致（p=0.1060/0.3428），Holm 参考例 [0.005,0.08,0.12,0.4,0.5]；power_sim：N=24 时 d=0.6 → .78、d=0.7 → .90（Holm 后 .61/.74）；286 python tests + 103 js pass；文档 protocol §2.4/§8.2、results-template §2.5、gold-annotation §5/§7 |
| 2026-11-06 | 压测 E9（151 万弹幕视频，单段 5,886 条）+ 7 个修复：`_TagStore` 缓存（服务端 2,288 ms → 41.1 ms / 56×）、teacher 队列上限 4,000、密度只对 show 竞争槽位、暂停角标提示、decisions 带 labels 修面板统计、BrokenPipe 静默、旧构建注入排查 | `docs/experiments/e9-stress.md`；193 python tests + 58 js pass；截图 `docs/screenshots/e9-density-{off,on}.png` |
| 2026-11-05 | 全量实验 + 实验报告（E0–E7） | docs/experiments/REPORT.md、data/experiments/results/*.json；2,457 条 / 6 视频；178 tests OK |
| 2026-11-05 | 三级级联落地：linear.py + train_models.py + bootstrap_video.py + server linear 后端（模型热加载、低置信 teacher 升级）+ JS 打分器 | 178 tests OK / 57 js pass；1,400 条 0.274 ms/条、同视频一致率 0.92；冷启动 71 条 teacher 标签生成 80.8 KB 模型；留一视频 0.32–0.44；见 ablation-v1.md §9 |
| 2026-11-05 | 标签边界：prompt v4（判定顺序 + 软化锚点）成默认；46 条最小对探针 + 边界感知指标 | deepseek audit 0.759→0.897、boundary must-hit 0.913→0.957；docs/experiments/e8-boundary.md |
| 2026-11-05 | 消融 v1（rules vs qwen3.5:4b）+ Ollama native 通道（num_ctx、去 format=json）+ 在线 LLM worker | `docs/design/ablation-v1.md`；146 tests OK；qwen3.5:4b 8 条/2.2 s、0.27 s/条单测速；human/teacher 指标见文档 |
| 2026-10-21 | 摸清 seg.so 协议（360s/段、裸 protobuf、空段 304、list.so 采样） | plan-v1.md §2 探测表 |
| 2026-10-21 | 契约 v1 冻结、写作用域划分 | contracts.md |
| 2026-10-21 | Part 5 + 500 词 Introduction 提交稿 | submission/；docs/intro/README.md |
| 2026-10-21 | dmproto.py 实现 + fetch.py seg.so 数据源（get_video_info/fetch_segment/fetch_all_segments）+ CLI fetch --source | tests/test_dmproto.py、tests/test_fetch.py 全绿 |
| 2026-10-21 | policy.py（DEFAULT_PREFS/normalize_prefs/resolve_item/decide）+ types.tags 字段 | tests/test_policy.py 全绿 |
| 2026-10-21 | CLI 新增 precompute / serve 子命令（延迟导入 pipeline/server） | `python3 -m danmaku_filter.cli --help` |
| 2026-10-21 | cache.py / pipeline.py / server.py + 47 单测（server-dev） | 全套 91 tests OK |
| 2026-10-21 | extension/ 全套 userscript：dmproto/policy/main/build + 40 单测（userscript-dev） | node 40 pass、node --check 通过、dist sha 稳定 |
| 2026-10-21 | 串起端到端：真实 seg 样本 fixture、precompute 示例缓存、scripts/smoke_test.py（15 项）、eval 链路 | 见 contracts.md §11 验收快照 |
| 2026-10-21 | 契约偏差登记（10 条）+ README 重写 + 本文件清零 | contracts.md §10/§11；README |
| 2026-11-04 | `llm.py` 支持任意 OpenAI 兼容端点：本地免 key、DM_LLM_BATCH/TIMEOUT/TEMP/RETRIES、5xx+空响应重试升温；新增 7 个 LLM 单测 | tests/test_llm.py；98 tests OK |
| 2026-11-04 | LFM2 实测：22 条抽样标签合理、1.5 s/条；gold 9 条 acc 0.778；bench 脚本入库 | /tmp/bench_out/、README「本地模型」 |
| 2026-11-04 | 默认模型切 deepseek-flash，`.env.local` 存 key 并 gitignore | `.env.local`、`llm.py`、README；119 tests OK |
| 2026-11-04 | 全量重标 1,407 条（0.53 s/条，12.5 分钟） | `data/tags/BV1GJ411x7h7.json` backend=llm model=deepseek-flash |
| 2026-11-04 | 先导审核 29 条（89.7% / MAE 0.05） | `scripts/audit_score.py`；`data/pilot/` |
| 2026-11-04 | 10/29 作业 docx（introduction + 设计 + pilot + 6 篇 APA + Figure 1） | `submission/ZonE + Introduction + pilot study.docx`；`scripts/build_pilot_doc.py` |
| 2026-11-04 | 新增 `reclassify`（pipeline + CLI）与 JSON strict=False 容错；README 补用法、单测数 105 | pipeline.py / cli.py / tests/；README |
| 2026-11-04 | taxonomy v0.3：+political/+inflammatory、other 正式化（OTHER_DEF + 使用规则）、prompt 规则同步 | taxonomy.py / taxonomy.md §8 / config/taxonomy.json；124 tests OK |
| 2026-11-04 | CLI `classify` 批量化 + `tests/test_cli.py`、`tests/test_taxonomy.py` | 124 tests OK |
| 2026-11-04 | 方案 v2 设计文档（5 点回应/架构/选型/评测矩阵/两周排期/待拍板） | docs/design/plan-v2.md |
| 2026-11-04 | extension 同步 v0.3（TYPES/LABELS_ZH/DEFAULT_PREFS）、重建 dist、40 js tests OK | extension/lib/policy.js；dist |
| 2026-11-04 | docx 去掉 [Third Author] 占位符 | scripts/build_pilot_doc.py；docx 重生成 |
| 2026-11-04 | userscript 面板重写：角标（品牌/计数/状态点/控制台/开关）+ 三 tab + 10 标签三态 + 全局参数 + 折叠卡（恢复显示/改标签）+ 反馈日志 + popover | extension/src/main.js；dist 2729 行；40 js tests OK |
| 2026-11-04 | 修复真实页不拦截：播放器改走 `/x/v2/dm/wbi/web/seg.so`，`SEG_PATHS` 同时匹配旧/新路径；extension/README 同步 | extension/src/main.js；README |
| 2026-11-04 | ego-browser 真实 B 站页端到端：GM shim + CDP 注入，connected=true、~5.9k 条/段、折叠 52、改标签/恢复显示生效 | `/tmp/ego-bili*.mjs`；截图 `C:/Users/alphayang/ego/proto/bili-*.png` |
| 2026-11-04 | 面板布局修补：body 最小 220px 防塌、toast 移到角标上方（fixed）、seg 按钮对比度、已显示条目隐藏「恢复显示」按钮 | extension/src/main.js 重建 |

## 决策记录

- 运行时只在网络层改 seg.so 响应，不碰 DOM；hook 失败一律 fail-open（plan-v1 §3）。
- LLM 只在离线预计算调用；运行时零 LLM 调用。
- 决策只读本地缓存 + override，不联网；本地服务不可用 → 原样渲染。
- 多标签用于运行时 item（tags 字典），gold 标注仍单主标签（reconciliation §2.2）。
- 密度控制放客户端（简单窗口），服务端只做类型级决策（contracts §5）。
- 7 类 + other 兜底；Irrelevance 暂不拆（reconciliation §2.1）。
- XHR 路径自持请求（GM 重拉一次 seg.so）换取避免 CSP/CORS 与可回退；见 contracts §10 第 4 条。

## 明确不做（MVP 之外）

- 直播/手机端/多用户共享缓存/自定义标签/自动审核/Sarcasm 主标签/浏览器扩展（MV3）。
- 运行时逐条 LLM 补标（miss 只进 pending 队列，人工/离线补）。
- 浏览器内自动化验收（本环境无浏览器；改为 extension/README.md 15 条真机清单，由用户执行）。
- 7 类人工标注 pilot、用户实验（属研究阶段，见 README 路线图，不在 MVP 代码范围）。
