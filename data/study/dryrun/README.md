# S2 分析管线 dry-run（合成数据，不是真人结果）

这个目录只验证一件事：**真实页面导出的 session JSON + 问卷 CSV 能被 `scripts/study/analyze.py` 正常读出、清洗、汇总、跑计划对比**。里面的问卷回答是脚本随机生成的，session 的 `started_at/ended_at` 被拉长到视频时长（否则清洗会按「停留 <60%」整批排除），**不能当成实验结果引用**。

## 数据来源

| 文件 | 真实的部分 | 合成的部分 |
|---|---|---|
| `raw/dmf-study-P01-<cond>-<bvid>.json` | 6 支刺激视频的真实播放页导出：`counts`（shown/hidden/collapsed/thinned）、`events`（segment/bvid/latency/online/applied）、`prefs`、`script_version`、`ua` | `started_at`/`ended_at`（按视频时长拉伸，模拟完整观看） |
| `questionnaire.csv` | 列结构与 `docs/study/questionnaire.md` 一致 | 全部回答（随机 1–5 / 0–1），`notes` 写明 synthetic |
| `analysis.json` / `analysis.md` | 管线生成的清洗、描述统计、planned contrasts、RQ1/RQ2 表 | 数值本身无意义 |

## 怎么复跑

~~~bash
# 1. 服务（无 teacher，避免研究条件被在线升级打乱）
DM_LLM_ENV_FILE=/nonexistent/dmf-study.env \
  python3 -m danmaku_filter.cli serve --online-backend linear --models-dir data/study/models

# 2. 打开 run-sheet 里对应 URL（A/B/C + 密度格），每块看完后用控制台导出
#    window.__dmfFilter.study.download()  →  dmf-study-P01-A-BV1Ho4y1D7Wz.json

# 3. 分析与报告
python3 scripts/study/analyze.py \
  --raw-dir data/study/dryrun/raw \
  --questionnaire data/study/dryrun/questionnaire.csv \
  --out data/study/dryrun/analysis.json \
  --markdown data/study/dryrun/analysis.md \
  --bootstrap 200
~~~

本轮（2026-11-18）干跑结果：`analysed 6 blocks / 1 participants`，A/B/C 各 2 块，清洗无排除，RQ1 的 genre × density 表与 RQ2 的延迟/行为表都能正常产出；因为只有一名「被试」，paired Wilcoxon 全部落到 `too few complete pairs`，这在正式实验（N=24）里会消失。

## C 条件在 6 支材料上的实际过滤量（真实页面，2026-11-18）

`c-condition-check.json` 是 6 支视频在 C 条件（原型策略 + 各密度格）下的一次实测：整池条数、策略给出的 show/hide/collapse、以及整池加载耗时。

| 视频 | 密度格 | 整池条数 | show | hide | collapse | 被过滤占比 | 加载耗时 |
|---|---|---|---|---|---|---|---|
| BV1Ho4y1D7Wz 铋晶体 | high | 1200 | 581 | 28 | 591 | 51.6% | 421 ms |
| BV18E1NByEYv 游戏 | low | 1200 | 706 | 56 | 438 | 41.2% | 1,875 ms |
| BV1VMRKBCEDp 睡眠 | low | 1172 | 901 | 57 | 214 | 23.1% | 475 ms |
| BV1X7zGY4E45 辩论 | high | 1200 | 671 | 251 | 278 | 44.1% | 3,205 ms |
| BV1Z4RHYKEcB 金融 | high | 725 | 652 | 71 | 2 | 10.1% | 214 ms |
| BV1CzW26vEXz 弃坑推文 | low | 1119 | 790 | 93 | 236 | 29.4% | 300 ms |

读法：collapse 里绝大部分是密度抽稀（低密度格每窗只留 5 条）；hide 是 attack/conflict/inflammatory/irrelevant_repetition 的策略命中。资金/时事类（金融、睡眠）hide 少、娱乐类（辩论、游戏）hide 多，符合材料设计。加载 0.2–3.2 s 都落在整池等待的 15 s 预算内。

## 干跑发现并修掉的问题

1. **条件标识不一致**：导出写的是 `all_on/keyword/prototype`，协议与问卷用 `A/B/C`，`analyze.py` 以前只认字母 → 6 个 session 全被跳过。现在导出同时写 `condition`（规范 id）与 `condition_label`（A/B/C），分析脚本两种都接受（`CONDITION_ALIASES`，含 `P01-A` 这种从 study_id 推字母的旧日志）。
2. **延迟统计全是 0**：决策请求走本地服务、同段还会命中缓存，`Date.now()` 分辨率下常见 0–2 ms；这是真实值，但重复记录时后一次（缓存命中）会把前一次的延迟覆盖成 0 → `recordSegment` 合并时对 `latency_ms` 也取最大值。
3. **清洗规则比想象中严格**：`familiar` 任一非零即算「看过」，6 块全填 1 就会触发「熟悉 ≥4 支」排除；正式实验请按问卷说明填 0/1（看过/没看过），不要填 1–5 量表。

## 正式实验要做的

- 用 `data/study/run-sheet.csv`（30 被试 × 6 块，含 `dmf_density`）逐个跑；每块结束导出 JSON，命名 `dmf-study-<study_id>-<bvid>.json` 放进 `data/study/raw/`。
- 问卷按 `data/study/questionnaire.csv` 表头录入，一人一行一块。
- 跑 `analyze.py --raw-dir data/study/raw --questionnaire data/study/questionnaire.csv`，输出写 `data/study/analysis.json/.md`。
- 需要 S1 金标时用 `--gold data/study/gold.jsonl --gold-double data/study/gold_double.jsonl`。