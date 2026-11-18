# S2 刺激视频筛选记录

日期：2026-11-18。目标：为 S2 受控实验选出 3 支信息型 + 3 支娱乐型视频，3–5 分钟、弹幕量足够、密度可覆盖高/低两个实验格，娱乐型素材要能支撑 C 条件（原型过滤）的对照。

## 1. 为什么不能直接用 API 搜

- `api.bilibili.com/x/web-interface/search/type` 直接请求返回 HTTP 412（风控），`ranking/v2` 返回 code -352。候选只能通过 ego-browser 的搜索页（登录态）采集。
- 采集脚本 `/tmp/ego-search-stimuli*.mjs`：按关键词搜索 → 抓 `a[href*='/video/BV']` 的 bvid/标题/时长/播放/弹幕数 → 写 `stimuli-cards*.json`。共 4 批、25 个关键词、约 1,300 张卡片。
- 筛选脚本 `scripts/study/screen_candidates.py`：对候选调 `fetch.get_video_info` + `fetch.fetch_danmaku`（legacy list.so），实测时长、条数、每分钟密度、峰值密度和 rules 标签分布，写 `data/study/candidates.json`。

## 2. 候选实测（节选）

| bvid | 标题 | 时长 | 条数 | 条/分 | 峰值/分 | rules attack/conflict/spoiler |
|---|---|---|---|---|---|---|
| BV1Ho4y1D7Wz | 三分钟教你做一个酷炫的铋晶体 | 186 s | 1200 | 387.1 | 739 | 0/0/1 |
| BV1VMRKBCEDp | 3分钟解释各种睡眠时长的后果 | 214 s | 1172 | 328.6 | 426 | 3/13/7 |
| BV1rAWc6YEFb | 3分钟看懂2026诺贝尔化学奖 | 222 s | 139 | 37.6 | 76 | 0/0/0 |
| BV1Z4RHYKEcB | 从零开始建立"金融世界观" | 281 s | 725 | 154.8 | 226 | 0/2/0 |
| BV18E1NByEYv | 哪个游戏的网络环境最恶臭？ | 290 s | 1200 | 248.3 | 631 | 15/1/0 |
| BV1X7zGY4E45 | 自由式辩论赛场：吃坚果好还是不好 | 194 s | 1200 | 371.1 | 427 | 1/33/1 |
| BV1CzW26vEXz | 日网友弃坑推文引来千万围观 | 271 s | 1119 | 247.7 | 407 | 2/6/7 |
| BV1Sptv6HEVY | 暴雨告发（甄嬛传解说） | 206 s | 1200 | 349.5 | 533 | 1/1/0 |
| BV1ko4y1t7QN | 小鬼九大逆天名场面 | 249 s | 1200 | 289.2 | 407 | 5/3/2 |
| BV1o6421T7aM | 三分钟解构《热辣滚烫》剧情 | 211 s | 142 | 40.4 | 120 | 0/0/0 |
| BV1CY4b64E6E | 《嫁金钗》大结局吐槽 | 227 s | 296 | 78.2 | 115 | 1/2/0 |
| BV1FngP6rExg | 锐评高开低走的百合作品 | 262 s | 358 | 82.0 | 129 | 1/0/0 |

观察：

1. `list.so` 单次最多返回 1,200 条，所以"条数"列在热门视频上都是 1200，真实池更大；材料要求 ≥300 条即可满足。
2. 剧透类（影评/大结局吐槽）单支视频的弹幕量普遍 <300 或峰值 <150，不能同时满足条数与高密度条件。
3. 娱乐型里 attack/conflict 的供给充足（游戏对喷、辩论），spoiler 稀缺——单支视频几乎不可能三类都 ≥5。

## 3. 判定口径的一次修正

原 `assess_stimulus` 要求**每支**娱乐视频里 attack/conflict/spoiler 各 ≥5 条。真实数据表明这个门槛无法满足，而且不符合设计：被试看的是全部三支娱乐视频，C 条件的对照应在**材料集合**层面成立。

- 单支视频的社交标签不足 → 降级为 warning；
- `summarise()` 新增材料级检查：三支娱乐视频合计 attack / conflict / spoiler 各 ≥5 条，否则 `ready=false`；
- 报告 `summary.social` 里给出合计值，论文里按这个口径描述材料。

## 4. 最终 6 支（`data/study/stimuli.json`）

| 对 | bvid | 类型 | 密度格 | 标签 | 实测 | 选它的理由 |
|---|---|---|---|---|---|---|
| P1 | BV1Ho4y1D7Wz | 信息 | 高 | science/chemistry | 186 s / 1200 / 387 每分 | 高密度信息型，事实点密集，适合理解测验 |
| P1 | BV18E1NByEYv | 娱乐 | 低 | gaming/trash-talk | 290 s / 1200 / 248 每分 | 游戏对喷，attack 供给充足 |
| P2 | BV1VMRKBCEDp | 信息 | 低 | science/health | 214 s / 1172 / 329 每分 | 健康科普，同样的知识点结构 |
| P2 | BV1X7zGY4E45 | 娱乐 | 高 | debate/conflict | 194 s / 1200 / 371 每分 | 辩论冲突，conflict 33 条 |
| P3 | BV1Z4RHYKEcB | 信息 | 高 | finance/explainer | 281 s / 725 / 155 每分 | 财经讲解，密度适中，事实点可出题 |
| P3 | BV1CzW26vEXz | 娱乐 | 低 | anime/commentary | 271 s / 1119 / 248 每分 | 动漫吐槽，spoiler 7 + conflict 6，补足材料级剧透供给 |

合计（rules 口径）：attack 18、conflict 40、spoiler 8，三类都 ≥5，满足材料级要求；换成 teacher 标签后以 `data/study/report.json` 的 `summary.social` 为准。

密度格按对分配（每对一支高、一支低）：P1 = 信息高 / 娱乐低；P2 = 信息低 / 娱乐高；P3 = 信息高 / 娱乐低。所有视频峰值密度都 ≥150 条/分，低密度格由服务端/脚本按固定规则抽稀，不依赖视频本身稀疏。

## 5. 老师标注与模型

- 每支视频用 `scripts/study/label_stimuli.py` 按时间分层抽 200 条，交 deepseek-flash teacher 打 relevance 向量，写 `data/study/teacher_sample.jsonl`；
- `scripts/study/prepare_stimuli.py --dataset data/study/teacher_sample.jsonl --retrain` 训练每视频线性模型（`data/study/models/<bvid>.json`）并生成 `data/study/report.json`；
- `report.json` 里 `status` 必须全部 `ok`、`summary.ready` 为 true，材料才算定稿。
