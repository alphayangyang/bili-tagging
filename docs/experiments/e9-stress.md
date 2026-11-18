# E9 压测：151 万弹幕视频、单段 5,886 条

日期：2026-11-06　执行：lead　状态：完成并已修复压测暴露的问题

## 1. 目标

验证整条链路（B 站播放器 → 油猴脚本拦截 → 本地策略服务 → 线性打标 → 回写弹幕流 → DOM 兜底）在极端密度下的可用性，回答三个问题：

1. 单段 5,886 条弹幕时，服务端决策与客户端改写是否还在可接受延迟内；
2. 打开一个密集视频，过滤是否真的让屏幕变干净（而不是只改计数）；
3. 连续播放/跳转时，浏览器的帧率、长任务是否被拖垮。

## 2. 测试对象与环境

| 项 | 值 |
| --- | --- |
| 视频 | BV1Js411o76u《【炮姐/AMV】我永远都会守护在你的身边！》 |
| aid / cid | 810872 / 1176840 |
| 规模 | 26,510,848 播放、1,511,332 条弹幕、332,453 条回复 |
| 时长 | API 报 689 s；播放器按合集分 P 报 324 s |
| segment 1 | 5,886 条 / 1,240,881 B（原始 protobuf），唯一文本 2,357 条（重复率 60%） |
| 服务 | python3 -m danmaku_filter.cli serve --online-backend linear，127.0.0.1:8765 |
| 缓存 | data/tags/BV1Js411o76u.json：39,840 条、20.4 MB、backend rules-online、source 全为 linear-online |
| 浏览器 | ego lite（Chromium），1600x1000，注入 GM shim + 用户脚本 dist |
| 机器 | WSL2 / python3.14 / node 24 / RTX 5060 Laptop 8 GB |

## 3. 结论速览

| 环节 | 结果 | 判读 |
| --- | --- | --- |
| 服务端单请求 5,886 条 | 41.1 ms（0.007 ms/条），修复前 2,288 ms | 可接受，瓶颈原本是重复解析 20 MB 缓存 |
| 服务端并发 | 8x200 并发 10,006 条/s；16x200 6,509 条/s，0 错误 | 单机足够撑住多个标签页 |
| 冷启动写盘 | 5,886 条 268.9 ms（0.046 ms/条） | 首次访问可接受 |
| 客户端 protobuf | 解析 26.45 ms；1,000 删 + 1,000 折叠 25.53 ms | 单段主线程 30-55 ms，无感 |
| 浏览器（密度关） | 首拦截 2.6 s；7,064 显示 / 7 折叠 / 0 隐藏 | 屏幕仍然满，符合“只按标签策略”的预期 |
| 浏览器（密度开 30 条/10 s） | 隐藏 16,161、显示 1,328、DOM 常驻 30 条 | 屏幕立刻稀疏，音量旋钮有效 |
| 帧率 | p50 13.9 ms、p95 14 ms，长任务 4-5 次共约 400 ms | 只在加载/首段解析时有可感知卡顿 |

## 4. 服务端压测

### 4.1 单请求批量（ephemeral + labels，偏好全 show）

| 条数 | 耗时 | 每条 |
| --- | --- | --- |
| 100 | 11.8 ms | 0.118 ms |
| 500 | 66.8 ms | 0.134 ms |
| 1,000 | 136.3 ms | 0.136 ms |
| 2,000 | 253.8 ms | 0.127 ms |
| 2,357 | 321.7 ms | 0.136 ms |

### 4.2 修复后的同一请求（增加内存标签缓存）

| 条数 | 修复前 | 修复后 | 提升 |
| --- | --- | --- | --- |
| 200 | 902.8 ms | 4.2 ms | 215x |
| 1,000 | 2,892.7 ms | 7.0 ms | 413x |
| 5,886（全段） | 2,288.3 ms | 41.1 ms | 56x |

根因：每次 /api/v1/decisions 都从磁盘读取并解析整份 20.4 MB 的标签缓存。修复见 §6.4。

### 4.3 并发（坏请求 0）

| 并发 | 每请求条数 | 吞吐 | p50 | p95 |
| --- | --- | --- | --- | --- |
| 4 | 500 | 7,071 条/s | 269 ms | - |
| 8 | 500 | 6,987 条/s | 495 ms | 740 ms |
| 8 | 200 | 10,006 条/s | 125 ms | 192 ms |
| 16 | 200 | 6,509 条/s | - | 1,151 ms |

### 4.4 写盘与 DOM 节拍

- 冷启动写盘 5,886 条：268.9 ms（0.046 ms/条），落盘 2,179.7 KB；
- 热请求（缓存命中）：76.6 ms（0.013 ms/条）；
- DOM 兜底 300 ms 节拍、每次 200 条、连续 60 次：p50 17.47 ms、p95 19.55 ms、max 19.95 ms，累计 1.07 s；
- 5 个新视频并发写盘（各 1,000 条）：wall 0.14 s，35,601 条/s。

## 5. 客户端 protobuf 与浏览器端到端

### 5.1 protobuf 微基准（node，extension/lib/dmproto.js，5,886 条 / 1.24 MB）

| 操作 | 耗时 |
| --- | --- |
| parseSegment | 26.45 ms |
| filterSegment 无改动 | 11.72 ms |
| filterSegment 1,000 删 + 1,000 折叠 | 25.53 ms |

### 5.2 端到端（密度关）

- 从导航开始计时：首段拦截 2.6 s、首条折叠记录 3.0 s；
- 5,886 条段：显示 7,064 / 折叠 7 / 隐藏 0；
- 帧间隔 p50 13.9 ms、p95 14 ms、over50 4 次（含一次 1,020 ms 的加载停顿）；
- longtask 4 次，合计 415 ms，最大 143 ms；
- 截图：docs/screenshots/e9-density-off.png（屏幕仍被弹幕铺满，角标“已隐藏 0 · 折叠 11”）。

### 5.3 端到端（密度开：30 条 / 10 s，超限隐藏）

- 偏好必须在加载前生效（否则已下载的段不回放，见 §9.1）；
- 4 个段累计：显示 1,328 / 折叠 9 / 隐藏 16,161，DOM 常驻 30 条；
- 帧间隔 p50 13.9 ms、p95 14.1 ms，longtask 4 次合计 410 ms；
- 截图：docs/screenshots/e9-density-on.png（屏幕几乎清空，角标“已隐藏 6,707 · 折叠 7”）。

### 5.4 标签分布（修复统计口径后，第 1 段）

客户端面板统计：humor 5,868 / information 512 / emotional_resonance 389 / other 292 / irrelevant_repetition 9；
服务端同段 labels：humor 4,270 / information 970 / emotional_resonance 384 / other 261 / irrelevant_repetition 1（客户端含 4 个段的累计与去重差异）。

## 6. 压测中发现并修复的问题

### 6.1 注入的是旧构建

ego 目录里的副本停留在 min_confidence 0.6、密度 12/collapse、per_type 全 collapse 的版本，导致“怎么调都没变化”。修复：每次构建后显式复制 dist 到注入目录，并在面板显示脚本版本号。

### 6.2 localStorage 里的 enabled:false 静默全放行

旧偏好残留 enabled:false 时，脚本一条都不拦，角标也没有提示。修复：updateBadge 在暂停时显示“已暂停 · 点控制台开启过滤”。

### 6.3 密度控制覆盖用户显式“折叠”

原逻辑对除 hide 外的所有动作套用密度超限动作，把用户明确设为折叠的标签也改成隐藏。修复：只有 show 竞争窗口槽位，显式 collapse 保留占位（extension/lib/policy.js、extension/test/density.test.js 同步更新）。

### 6.4 每次请求重解析 20 MB 缓存

新增 _TagStore：按 bvid 缓存解析结果，用 mtime 热加载；服务自己写盘后调用 saved() 直接回填，不再重新解析。新增 2 个测试（三次请求只解析一次、在线写盘后不再重解析）。

### 6.5 LLM teacher 队列无上限

密集视频在几分钟内向队列塞了 25,097 条待升级评论，DeepSeek 侧会持续计费数小时。修复：max_pending = 4,000，超出部分计入 llm_dropped 并保留快速线性标签；同一 dmid 去重；health 暴露 llm_pending / llm_dropped / tag_cache_loads。

### 6.6 BrokenPipe 把日志刷成 traceback

页面跳转或 5 s 超时中断请求时，服务端回写会抛 BrokenPipeError 并打印整段栈。修复：_send_json 容错，DanmakuFilterServer.handle_error 对 BrokenPipe/ConnectionReset 静默。

### 6.7 面板把“显示”的弹幕全记成 other

服务端只为被拦截的条目返回 decisions，客户端因此对每条显示项都计 other，面板分布完全失真。修复：请求带 labels:true，planSegment 用 labels 为显示项补标签，面板显示真实分布（§5.4）。

## 7. 冷启动质量发现（未修复，需拍板）

该视频没有 bootstrap 模型，全部条目由 data/models/_global.json 在线打标并落盘，结果：39,840 条里 humor 25,684（64%）、other 6,231、information 4,647、emotional_resonance 2,029、irrelevant_repetition 1,249。

这是 E8 里已经见过的“幽默吸引子”：跨视频的全局模型把所有短句倾向判成 humor，而这次这些标签被直接写进缓存（backend rules-online、source linear-online），后续访问命中后不会再改。

建议二选一：

1. _global 打标结果不落盘（只用于当前会话）；
2. 落盘时标记 provisional:true，bootstrap/人工纠错时优先覆盖，并在面板提示“该视频尚未校准”。

## 8. 测试

- python3 -m unittest discover -s tests → 193 tests OK（新增 tests/test_server_perf.py 4 个用例）；
- npm --prefix extension test → 58 pass 0 fail；
- 端到端脚本：/tmp/ego-D2-dens-off.mjs、/tmp/ego-D3-dens-30b.mjs（ego-browser nodejs < 脚本）。

## 9. 已知问题（本轮未修）

### 9.1 中途改密度不回放已加载的段

reapplyPrefs 只重算面板条目和 DOM 节点，不会重新过滤播放器里已经排队的弹幕；用户调完滑块要等下一段（长视频可达数分钟）才看到效果。压测时因此必须“先写偏好再 reload”。

### 9.2 seek/重放计数可能重复

state.shown/hidden/collapsed 按 segmentKey|dmid 去重，但跨段重放与面板上限裁剪（200 条）会让角标数字与实际拦截条数不完全一致，面板数字只应读作“本会话累计”。

### 9.3 首拦截 1.2-2.6 s

这段时间是 B 站播放器拉取首段 + 本地服务决策 + protobuf 改写的总和；播放器在这之前会先显示一小段未过滤弹幕（fail-open 设计）。

## 10. 复现命令

~~~bash
# 1) 启动服务（无 teacher，避免压测时产生 API 费用）
cd /home/alphayang/bili-tagging
DM_LLM_ENV_FILE=/nonexistent/dmf-stress.env nohup python3 -m danmaku_filter.cli serve \
  --online-backend linear > /tmp/dmf-serve.log 2>&1 &

# 2) 服务端批量延迟（脚本会打印 200/1000/全部 三档）
python3 /tmp/stress_lat3.py

# 3) 浏览器端到端（先写偏好再 reload，再播放 30 s）
node extension/build.mjs && cp extension/dist/bili-danmaku-filter.user.js /mnt/c/Users/alphayang/ego/dmf/dmf.user.js
ego-browser nodejs < /tmp/ego-D3-dens-30b.mjs
~~~

## 11. 一句话总结

整条链路在单段 5,886 条、全站 151 万弹幕的视频上可用：服务端 0.007 ms/条、客户端单段 30-55 ms、帧率 p95 14 ms；密度旋钮能把 16,000 条弹幕压到 30 条常驻。真正需要继续修的是“改偏好立即回放”和冷启动时全局模型标签落盘这两个体验/质量问题，而不是性能。
