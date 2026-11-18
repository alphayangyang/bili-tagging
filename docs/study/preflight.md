# Study preflight：18 块全流程演练（2026-11-18）

在真人被试到位之前，用 ego-browser 按 data/study/run-sheet.csv 的真实链接把 P01/P02/P03 的 18 块完整跑了一遍：每个 URL 都走「注入用户脚本 v0.4.1 → 打开视频 → 播放 12 秒 → 导出 session JSON」，检查研究模式是否按条件生效、密度格是否真的抽稀、整池预取是否在预算内。

这不是被试数据，不参与分析；用途是证明「主试照清单操作一遍不会踩坑」。

## 1. 环境与冻结状态

| 项 | 值 |
|---|---|
| 用户脚本 | extension/dist/bili-danmaku-filter.user.js，v0.4.1，sha256 见 data/study/manifest.json |
| 本地服务 | http://127.0.0.1:8765，online_backend=linear，models=12，llm_pending=0 |
| 材料 | data/study/stimuli.json 6 支、每支一份 data/study/models/<bvid>.json |
| 链接 | data/study/run-sheet.csv 中 P01/P02/P03 的 18 行（每人 A/B/C 各 2 块） |
| 冻结 | python3 scripts/study/freeze.py --verify → manifest OK: 20 files match |
| 原始导出 | data/study/preflight/dmf-study-*.json（18 个）与 summary.json |
| 复跑 | 脚本逻辑与 docs/study/examiner-checklist.md 每块流程一致；浏览器环境为 ego-browser 1600×1000 |

## 2. 结果

A = all_on、B = keyword（平台屏蔽词）、C = prototype（本系统）。密度格 high 原样、low 抽稀到 30 条/分。
「记录值」是被脚本记下的动作分布（A/B 只记录不改流，C 真的按此过滤）。

| 被试 | 条件 | 视频 | 密度格 | 研究模式 | 只记录 | 整池 | 整池耗时 | 记录 shown / hidden / collapsed / thinned |
|---|---|---|---|---|---|---|---|---|
| P01 | A | BV1Ho4y1D7Wz 铋晶体 | high | 开 | 是 | — | — | 972 / 19 / 0 / 0 |
| P01 | A | BV18E1NByEYv 游戏 | low | 开 | 是 | — | — | 58 / 58 / 0 / 1459 |
| P01 | B | BV1VMRKBCEDp 睡眠 | low | 开 | 是 | — | — | 57 / 47 / 0 / 704 |
| P01 | B | BV1X7zGY4E45 辩论 | high | 开 | 是 | — | — | 2180 / 652 / 0 / 0 |
| P01 | C | BV1Z4RHYKEcB 金融 | high | 开 | 否 | 是 | 316 ms | 303 / 42 / 0 / 0 |
| P01 | C | BV1CzW26vEXz 弃坑 | low | 开 | 否 | 是 | 320 ms | 43 / 71 / 170 / 568 |
| P02 | A | BV1Ho4y1D7Wz 铋晶体 | high | 开 | 是 | — | — | 972 / 19 / 0 / 0 |
| P02 | A | BV18E1NByEYv 游戏 | low | 开 | 是 | — | — | 58 / 58 / 0 / 1459 |
| P02 | C | BV1VMRKBCEDp 睡眠 | low | 开 | 否 | 是 | 200 ms | 49 / 47 / 0 / 704 |
| P02 | C | BV1X7zGY4E45 辩论 | high | 开 | 否 | 是 | 250 ms | 1919 / 652 / 0 / 0 |
| P02 | B | BV1Z4RHYKEcB 金融 | high | 开 | 是 | — | — | 303 / 42 / 0 / 0 |
| P02 | B | BV1CzW26vEXz 弃坑 | low | 开 | 是 | — | — | 54 / 71 / 0 / 568 |
| P03 | B | BV1Ho4y1D7Wz 铋晶体 | high | 开 | 是 | — | — | 972 / 19 / 0 / 0 |
| P03 | B | BV18E1NByEYv 游戏 | low | 开 | 是 | — | — | 58 / 58 / 0 / 1459 |
| P03 | A | BV1VMRKBCEDp 睡眠 | low | 开 | 是 | — | — | 57 / 47 / 0 / 704 |
| P03 | A | BV1X7zGY4E45 辩论 | high | 开 | 是 | — | — | 2180 / 652 / 0 / 0 |
| P03 | C | BV1Z4RHYKEcB 金融 | high | 开 | 否 | 是 | 302 ms | 303 / 42 / 0 / 0 |
| P03 | C | BV1CzW26vEXz 弃坑 | low | 开 | 否 | 是 | 212 ms | 43 / 71 / 170 / 568 |

（CzW 那行的 collapsed 170 是被原型策略折叠的条数；低密度格里 thinned 与 collapsed 同时出现，前者是实验操纵，后者是过滤策略。）

## 3. 三条可核对的一致性

1. **同视频同密度格，记录值跨条件完全一致。** Ho4 high 在 A/B 都是 972/19；X7z high 在 A/B 都是 2180/652；Z4 high 在 B 是 303/42，C 也是 303/42。说明「记录什么」只取决于材料与策略，跟条件无关 —— 被试看到的差异来自「是否真的应用」。
2. **C 的 shown 只会少、hidden 不会多。** X7z high：A/B 记录 2180 显示、652 隐藏；C 实际显示 1919，隐藏仍是 652，差额 261 是 prototype 额外隐藏/折叠的部分（面板折叠列表可找回）。这正是 C vs A、C vs B 要测的操纵。
3. **低密度格在三条件都真的变稀。** 18E 抽掉 1459 条后剩 58 条可见（290 秒 ≈ 30 条/分）；VMR 剩 57 条（214 秒）；CzW 剩 43–54 条（271 秒）。同一视频在 A/B/C 的可见量同量级，密度格不是 C 独有的效果。

## 4. 整池耗时与等待预算

C 条件六块的整池加载 200–320 ms（远低于脚本的 15 秒研究上限 POOL_WAIT_MS），所以正式实验里不应出现「等待胶囊」超过 1 秒以上；若主试看到胶囊长期停留，按故障表处理（检查服务、网络、模型文件）。

## 5. 服务挂掉时会不会黑屏（fail-open 演练）

把本地服务直接 kill 掉，再跑 P01 的 4 块（A 高/低、C 高/低），导出存在 data/study/preflight/failopen/。

| 块 | 服务 | 研究模式 | 只记录 | 整池 | 记录 shown / hidden / collapsed / thinned | 结论 |
|---|---|---|---|---|---|---|
| P01-A Ho4 high | 挂 | 开 | 是 | — | 991 / 0 / 0 / 0 | 全部记录为显示 |
| P01-A 18E low | 挂 | 开 | 是 | — | 60 / 0 / 0 / 1459 | 抽稀照常生效 |
| P01-C Z4 high | 挂 | 开 | 否 | 是（265 ms） | 345 / 0 / 0 / 0 | 整池 725 条全部放行 |
| P01-C CzW low | 挂 | 开 | 否 | 是（159 ms） | 60 / 0 / 0 / 568 | 整池 1119 条全部放行 + 抽稀 |

结论：服务不可用时脚本不会黑屏也不会误杀 —— 拿不到标签就按「显示」处理，播放器照常跑；密度操纵（客户端计算）仍然生效。对照第 2 节（服务正常）同一块 C Z4 是 303/42、CzW 是 43/71/170，可见差别只来自「有没有过滤」，不是「能不能播」。

主试注意：如果服务没起来，C 条件的记录值会接近 A（全显示），此时该块数据要作废重跑；检查方法是看 C 块启动时是否出现过整池胶囊，以及块末分组统计里 hidden/collapsed 是否为 0。

## 6. 演练中发现并修掉的问题

- **一次瞬时失败**：P02-A 打开 Ho4 时 page.evaluate 超时（页面脚本忙，15 秒），该块没有导出。重跑同一链接即 972/19，与 P01-A 完全一致 —— 属于浏览器端的偶发超时，不是产品缺陷。主试清单里对应建议：某块如果 20 秒内没出现角标，重开该块（记录一次重试），不要继续作答。

## 7. 结论

- 18/18 块：研究模式开启、条件解析正确（A→all_on、B→keyword、C→prototype）、A/B 只记录、C 真过滤并走整池、密度格三条件一致生效。
- 服务挂掉时 4 块演练全部 fail-open（全部显示），密度操纵仍生效，不会黑屏或误杀；C 块在服务挂掉时记录值接近 A，可据此判废重跑。
- 材料、模型、链接、脚本、服务都处于「照清单就能开跑」的状态；冻结 manifest 可证明正式实验用的就是这一版材料。
- 仍未验证的只剩真人流程本身：知情同意、问卷作答、报酬与排期 —— 见 docs/study/examiner-checklist.md 与 docs/study/session-kit.md。

## 8. 复跑方式

~~~bash
# 1) 服务与材料
DM_LLM_ENV_FILE=/nonexistent/dmf-study.env nohup python3 -m danmaku_filter.cli serve --online-backend linear --models-dir data/study/models > /tmp/dmf-serve.log 2>&1 &
python3 scripts/study/precheck.py
python3 scripts/study/freeze.py --verify

# 2) 浏览器演练（ego-browser，需本机 Windows 浏览器）
#    逐块打开 run-sheet.csv 的 URL，播放 12 秒后导出 session JSON；
#    判定标准见第 3 节的三条一致性。
~~~
