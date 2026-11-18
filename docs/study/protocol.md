# 正式受控实验执行方案（S2 Controlled Viewing Study）

> 版本 v1 · 2026-11-18 · 对应 docs/research-design.md 的 S2，并与 S1（标注评测）衔接。
> 本文件是「可以照做」的执行手册：设计、材料、流程、问卷、埋点、分析、排期、分工、风险。

## 0. 一页速览

| 项 | 内容 |
|---|---|
| 研究类型 | 被试内（within-subject）受控观看实验 |
| 条件 | A 全部弹幕开启 / B 平台关键词屏蔽 / C 社会功能过滤（我们的原型） |
| 被试 | N = 24（招募 30 防流失），B 站常规用户（≥3 小时/周） |
| 材料 | 6 支 3–5 分钟真实视频（3 信息型 + 3 娱乐型），真实弹幕流，密度自然分层（低 ~30 条/分，高 ~150 条/分） |
| 单场时长 | 70–85 分钟 |
| 平衡 | 正式设计：4 条件 × 8 块/人，4 条拉丁方（每 4 人一轮完全平衡）；模拟试跑：6 块/人、{A,A,B,B,C,D} 轮转（见 2.1 的变更说明） |
| 主要测量 | 不适感、共看感、控制感、FOMO、NASA-TLX、理解测验、可解释性、会话日志 |
| 产出 | data/study/raw/P01..P24、data/study/analysis.json、结果表与图 |
| 关键依赖 | task-7 研究模式（脚本）、task-8 材料准备与分析脚本 |

## 1. 研究问题与对应假设

| 编号 | 问题 | 假设 | 本实验如何回答 |
|---|---|---|---|
| RQ1 | 弹幕的类型与密度如何影响观看体验？ | H1a 攻击/争论/剧透提高不适；H1b 信息/幽默/共鸣提升共看感；H1c 高密度放大类型效应 | 只用条件 A 的 6 支视频，按视频的 genre × density 分析 |
| RQ2 | 分类器是否够准够快？ | H2 macro-F1 ≥ 0.70，p50 延迟足够离线预计算，spoiler 最弱 | S1 标注（180 条双标）+ 离线评测 + 会话日志延迟 |
| RQ3 | 个性化过滤是否改善体验、降低负担？ | H3a 不适↓控制↑；H3b setup ≤3 min、纠正 ≤1 次/10 min；H3c 不损信息理解；H3d 隐藏可解释；H3e 效果超过 sham 界面 | A/B/C/D 四条件对比（C vs A/B/D）+ 日志（setup 时间、纠正次数）+ 简短评分 |

## 2. 设计

### 2.1 条件

| 代号 | 名称 | 页面状态 | 说明 |
|---|---|---|---|
| A | all-on | B 站默认；**关闭**关键词屏蔽；脚本以 record-only 运行（不干预渲染，只记录） | 基线：未经任何过滤的真实弹幕环境 |
| B | platform-keyword | B 站原生「屏蔽词」开启并预置 5 个词；脚本 record-only | 代表平台现有能力：任意词/正则匹配，无语义 |
| C | prototype | 脚本 prototype 模式：attack / conflict / inflammatory / irrelevant_repetition → 隐藏；spoiler → 折叠（面板列表可找回原文）；其余 → 显示；阈值 0.30 | 我们的社会功能过滤 + 可解释 + 可纠正 |
| D | sham | 面板、整池加载进度、折叠列表都在，但**不过滤**（全 show、不折叠） | 分离界面/期待效应：A vs D 量界面本身，C vs D 量语义过滤的净效应 |

> 变更（2026-11-19，加 sham 条件 D 后）：正式设计改为 **4 条件 × 8 块/人**（每条件 2 块），4 条件拉丁方；`scripts/study/session_url.py` 的 run sheet 生成器目前仍是 3 条件/6 块，正式招被试前必须先改并重生成（`data/study/run-sheet.csv` 也要重跑）。模拟试跑（docs/study/results-simulated.md）用的是缩减版 6 块/人（每人条件多重集 {A,A,B,B,C,D}）与盲化材料，仅用于验证管线与设计，不是正式设计。

**密度操纵（四个条件都生效）**：每个视频块带一个密度格，由 dmf_density 参数控制。high = 保持原样（实测 150–750 条/分）；low = 脚本按 10 秒时间窗 + 稳定 hash 抽稀到 **30 条/分**（每窗 5 条，被抽掉的直接不渲染）。这道操纵在 A/B（record-only）条件下同样执行，否则 RQ1 的「高密度放大类型效应」检验没有低密度格可用。所有 6 支视频实测峰值都 ≥ 150 条/分，低格只靠抽稀实现，不依赖视频本身稀疏。

B 条件的屏蔽词（对所有被试统一，事先写在设备上，实验后清除）：

~~~
剧透
前方高能
傻逼
打卡
？？？
~~~

> 说明：这 5 个词覆盖「剧透」「攻击」「无意义重复」最朴素的形态，正好对应我们三个被过滤的标签，用来做「同样想过滤、但只有字面手段」的对照。

### 2.2 材料：6 支视频 × 3 个视频对

每对 = 1 支信息型 + 1 支娱乐型，且 1 支高密度 + 1 支低密度。这样可以保证每个条件、每个被试都同时看到两类内容和两种密度。

| 对 | 视频 | 类型 | 密度格 | 时长 |
|---|---|---|---|---|
| P1 | BV1Ho4y1D7Wz（铋晶体，科普） | 信息型 | 高 | 186 s |
| P1 | BV18E1NByEYv（哪个游戏的网络环境最恶臭） | 娱乐型 | 低 | 290 s |
| P2 | BV1VMRKBCEDp（3 分钟解释睡眠时长） | 信息型 | 低 | 214 s |
| P2 | BV1X7zGY4E45（自由式辩论赛场） | 娱乐型 | 高 | 194 s |
| P3 | BV1Z4RHYKEcB（金融世界观） | 信息型 | 高 | 281 s |
| P3 | BV1CzW26vEXz（日网友弃坑推文） | 娱乐型 | 低 | 271 s |

> 筛选过程、候选实测与理由见 `docs/study/stimuli-selection.md`；机器可读清单在 `data/study/stimuli.json`，实测报告在 `data/study/report.json`。所有视频实测峰值密度 ≥150 条/分，低密度格由固定规则抽稀实现，不依赖视频本身稀疏。

选取标准：

1. 3–5 分钟、桌面端可播放、不需要大会员；
2. 弹幕总量足够（该时长内 ≥ 300 条），且密度落在目标区间（用 prepare_stimuli.py 实测每分钟条数）；
3. 信息型 = 科普/评测/讲解，有可验证的事实点（能出 5 道理解题）；娱乐型 = MV/搞笑/混剪；
4. 内容安全：不含真实人身攻击视频、不含时政敏感画面；弹幕里出现攻击性内容是研究材料的一部分，但视频本身需能过课程伦理审查；
5. 标签分布合理：**三支娱乐视频合计**的攻击/争论/剧透各至少 5 条（单支视频不强制），否则 C 条件看不出差异；`prepare_stimuli.py` 的 `summary.social` 校验这个口径；
6. 被试可能看过 → 每块问「之前看过吗」，作为协变量。

> 落地起点：`data/study/stimuli.json` 里先放占位符，由 prepare_stimuli.py 校验后替换成真实 bvid。若某支视频密度不达标，用 V2 方案：服务端按固定比例抽稀（thinning）降到低密度，抽稀逻辑要固定随机种子并在日志里记录。

### 2.3 顺序平衡（拉丁方）

每个被试看全部 6 支视频，每个条件 2 支（1 信息 1 娱乐）。视频对与条件按 6 行循环分配，
**每一行都覆盖全部三个视频对**（不能用「行内互换」的写法，否则会出现某支视频从没在 C 条件出现过）：

| 被试编号 mod 6 | A 条件用的视频对 | B 条件用的视频对 | C 条件用的视频对 | 条件出现顺序 |
|---|---|---|---|---|
| 0 | P1 | P2 | P3 | A → B → C |
| 1 | P2 | P3 | P1 | A → C → B |
| 2 | P3 | P1 | P2 | B → A → C |
| 3 | P1 | P3 | P2 | B → C → A |
| 4 | P2 | P1 | P3 | C → A → B |
| 5 | P3 | P2 | P1 | C → B → A |

这样 6 行合计后，**每支视频在 A/B/C 各恰好出现 2 次**（例如 P1 的信息型视频在每个条件下各播 2 次），
条件与视频不混淆。每块的行顺序由 `urls_for` 决定：第 position 个条件占 block 2·position−1 与 2·position，
对内的两支视频按 stimuli.json 的顺序（信息型在前、娱乐型在后）依次播放。
study_id 形如 `P01-1-A`（被试-块号-条件），每个 block 唯一，导出文件不会互相覆盖。N = 24 时每行恰好 4 人。

> 核对命令：`python3 scripts/study/session_url.py --sheet data/study/run-sheet.csv`，
> 结果里每个 bvid 应出现 30 次（30 名被试），且三种条件各 10 次。

### 2.4 被试与功效

- 被试内、3 水平、α = .05、power = .80、中等效应 f = 0.25 → 约 24 人；招募 30 人（流失、设备问题、数据不合格）。
- 最小可检测效应（模拟，`python3 scripts/study/power_sim.py --n 24 --reps 2000`，paired Wilcoxon 正态近似、高斯噪声）：d = 0.6 时 power ≈ .78、d = 0.7 时 ≈ .90；经 Holm 校正 3 次后分别约 .61 / .74。结论：N=24 对 d ≥ 0.7 的差异有 80% 以上把握，更小的效应只报效应量与 95% CI、不宣称显著（writing up 时把这一句放进方法或局限）。
- 纳入标准：18 岁以上；每周看 B 站 ≥3 小时；桌面端 Chrome/Edge；视力正常或矫正；能用中文交流。
- 排除：参与过本项目 pilot；与我们同组；看过 ≥3 支刺激视频（记录后作为敏感性分析排除项）。
- 抽样：校园便利抽样 + 社交平台招募，记录渠道来源；报告时说明是便利样本。
- 报酬：¥50/人（或课程加分，按学校规定）；无论是否完成都可获得基础报酬。

## 3. 测量

### 3.1 每个视频块结束后（约 3–4 分钟）

| 构念 | 题数 | 形式 | 备注 |
|---|---|---|---|
| 不适感 discomfort | 5 | 7 点 Likert（1 完全不同意 – 7 完全同意） | 「这段弹幕让我感到不适 / 被冒犯 / 烦躁 / 想关掉弹幕 / 影响我看内容」 |
| 共看感 co-viewing | 3 | 7 点 | 「像和其他观众一起看 / 有共鸣 / 氛围是友好的」 |
| 控制感 control | 3 | 7 点 | 「我能控制自己看到什么 / 过滤结果符合预期 / 我能找回被隐藏的内容」 |
| FOMO | 1 + 开放 | 7 点 + 一句话 | 「我担心错过有用的信息」 |
| 认知负荷 burden | NASA-TLX 6 项 | 7 点（或 0–100 刻度） | 脑力/体力/时间压力/绩效/努力/挫败 |
| 理解 comprehension | 5 选择 + 自由回忆 | 选择题 + 30 秒口述 | 仅信息型视频 |
| 可解释性 explainability | 2 + 开放 | 是/否 + 一句话 | 仅 C 条件：「你能说出为什么这条被隐藏吗」 |
| 熟悉度 familiarity | 1 | 是/否 + 看过几次 | 协变量 |
| 总体评价 | 2 | 7 点 | 「愿意继续用 / 会推荐给朋友」 |

### 3.2 会话开始前

- 人口学：年龄、性别、学历、专业；
- B 站使用：年限、每周时长、常用设备、是否常开弹幕、是否用过屏蔽词/关弹幕；
- 弹幕态度 5 题（喜欢/烦/有帮助/影响理解/愿意付费过滤）。

### 3.3 会话结束后（10 分钟半结构访谈）

1. 哪个条件最舒服？为什么？
2. C 条件里有没有「不该被隐藏却被隐藏」或「该隐藏没隐藏」的例子？（回放日志里的纠正记录）
3. 折叠（spoiler）和直接隐藏，你更接受哪种？为什么？
4. 你信任它自动做决定吗？如果它错了你会怎么办？
5. 如果明天就能装上，你会用吗？会怎么设置？
6. 你觉得「按类型过滤」和「按关键词过滤」最大的差别是什么？

### 3.4 行为日志（脚本自动）

见 §7。用于：setup 时间、纠正次数、每标签计数、延迟、是否 fail-open。

## 4. 流程与分钟表

| 时间 | 环节 | 内容 | 负责人 |
|---|---|---|---|
| 0:00–0:05 | 签到与同意 | 编号、同意书签字、说明可随时退出 | 主试 |
| 0:05–0:12 | 基线问卷 | 人口学 + B 站习惯 + 弹幕态度 | 主试 |
| 0:12–0:20 | 设备与练习 | 打开练习视频（60 s），演示：看弹幕、点开折叠、纠正标签、调偏好；告知 think-aloud | 操作员 |
| 0:20–0:28 | 条件 1（2 支视频） | 视频 A（3–4 min）→ 问卷 → 视频 B → 问卷 | 操作员 + 主试 |
| 0:28–0:36 | 条件 2 | 同上 | 同上 |
| 0:36–0:40 | 休息 | 3–4 分钟，不讨论条件 | 主试 |
| 0:40–0:48 | 条件 3 | 同上 | 同上 |
| 0:48–0:58 | 收尾问卷 | 条件两两偏好、总体 SUS、open | 主试 |
| 0:58–1:08 | 访谈 | §3.3 六问，录音（可选） | 主试 |
| 1:08–1:15 | 结束 | 报酬、debrief、删除本地数据确认 | 主试 |

时间预算：6 支视频 × (3.5 min 观看 + 4 min 问卷) ≈ 45 min，其余 30 min。若某被试明显疲劳，允许在休息处提前结束并记录。

## 5. 每场实验的操作清单（runbook）

### 5.1 实验前 10 分钟

1. 启动本地服务：`cd ~/bili-tagging && DM_LLM_TIMEOUT=300 python3 -m danmaku_filter.cli serve --online-backend linear`；浏览器打开 `http://127.0.0.1:8765/api/v1/health` 确认 `ok:true` 且 `models ≥ 6`。
2. 确认 6 支视频的缓存与模型都在：`ls data/tags/<bvid>.json`、`ls data/models/<bvid>.json`（prepare_stimuli.py 的 report.json 应已核对）。
3. 打开浏览器干净 profile（避免旧 localStorage）：确认 `localStorage` 里没有 `dmf.prefs.v1`、`dmf.study`。
4. 清空 B 站原生屏蔽词设置（条件 A/C），记录条件 B 需要预置的 5 个词。
5. 打开计时器与录屏（可选，仅屏幕，不录摄像头/麦克风；被试可拒绝）。
6. 准备好 URL 模板：`https://www.bilibili.com/video/<bvid>/?dmf_study=P01&dmf_cond=<A|B|C>`。

### 5.2 每个条件开始前

1. 按 §2.3 查到本被试该条件对应的视频对；
2. 设置条件（A：关关键词、record-only；B：开关键词 5 词、record-only；C：脚本 prototype 预置）；
3. 打开第 1 支视频 URL，等首段拦截完成（角标/控制台出现决策计数），开始计时；
4. 视频结束后立即暂停，被试填问卷（纸质或在线表单），再播第 2 支；
5. 条件结束：在控制台执行 `__dmfFilter.study.download()`，确认下载的 `dmf-study-P01-<cond>.json` 存在；
6. 条件之间清空页面（关标签页重开），避免上一条件状态残留；
7. 在 runbook 表上勾选：条件、视频、开始/结束时间、异常备注。

### 5.3 实验后 10 分钟

1. 把 `data/study/raw/P01/` 下 3 个 JSON + 问卷 CSV + 笔记归档；
2. 删除浏览器 profile（或至少清 localStorage）、清除 B 站屏蔽词；
3. 备份到 `data/study/raw/` 之外的加密目录（如 `~/study-backup/`），原始录屏编码后删除；
4. 在 `data/study/runbook.csv` 追加一行（编号、日期、实验者、异常）。

## 6. 条件与设备核对（每次实验后）

| 检查项 | A | B | C |
|---|---|---|---|
| B 站屏蔽词 | 空 | 5 词 | 空 |
| 脚本 enabled | false（record-only） | false（record-only） | true |
| 面板/角标 | 隐藏 | 隐藏 | 隐藏（被试可要求显示） |
| 密度格 | 按 URL 的 dmf_density（high / low=30 条/分） | 同左 | 同左 |
| 会话日志 | 有（只统计） | 有（只统计） | 有（含纠正） |

## 7. 埋点与数据格式

### 7.1 启动参数

~~~
https://www.bilibili.com/video/<bvid>/?dmf_study=P01-A&dmf_cond=A&dmf_density=high
~~~

`dmf_cond` ∈ {A, B, C}；`dmf_density` ∈ {high, low}（缺省 high）；`dmf_study` = 被试编号 + 条件（也允许只写编号，条件从 localStorage 读）。脚本在 study mode 下：默认隐藏面板与角标；C 条件预置策略并生效；A/B 只记录不干预。

链接不要手写：用 `python3 scripts/study/session_url.py --sheet --participants 30` 生成 `data/study/run-sheet.csv`（30 被试 × 6 块 = 180 条 URL），现场按表逐条打开。被试编号决定拉丁方行（编号 mod 6），不要改名。现场操作、导出与录入细节见 `docs/study/session-kit.md`。

### 7.2 导出 JSON（每块一个文件）

一个 URL 只覆盖一个视频，所以**每块结束立刻导出并改名**：`dmf-study-<study_id>-<bvid>.json`（同一被试的 A 条件有两个视频，不改名会互相覆盖）。在播放页控制台执行 `window.__dmfFilter.study.download()`，或 `window.__dmfFilter.study.export()` 拿对象自行保存。

~~~json
{
  "study_id": "P01-1-A",
  "condition": "all_on",
  "condition_label": "A",
  "started_at": "2026-11-20T10:05:11.000Z",
  "ended_at": "2026-11-20T10:08:31.000Z",
  "script_version": "0.4.2",
  "prefs": { "min_confidence": 0.3, "per_type": { "...": "..." }, "density": { "enabled": false } },
  "counts": { "segments": 1, "shown": 991, "hidden": 0, "collapsed": 0, "thinned": 0,
              "would_shown": 972, "would_hidden": 19, "would_collapsed": 0,
              "prefs": 0, "corrections": 0, "restores": 0, "expands": 0, "dropped_events": 0 },
  "events": [
    { "t": 1795003484209, "type": "segment", "bvid": "BV1Ho4y1D7Wz", "url": "https://api.bilibili.com/x/v2/dm/wbi/web/seg.so?...",
      "segment_index": 1, "shown": 991, "hidden": 0, "collapsed": 0, "thinned": 0,
      "by_label": {}, "latency_ms": 1, "online": true, "applied": false,
      "would": { "shown": 972, "hidden": 19, "collapsed": 0, "by_label": { "attack": 19 } } },
    { "t": 1795003490123, "type": "correction", "dmid": "123", "text": "原文", "from": "attack", "to": "humor" },
    { "t": 1795003491000, "type": "restore", "dmid": "456", "label": "spoiler" },
    { "t": 1795003492000, "type": "expand", "dmid": "789", "label": "spoiler" },
    { "t": 1795003493000, "type": "pref", "key": "attack", "from": "hide", "to": "show" }
  ],
  "ua": "Mozilla/5.0 ..."
}
~~~

字段说明：`condition` 是规范 id（`all_on`/`keyword`/`prototype`/`sham`），`condition_label` 是协议里的 A/B/C/D（分析脚本优先读后者，旧导出没有时从 `study_id` 后缀取字母）；`events[].t` 是 epoch 毫秒；`thinned` 是本段被密度格抽稀掉、本来会显示的条数；`applied` 表示这一段是否真的改写了弹幕字节（A/B 条件恒为 false）；`online` 表示决策是否走了服务端实时打标。

**applied 与 would 的区别（v0.4.2 起）**：`shown/hidden/collapsed` 一律记「被试实际看到的」，所以 A/B 恒为 `shown = 段内全部 − 抽稀, hidden = 0, collapsed = 0`；原型策略在 A/B 上「本来会怎么做」记在 `would`（及整块累计的 `counts.would_shown/would_hidden/would_collapsed`）。这样 RQ2 的行为表可以同时回答「条件实际过滤了多少」和「若启用原型会过滤多少」，不会把反事实当成事实。

约束：事件上限 5,000（超出丢最旧，`counts.dropped_events` 记账）；文本字段截断到 200 字；不记录任何用户名/uid；`counts` 为整块累计。

### 7.3 问卷 CSV

一行 = 一个被试的一个视频块：

~~~
participant,condition,video,genre,density,block_order,familiar,discomfort_1..5,coview_1..3,control_1..3,fomo,expectancy_1..2,nasa_1..6,comp_1..5,recall,explain_1..2,overall_1..2,notes
~~~

问卷用在线表单（问卷星/Google Form）导出 CSV，或纸质录入；列名与上面一致，方便 analyze.py 直接读。

## 8. 分析计划

### 8.1 数据清洗

- 排除标准（事先写死）：单场访谈未完成、明显未观看（页面停留 < 视频时长 60%）、设备故障导致条件错配、自报看过 ≥4 支视频 → 主分析排除，敏感性分析保留；
- 反向题先反向计分；每个量表算均值 + Cronbach α（α < .60 的题在报告中标注）；
- 日志与问卷按 (participant, condition, video) 对齐；日志缺失的块用问卷里的自报纠正次数替代并标注。

### 8.2 主分析

RQ3（A/B/C/D 四条件）：

~~~
score ~ condition + (1 | participant) + (1 | video)
~~~

- 计划对比：C vs A（核心）、C vs B（相对平台能力）、B vs A（平台现有能力是否有效）；Holm 校正 3 次；
- 非正态：对齐秩变换（ART）或 ordinal mixed model；报告 Cohen's d / 效应量 + bootstrap 95% CI（5,000 次，按被试重采样）；
- 主要结局：discomfort、control；次要：co-viewing、FOMO、NASA-TLX、overall。

RQ1（仅条件 A，视频层）：

~~~
score ~ genre * density + familiarity + (1 | participant) + (1 | video)
~~~

- 因每个被试在 A 只看到 2 支视频，genre × density 的交互用被试间（视频层）信息，报告为探索性；
- 若 N=24 仍不足，改为描述性对比 + 效应量，不做显著性宣称。

RQ2（离线）：

- 金标：data/study/gold.jsonl 目前为 120 条单标注（6 支视频各 20 条，构造与分布见 docs/study/gold-annotation.md）；用 analyze.py 的 --gold 算 per-tag P/R/F1 与 macro-F1；relevance MAE/Spearman 只在 gold 行带 score 时可用；
- 双标注：如能补第二名标注者，按 docs/study/gold-annotation.md §7 生成 data/study/gold_double.jsonl 并加 --gold-double，报告 Krippendorff α（≥.67 可接受，≥.80 好）；只有单标注者时必须在局限里声明 α 未估计；α 用名义型公式并按 N(N−1) 做有限样本修正（analyze.py 已与参考实现逐值对齐）；
- 金标指标口径：dominant 准确率 + macro-F1（在「预测标签 ∪ gold 标签」上平均，预测到而 gold 没有的标签 support 记 0，输出里带 macro_f1_note），并给 per-tag P/R/F1 与 support；
- 延迟从会话日志算 p50/p95，另附离线 precompute 时间/视频与模型体积。

### 8.3 定性

- 访谈转写（不录音则用笔记），两位编码者独立编码 6 份后合并 codebook，主题：信任、可解释、边界争议、FOMO、与关键词过滤的对比；
- 纠正记录按「模型错在哪类边界」归类，作为 E8 边界实验的生态效度证据。

## 9. 伦理与数据管理

- 同意书见 docs/study/consent-zh.md；签字后才开始；被试可随时退出且不影响报酬；
- 只采集公开弹幕文本；不记录用户名、uid、IP；问卷不含直接身份信息（编号替代）；
- 录屏可选，只录屏幕；转写完成后删除原始音视频；
- 数据存 `data/study/raw/`（仓库内，已 gitignore）+ 加密备份；报告只呈现聚合结果与匿名引语；
- 实验前向被试说明：过滤器可能出错、会 fail-open（出错时弹幕照常显示）、可以随时关掉；
- 若课程/学院有伦理审查表，按模板提交；本方案不含欺骗、不含生理测量、不含弱势群体。

## 10. 排期（两周，D1 = 开始日）

| 日 | 任务 | 产出 |
|---|---|---|
| D1 | 定 6 支视频、跑 prepare_stimuli.py、核对密度/标签/延迟 | data/study/stimuli.json + report.json |
| D2 | 研究模式联调、问卷定稿、内部试跑 2 人（组内） | 修好的脚本 + 修订问卷 |
| D3 | 招募（30 人）、排期表、打印材料 | 被试名单 + 时段表 |
| D4–D6 | 正式实验，每天 5–6 人 | P01–P15 数据 |
| D7 | 休息 + 中期检查（日志完整性、条件是否错配） | 问题清单 |
| D8–D10 | 正式实验，每天 5–6 人 | P16–P24（+备选） |
| D11–D12 | S1 金标：现有 120 条单标注；能补第二人则双标 + α | data/study/gold.jsonl（+ gold_double.jsonl） |
| D13 | 跑 analyze.py、出表出图 | analysis.json + 结果表 |
| D14 | 写报告/PPT、补漏、备份归档 | 最终报告 |

## 11. 分工

| 角色 | 职责 | 人选 |
|---|---|---|
| 主试（1 人） | 读脚本、知情同意、访谈、计时 | 组员 A |
| 操作员（1 人） | 切条件、开 URL、启动/导出日志、设备故障处理 | 组员 B |
| 记录员（1 人，可兼） | 观察笔记、异常记录、问卷回收 | 组员 C |
| 数据（1 人，可兼） | 归档、备份、跑分析脚本 | 组员 B |
| 标注（2 人） | S1 双标 180 条 | 组员 A + C |

> 两人也能跑：主试兼操作员，记录用录音/自动日志替代；但访谈时最好有第二人在场。

## 12. 风险与预案

| 风险 | 影响 | 预案 |
|---|---|---|
| 服务或脚本崩溃 | 该条件无数据 | fail-open 不影响观看；记录异常，条件重跑或作缺失处理 |
| B 站改版/请求路径变化 | 拦截失效 | 实验前锁定脚本版本与浏览器；准备 2 台机器；当天先用测试视频验证 |
| 弹幕密度与预期不符 | 密度操纵失败 | prepare_stimuli 实测；不达标改选视频或按固定种子抽稀 |
| 被试看过视频 | 混淆 | 每块询问；≥4 支者排除主分析 |
| 疲劳/时长超限 | 数据质量下降 | 3 分钟休息；允许中途退出；单场 ≤85 min |
| 条件顺序效应 | 偏差 | 拉丁方 + 顺序作为协变量检验 |
| 关键词条件太弱/太强 | 对照不公平 | 预设 5 词并在 2 人试跑中检查实际屏蔽比例（目标 5%–20%） |
| 招募不足 | 功效不足 | 目标 24，最低 18；不足时降级为「C vs A」两条件分析并明确标注 |

## 13. 时间紧张时的最小可行版本（MVP）

若只剩 3–4 天：

- 条件减为 A、C 两个（去掉平台关键词条件），每条件 2 支视频（1 信息 1 娱乐），单场 45 分钟；
- N = 12–16，条件顺序 ABBA 平衡；
- 保留 discomfort / control / FOMO / NASA-TLX / 理解测验 5 项核心量表；
- 访谈缩为 3 问（最舒服的条件、一个错例、是否愿意用）；
- RQ2 用已有 29 条人工金标 + 新增 60 条双标，报告时说明样本量限制。

## 14. 开始前必须确认的 6 件事（当前状态）

| # | 事项 | 状态 |
|---|---|---|
| 1 | 截止日期与报告形式（论文 / 展示 / 两者） | 待定（用户确认） |
| 2 | 能招多少人、渠道、报酬 | 待定；本研究按 N=24（招 30）、单场 70–85 分钟、校内渠道写死；招募文案与排期模板见 docs/study/recruitment.md |
| 3 | 场地：实验室还是远程 | 待定；推荐实验室一人一间，远程无法控制设备与条件，不推荐 |
| 4 | B 站原生屏蔽词是否可用 | 需登录账号；操作步骤与降级方案写在 docs/study/examiner-checklist.md §2（B 条件平台屏蔽词 5 词） |
| 5 | 是否做 S1 双标 | 已做 120 条单标注金标（data/study/gold.jsonl，RQ2 已能出 per-tag P/R/F1）；第二名标注者待补，补法见 docs/study/gold-annotation.md §7 |
| 6 | 伦理审查表与同意书版本 | 待定；同意书见 docs/study/consent-zh.md，若学院有模板需替换 |

实验前自检：python3 scripts/study/precheck.py 应 7 项全 PASS（服务、材料、模型、报告、链接表、金标、脚本版本）；再跑 python3 scripts/study/freeze.py --verify，应输出 manifest OK（20 个文件 hash 一致）。manifest 记录的是「实际跑的材料」：stimuli、teacher 标签、金标、12 个模型、run-sheet、问卷模板与用户脚本版本，论文附录可引用其 sha256。

## 15. 相关文件

| 文件 | 用途 |
|---|---|
| docs/research-design.md | 研究目标、RQ/H、总体设计（本文件的上级文档） |
| docs/study/consent-zh.md | 知情同意书（可打印） |
| docs/study/questionnaire.md | 基线问卷 + 每块问卷 + 收尾问卷 |
| docs/study/session-script.md | 主试逐字稿与操作员检查表 |
| docs/study/protocol.md | 本文件 |
| scripts/study/prepare_stimuli.py | 材料校验与模型准备 |
| scripts/study/analyze.py | 数据分析 |
| scripts/study/freeze.py | 材料冻结：写/校验 data/study/manifest.json（论文附录的材料 hash 凭证） |
| scripts/study/label_stimuli.py | 每支刺激视频抽 200 条交 teacher 打标 |
| scripts/study/screen_candidates.py | 候选视频实测（时长/条数/密度/标签分布） |
| scripts/study/session_url.py | 生成每被试三个条件的实验 URL 与链接表 |
| scripts/study/demo_data.py | 合成数据，干跑分析链路（非真实结果） |
| docs/study/stimuli-selection.md | 6 支材料的选择过程与理由 |
| docs/study/session-kit.md | 主试工具包：链接、导出、命名、录入、故障处理 |
| docs/study/recruitment.md | 被试招募文案、筛选条件、排期模板、提醒话术、报酬与隐私 |
| docs/study/examiner-checklist.md | 实验前一天/每场/每天的主试与操作员清单 + 培训试跑 |
| docs/study/preflight.md | 18 块全流程演练：按真实链接跑 A/B/C，核对记录值与密度格（含原始导出 data/study/preflight/） |
| data/study/preflight/ | 演练的 18 个 session 导出与 summary.json（非被试数据） |
| docs/study/gold-annotation.md | S1 金标的抽样、分布、与 teacher 差异、双标扩展方法 |
| docs/study/results-template.md | 结果报告模板：analysis.json 字段到章节的映射 |
| data/study/ | stimuli.json、candidates.json、models/、teacher_sample.jsonl、report.json、run-sheet.csv、questionnaire.csv、gold.jsonl、gold_double.jsonl（可选）、raw/、analysis.json、dryrun/ |

