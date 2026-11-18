# 主试与操作员清单（S2 正式实验）

配合 docs/study/session-kit.md（链接、导出、命名、故障处理）与 docs/study/session-script.md（逐字稿）使用。

## 1. 实验前一天（D-1）

- [ ] 浏览器装好 Tampermonkey，导入固定版本的 dist/bili-danmaku-filter.user.js，面板标题下应显示 v0.4.1 或更高；实验期间不升级。
- [ ] 本地服务能在实验机上跑起来：在仓库根目录执行

    DM_LLM_ENV_FILE=/nonexistent/dmf-study.env nohup python3 -m danmaku_filter.cli serve --online-backend linear --models-dir data/study/models > /tmp/dmf-serve.log 2>&1 &

- [ ] 打开 http://127.0.0.1:8765/api/v1/health，确认 ok=true、online_backend=linear、models 至少 6、llm_pending=0。
- [ ] 用测试视频（不要用 6 支正式材料）验证 A/B/C 三个 URL 都能正常：面板出现、A/B 不干预、C 出现启动胶囊并过滤。
- [ ] 打印/准备：知情同意书 2 份/人、问卷（纸质或在线表格）、报酬记录表、run-sheet.csv。
- [ ] 跑 python3 scripts/study/precheck.py，7 项全 PASS（服务、材料、模型、报告、链接表、金标、脚本版本）；任一 FAIL 先修再约被试。
- [ ] 材料冻结校验：`python3 scripts/study/freeze.py --verify` 应输出 `manifest OK: 20 files match`；若 FAIL，先确认改动是有意的，再 `--write` 重新冻结，并把新的 manifest 时间/计数记进实验日志（data/study/manifest.json 是论文附录里「实际跑了什么材料」的凭证）。
  - [ ] 检查 run-sheet.csv 是 180 行（30 被试 × 6 块），URL 带 dmf_study / dmf_cond / dmf_density 三个参数（precheck 会校验）。
- [ ] 关闭系统通知、休眠、自动更新；插电源；浏览器缩放 100%。

## 2. 每场开始前（5 分钟）

- [ ] 服务仍在跑（health 再确认一次）；日志文件路径记在纸上。
- [ ] 浏览器新开窗口，确认没有残留旧标签页（旧的 localStorage 会污染条件）。
- [ ] 被试编号与顺序：从 run-sheet.csv 找到该被试的 6 个 URL，按 block_order 排好（顺序已在表里）。
- [ ] 知情同意：读 consent-zh.md，签字，说明「可能出错、失败会放行、可随时关闭」。
- [ ] 基线问卷（年龄/性别/专业/每周看 B 站时长/弹幕习惯）。

B 条件的平台关键词屏蔽（每场都要确认）：

- [ ] 打开任意 B 站视频，播放器内点「弹幕设置」→「屏蔽词」，粘贴 B 条件的 5 个词：剧透、前方高能、傻逼、打卡、？？？；截图存档（证明 B 条件是平台原生能力，而不是脚本在过滤）。
- [ ] A/C 块开始前，把这 5 个词从平台屏蔽词里删掉（或整体关闭），并截图确认。
- [ ] 若账号没有屏蔽词功能/改了入口，先在两支测试视频上验证「B 明显少于 A」再开始正式实验；无法实现就把 B 降级为记录-only 对照并在报告里说明。

## 3. 每块（视频 3–5 分钟）流程

1. 操作员按 run-sheet 打开该块 URL，等页面加载完（A/B 直接播；C 会先出现顶部进度胶囊，最长约 15 秒）。
2. 主试说固定开场句（见 session-script.md），确认声音/画面正常后开始播放。
3. 播放期间操作员不碰页面；主试观察并记录异常（卡顿、胶囊不消失、弹幕明显异常）。
4. 视频结束（或播到 95%）后暂停，被试填该块问卷（不适感 5 题、共看 3 题、控制感 3 题、FOMO 1 题、NASA-TLX 6 题、理解 5 题 + 自由回忆）。
5. 操作员导出日志：在页面控制台执行 window.__dmfFilter.study.download()，文件改名 dmf-study-<study_id>-<bvid>.json，放进当天文件夹。
6. 核对计数（研究模式下插件面板默认隐藏，需要目视检查时在控制台执行 `window.__dmfFilter.study.showUi()`，看完 `window.__dmfFilter.study.hideUi()`）。数字更稳的读法是直接看导出对象：`window.__dmfFilter.study.export().counts`：
   - A/B：`hidden` 与 `collapsed` 必须是 0（只记录不改流），原型策略本来会隐藏多少看 `would_hidden` / `would_collapsed`；
   - C：`hidden`/`collapsed` 应为正数，且 `shown` 明显少于同视频的 A/B；
   - 低密度格：`thinned` 约等于 段内条数 − 30 条/分 × 时长（例如 290 秒的视频整块约抽掉 1,450 条）。

   B 块额外确认平台屏蔽词已开（见 §2），并在 notes 里记「B 的 5 个词是否真的出现过」。整块可见量级：

| 视频 | A/B 实际可见（整块） | C 实际可见（整块） | 说明 |
|---|---|---|---|
| BV1Ho4y1D7Wz 铋晶体（186 s，high） | 约 990–1,200 条 | 约 500–700 条（原型隐藏约 40%） | 高密度格，不抽稀 |
| BV18E1NByEYv 游戏（290 s，low） | 约 145 条 | 约 100 条 | 抽稀到 30 条/分 |
| BV1VMRKBCEDp 睡眠（214 s，low） | 约 107 条 | 约 85 条 | 同上 |
| BV1X7zGY4E45 辩论（194 s，high） | 约 1,200 条 | 约 670 条（攻击/冲突多） | 不抽稀 |
| BV1Z4RHYKEcB 金融（281 s，high） | 约 725 条 | 约 650 条（过滤很轻） | 不抽稀 |
| BV1CzW26vEXz 弃坑（271 s，low） | 约 135 条 | 约 60–90 条 | 抽稀到 30 条/分 |

（C 的数字来自 docs/study/preflight.md 的整池演练；A/B 的数字来自同一次演练的 applied counts。单段面板数字会比整块小，判断块是否正常以「A/B 的 hidden=0、C 的 hidden>0 且 shown 更少」为准。）

7. 块间休息 1 分钟；每 3 块休息 3–5 分钟（协议 §4）。

## 4. 每场结束后（10 分钟）

- [ ] 收齐 6 个 JSON（每块一个文件，不是每人一个），检查文件名含 study_id 与 bvid。
- [ ] 问卷录入：每块一行，participant/condition/video/genre/density/block_order/familiar/各量表/notes；condition 只写 A/B/C。
- [ ] 异常记录写进当天 notes：条件错配、崩溃、重跑、被试看过视频。
- [ ] 数据双备份：当天文件夹复制到移动盘/网盘，并保留在 data/study/raw/。
- [ ] 结算报酬，感谢被试。

## 5. 每天收尾（10 分钟）

- [ ] 统计当天完成人数与块数（目标 5–6 人/天）；缺块立即补排。
- [ ] 检查 6 个 JSON 的 counts 是否有 0 或明显异常（如 shown=0 全段），有则标记该块重跑。
- [ ] 把服务日志复制一份到当天文件夹（/tmp/dmf-serve.log），便于回溯。
- [ ] 更新进度表：已完成的 participant × condition 矩阵。

## 6. 故障处理（先记录，再处理）

| 现象 | 先做 | 再做 |
|---|---|---|
| 面板不出现 | 刷新一次页面 | 检查 Tampermonkey 是否启用、脚本版本；改用备用机器 |
| 角标显示「已暂停」 | 点控制台按钮开启过滤 | 若是 A/B 条件则正常（记录-only 不显示过滤效果） |
| C 条件胶囊超过 20 秒 | 记录时间与视频 | 继续播放不影响数据；把异常写进 notes |
| 服务连不上（面板显示未连接） | 重启服务并确认 health | 该块标记为故障，条件重跑或按缺失处理 |
| 导出为空/报错 | 控制台执行 __dmfFilter.study.export() 看返回 | 保存页面为 HTML 或截图为证，记 notes |
| 条件错配（打开错 URL） | 立即停止该块 | 记录并在分析时排除该块（不猜条件） |
| 被试中途退出 | 停止实验 | 记录退出时间点，问卷与日志保留但不计入主分析 |

## 7. 主试培训与试跑（正式实验前必须做）

- [ ] 两名组员各完整跑一遍 A/B/C（用测试视频，不是 6 支材料），能独立完成：开 URL、导出、改名、录问卷。
- [ ] 一起看一遍 session-script.md，统一措辞（尤其：不评价过滤好坏、不引导回答、出错时如实说明）。
- [ ] 做一次「双人同时记录」练习：一人主试一人操作，交换角色各一次。
- [ ] 记录试跑发现的问题并改 session-kit；试跑数据不进 analysis。

## 8. 伦理提醒（每次都要）

- 只采公开弹幕文本，不记录用户名/uid/IP；问卷用编号。
- 录屏可选，只录屏幕；转写完成即删原始音视频。
- 被试可随时退出且不影响报酬；出错不隐瞒，按 fail-open 说明。
- 数据只存仓库 data/study/raw/ 与加密备份，不公开发布原始日志。