# S2 主试工具包（session kit）

这份文档给现场主试/操作员用：怎么生成链接、怎么开条件、怎么导出数据、文件怎么命名、问卷怎么录。研究设计与统计口径见 `docs/study/protocol.md`，逐字稿见 `docs/study/session-script.md`。

配套文档：`docs/study/examiner-checklist.md`（逐项勾选清单与培训试跑）、`docs/study/gold-annotation.md`（S1 金标与双标方法）、`docs/study/results-template.md`（结果报告模板）。

## 1. 开一场实验前的 5 分钟准备

1. 本地策略服务必须在跑（脚本在 study 模式仍会向它要标签）：

~~~bash
cd /home/alphayang/bili-tagging
DM_LLM_ENV_FILE=/nonexistent/dmf-study.env nohup python3 -m danmaku_filter.cli serve --online-backend linear > /tmp/dmf-serve.log 2>&1 &
curl -s http://127.0.0.1:8765/api/v1/health
~~~

   health 里 `online_backend` 应为 `linear`、`llm_pending` 为 0（实验期间不要开 teacher 队列，否则条件不可复现）。

   也可以直接跑一键自检：

~~~bash
python3 scripts/study/precheck.py
~~~

   7 项全 PASS 才能开始（服务、材料、模型、报告、链接表、金标、脚本版本）；FAIL 项按输出提示修。

   再跑一次材料冻结校验（论文附录凭证）：

~~~bash
python3 scripts/study/freeze.py --verify
~~~

   输出 `manifest OK: 20 files match` 才继续；FAIL 说明材料被改过，确认是有意修改后 `--write` 重新冻结并记录新时间。

2. 确认浏览器已装最新用户脚本（`extension/dist/bili-danmaku-filter.user.js`，脚本版本 ≥ 0.4.0）。

3. 打开本次被试的链接表：`data/study/run-sheet.csv`（30 被试 × 6 块 = 180 条 URL）。

4. 每场实验用一个新的浏览器 profile（或清 localStorage 后重开），避免上一个被试的偏好残留。

## 2. 生成/重生成链接表

~~~bash
# 单个被试的 3 个条件 × 2 块 = 6 条链接
python3 scripts/study/session_url.py --participant 7

# 只要某个条件
python3 scripts/study/session_url.py --participant 7 --condition C

# 全部被试（默认 30 人）的链接表 → data/study/run-sheet.csv
python3 scripts/study/session_url.py --sheet --participants 30
~~~

链接形如：

~~~
https://www.bilibili.com/video/BV1Ho4y1D7Wz/?dmf_study=P07-A&dmf_cond=A&dmf_density=high
~~~

`dmf_study` = 被试编号-条件，`dmf_cond` = A/B/C，`dmf_density` = high/low（缺省 high）。被试编号决定拉丁方行（编号 mod 6），**不要**手改编号，否则条件顺序与视频对分配会错。

## 2.5 每块预期看到多少弹幕（核对用）

| 视频 | 时长 | 密度格 | 预期可见（约） |
|---|---|---|---|
| BV1Ho4y1D7Wz 铋晶体 | 186 s | 高 | 全部（约 1,100–1,200 条，实测峰值 739/min） |
| BV18E1NByEYv 游戏 | 290 s | 低 | 约 145 条（30/min 抽稀） |
| BV1VMRKBCEDp 睡眠 | 214 s | 低 | 约 107 条 |
| BV1X7zGY4E45 辩论 | 194 s | 高 | 全部（约 1,200 条，峰值 427/min） |
| BV1Z4RHYKEcB 金融 | 281 s | 高 | 全部（约 725 条） |
| BV1CzW26vEXz 弃坑推文 | 271 s | 低 | 约 135 条 |

> 抽稀按 10 秒窗口、每窗 5 条、稳定 hash 选留，三个条件完全一致。C 条件启动时会有整池处理胶囊，最长等 15 s（研究模式放宽了等待），属正常；A/B 不应出现胶囊。

## 2.6 每块结束：导出与命名

1. 被试看完一块后，在播放页打开控制台（F12）执行：

~~~js
window.__dmfFilter.study.download();   // 浏览器下载 dmf-study-<study_id>.json
~~~

2. 立刻把文件改名为 `dmf-study-<study_id>-<bvid>.json`（例如 `dmf-study-P01-A-BV1Ho4y1D7Wz.json`），否则同一被试的 6 个文件会互相覆盖。
3. 主试在运行表里勾掉该块，并记录：开始/结束时间、异常（崩溃、掉线、弹幕关过、快进过）。
4. 全部被试结束后，把 30 × 6 = 180 个 JSON 放进 `data/study/raw/`，问卷按 `data/study/questionnaire.csv` 表头录入，然后跑 `python3 scripts/study/analyze.py`（见 §5）。

> 导出字段核对：`study_id`、`condition`（`all_on`/`keyword`/`prototype`）、`condition_label`（A/B/C）、`counts`（含 `thinned`）、`events[]`、`script_version`（应为 0.4.x）、`prefs`。缺 `condition_label` 的旧导出也能分析，脚本会从 `study_id` 后缀取字母。

> 干跑样例与已知坑见 `data/study/dryrun/README.md`（合成数据，只验证管线，不是结果）。

## 3. 三个条件在屏幕上是什么样

| 条件 | 脚本行为 | 被试看到 |
|---|---|---|
| A（全开） | 只记录，不改渲染；按密度格抽稀 | 原生弹幕，无面板 |
| B（平台关键词） | 只记录，不改渲染；按密度格抽稀 | 原生弹幕；B 的"平台过滤"由实验者事先在 B 站账号里设置 5 个屏蔽词实现 |
| C（原型） | 预置策略并真正过滤；按密度格抽稀 | 面板隐藏；按策略隐藏/折叠，折叠条目不渲染 |

注意：B 条件依赖 B 站账号的"屏蔽词"设置，必须在实验前统一设好（5 个词见 protocol §2.1），且实验期间不要改。
> 密度格：每个视频块在 URL 里带 dmf_density。high = 原样（150–750 条/分），low = 抽稀到 30 条/分（每 10 秒窗口 5 条）。三个条件都会执行这道操纵，它是实验自变量，不是过滤策略。


## 4. 一块视频的流程（约 8–10 分钟）

1. 主试念转场语（见 session-script.md），被试打开该块链接。
2. 等页面加载完、弹幕开始滚动后开始计时；C 条件会有 1–2 秒的整池处理胶囊，属正常。
3. 完整播完（或按脚本停在指定位置），被试填写该块的在线问卷。
4. 关掉标签页前，操作员导出埋点（见下）。

## 5. 导出埋点数据

每块结束、关标签页之前，在页面 console 里执行：

~~~js
__dmfFilter.study.download()   // 下载 dmf-study-P07-A.json
~~~

或先取内容再自己保存：

~~~js
JSON.stringify(__dmfFilter.study.export())
~~~

文件命名沿用脚本给的名字：`dmf-study-<study_id>.json`，例如 `dmf-study-P07-A.json`。每块一个文件，放到：

~~~
data/study/raw/dmf-study-P07-A.json
data/study/raw/dmf-study-P07-B.json
data/study/raw/dmf-study-P07-C.json
~~~

（同一被试三个条件一共 3 个文件；文件内部 `events` 里带 `bvid`，所以一块视频对应一段事件，不必拆文件。）

导出后立刻检查三件事：

1. `study_id`、`participant`、`condition` 与被试编号一致；
2. `counts.shown/hidden/collapsed` 不是全 0（C 条件必须 > 0 的 hidden 或 collapsed，否则说明脚本没生效）；
3. `events` 里有若干 `type=segment` 的记录。

## 6. 问卷录入

问卷用在线表单，导出 CSV 后合并进 `data/study/questionnaire.csv`。列名固定（见 protocol §7.3）：

~~~
participant,condition,video,genre,density,block_order,familiar,discomfort_1..5,coview_1..3,control_1..3,fomo,nasa_1..6,comp_1..5,recall,explain_1..2,overall_1..2,notes
~~~

`participant` 用 `P01…P30`，`condition` 用 `A/B/C`，`video` 用 bvid，`genre` 用 `information/entertainment`，`density` 用 `high/low`，`block_order` 是该块在本场中的序号（1–6）。

## 7. 收尾

~~~bash
# 分析（会话 JSON + 问卷 CSV）
python3 scripts/study/analyze.py \
  --raw-dir data/study/raw \
  --questionnaire data/study/questionnaire.csv \
  --out data/study/analysis.json \
  --markdown data/study/analysis.md \
  --gold data/study/gold.jsonl \
  --models-dir data/study/models
~~~

输出包含：清洗后的样本量、按条件的描述统计、planned contrasts（C vs A、C vs B、B vs A，Wilcoxon + Holm 校正）、混合线性模型（若装了 statsmodels）、RQ1（genres × density，仅 A 条件）与 RQ2（标签质量）表。

RQ2 的 per-tag 指标需要 `--gold`（当前为 120 条单标注，见 gold-annotation.md）；若能补第二人，另加 `--gold-double data/study/gold_double.jsonl` 输出 Krippendorff α。报告按 `docs/study/results-template.md` 填。

## 8. 常见故障

| 现象 | 处理 |
|---|---|
| 页面弹幕完全没变（C 条件） | 看 console 有无 `[dmf]` 日志；确认 8765 health 正常；确认脚本版本 ≥ 0.4.0；刷新页面 |
| 面板/角标不应出现却出现了 | study 模式默认隐藏 UI；若出现说明 study 配置没被解析，检查 URL 参数拼写 |
| 导出为空或 counts 全 0 | 该块没有触发拦截（可能页面未真正播放）；重放该块 |
| 同一被试第二块计数被累加 | 文件按 study_id 分开，正常；若同一条件重复导出，覆盖旧文件即可 |