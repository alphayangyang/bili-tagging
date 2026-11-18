# Bilibili 弹幕过滤 userscript（extension/）

一个 Tampermonkey 用户脚本：在 B 站视频页拦截播放器的 `seg.so` 弹幕请求，
把原始 protobuf 字节发给本地标签服务换取 `show / hide / collapse` 决策，
再用同一套 protobuf 编解码重写响应交回播放器。攻击/争论/剧透默认隐藏，
无意义重复默认折叠；隐藏与折叠都可在角标面板里逐条撤销或改标签。

对应契约：`docs/design/contracts.md` §2（dmproto/policy 语义）、§4（本地 HTTP API）、
§5（prefs + 密度）、§6（userscript）。实现细节见 `docs/design/plan-v1.md`。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `lib/dmproto.js` | 零 import 的 ESM protobuf wire 编解码：`parseSegment` / `filterSegment` / `iterElements` / `replaceField` / `encodeField` / `encodeVarint`。dmid 全程用 64 位字符串（BigInt 读 varint，避免 >2^53 精度丢失）。 |
| `lib/policy.js` | 零 import 的 ESM 偏好与决策逻辑：`DEFAULT_PREFS` / `normalizePrefs` / `mergePrefs` / `decide` / `resolveItem` / `DensityController` / `planSegment` / `collapsedText`。 |
| `lib/linear.js` | 零 import 的 ESM 线性打分器：`LinearScorer`，按 Python `LinearTagger` 逐位复现 hashed char 1–3gram TF-IDF 与 centroid/softmax 打分，逐视频模型可在浏览器本地出分。 |
| `lib/pool.js` | 零 import（仅 import dmproto/policy）的整池动作表：`segmentCount` / `mergePool` / `buildActionTable` / `dropSet` / `countForSegment` / `poolStats`，密度按时间窗对整池抽稀（information/other 豁免）。 |
| `lib/poolweb.js` | 零 import 的整池抓取与播放器接线（依赖拼接后的 main.js 作用域）：`loadPool`（list.so 主源 + seg 兜底）、`labelPool`（本地模型优先）、`serveSegmentFromPool`、顶部进度胶囊。 |
| `lib/study.js` | 零 import 的 ESM 研究模式逻辑：条件预设（`conditionSpec` / `prefsForCondition` / `analysisPrefsForCondition`）、URL/localStorage 激活（`parseStudyConfig`）、有界事件记录器（`createStudySession`）、协议 §7 导出与 `diffPrefs` / `countActions`。 |
| `src/main.js` | 普通页面脚本（无 import/export）：XHR/fetch hook、GM_xmlhttpRequest 拉原始弹幕、决策缓存、UI 面板、偏好/反馈、study mode 接线。 |
| `build.mjs` | 把上面四份源码去掉顶层 `export ` 后按顺序拼进一个 IIFE，写入 `dist/`。 |
| `dist/bili-danmaku-filter.user.js` | **构建产物，安装的就是这个文件**（单文件，含 ==UserScript== 元数据）。 |
| `test/*.test.js` | `node:test` 单测（dmproto 编解码、policy、密度窗口、linear 打分器、study mode），dmproto 使用真实 fixture `tests/fixtures/seg_sample.bin`。 |
| `test/index.js` | 测试目录聚合入口。Node 24 的 `node --test <目录>/` 会把目录解析成单个入口模块，没有它命令会报 `Cannot find module .../extension/test`。 |

## 构建与测试

在仓库根目录执行：

```bash
node extension/build.mjs
node --check extension/dist/bili-danmaku-filter.user.js
node --test extension/test/
```

也可以只跑单个文件：

```bash
node --test extension/test/dmproto.test.js
```

当前基线：8 个测试文件共 103 个测试全部通过。测试只依赖 Node 内置模块，
不需要 `npm install`。（直接 `cd extension && node --test` 也会跑通，但会把
`test/index.js` 和七个测试文件各算一次，计数会翻倍，属预期现象。）

## 安装 userscript

1. 浏览器安装 Tampermonkey（Chrome/Edge/Firefox 均可）。
2. 打开 `extension/dist/bili-danmaku-filter.user.js`，Tampermonkey 会自动弹出安装页
   （或 Tampermonkey 面板 → 实用工具/添加新脚本 → 粘贴文件全文后保存）。
3. 确认元数据块正确后再点「安装」：

   ```
   // @match        https://www.bilibili.com/video/*
   // @run-at       document-start
   // @grant        GM_xmlhttpRequest
   // @connect      127.0.0.1
   // @connect      api.bilibili.com
   ```

   `@run-at document-start` 是为了在播放器发请求前装好 hook，别改成 document-end。
   `@connect api.bilibili.com` 允许脚本直接读取 `seg.so` 原始字节；
   `@connect 127.0.0.1` 允许访问本地标签服务。
4. 刷新视频页。右下角会出现固定角标「弹幕过滤 已隐藏 N · 折叠 M」。
5. 修改 `src/` 或 `lib/` 后重新 `node extension/build.mjs`，再到 Tampermonkey 里
   更新/重装脚本（或直接粘贴新 dist 内容）。

## 启动本地服务

服务与 userscript 是分开的两个进程。先在仓库根目录启动服务（默认
`127.0.0.1:8765`、标签目录 `data/tags`）：

```bash
python3 -m danmaku_filter.server
# 等价：python3 -m danmaku_filter.cli serve [--host 127.0.0.1] [--port 8765] [--tags-dir data/tags]
```

服务必须能读到对应 bvid 的标签缓存，否则 `/api/v1/decisions` 会返回
`found=false`、全部落在 `miss`，userscript 会原样放行（页面正常但不过滤）。
预热某个视频的标签缓存：

```bash
python3 -m danmaku_filter.cli precompute --bvid BV1GJ411x7h7 --backend rules
```

仓库当前带有 `data/tags/BV1GJ411x7h7.json`（rules 后端，1407 条，含 5 条
spoiler、4 条 irrelevant_repetition），可直接用它做手动测试。

服务地址可在面板「设置 → 服务地址」里改，持久化在
`localStorage["dmf.service.v1"]`；偏好持久化在 `localStorage["dmf.prefs.v1"]`。

## 工作方式（简要）

- `document-start` 时在页面 realm（`unsafeWindow`）上 patch
  `XMLHttpRequest.prototype.open/send/abort` 与 `fetch`，只装一次。
- 命中 URL 含 `/x/v2/dm/web/seg.so` 或 `/x/v2/dm/wbi/web/seg.so`（2023 年后播放器改走带签名的 wbi 路径）时：用 `GM_xmlhttpRequest` 以同样的 URL
  重新拉原始字节 → `dmproto.parseSegment` 取出 dmid 列表（全部为字符串）→
  `POST /api/v1/decisions`（附当前 prefs）→ `policy.planSegment`（偏好 +
  本会话 override / showOnce + 密度窗口）→ `dmproto.filterSegment` 重写 protobuf
  （drop 整条删除，collapse 只替换 field7 文案，其余字段与未知字段原样保留）。
- XHR 走「自持请求」：不调用原 `send`，而是用 `Object.defineProperty` 在 xhr 实例上
  伪造 `readyState=4 / status=200 / response / responseText`，再 dispatch
  `readystatechange → load → loadend`；`getAllResponseHeaders()` 返回
  `content-type: application/octet-stream` 与新的 `content-length`。
  `responseType` 为 `arraybuffer` / `blob` / `""(text)` 时分别合成对应类型；
  `json` / `document` 无法逐字节合成，直接放行原请求。
- fetch hook：先 `await` 原 fetch，`response.clone().arrayBuffer()` 处理后
  返回 `new Response(bytes, {status, statusText, headers})`。
- 任何异常（无 GM、bvid 解析失败、服务不可达/超时 1500ms、protobuf 坏数据、
  responseType 不支持）一律 fail-open：调用原 `send` / 返回原 response，
  页面与弹幕照常。角标会显示「· 本地服务未连接」。
- 决策按 `(bvid, cid, segment_index)` 缓存，同一分段不重复 POST；失败结果缓存
  10s 避免服务挂掉时反复阻塞播放器。SPA 换视频（每秒检测 URL 变化）会清空
  计数、条目、override 与缓存。
- 反馈通道：偏好变更 `POST /api/v1/feedback kind=prefs`；「显示这条」发
  `policy_event(action=show_once)`；「总是显示此类型」发
  `policy_event(action=always_show_type)`；改标签发
  `label_correction`（服务端据此写 `data/overrides/<bvid>.json`，下次
  decisions 生效），本地同时立刻重算，不等服务端。
- 折叠文案固定为 `【已折叠·{中文标签}】`，10 类映射：information 信息、humor 幽默、
  emotional_resonance 共鸣、irrelevant_repetition 无意义重复、attack 攻击、
  conflict 争论、spoiler 剧透、political 政治、inflammatory 引战、other 其他。
- 密度（客户端规则）：把最终 action 不为 `hide` 的弹幕算作「可见」
  （show 与 collapse 都占可见位）。窗口内可见数 `>= max_visible` 时，新到的
  `action === "show"` 条目按 `density.exceed` 处理，reason=`density`；
  information 类型（`DENSITY_PROTECTED_LABELS`）永远豁免，显式的 hide/collapse
  决策不会被密度覆写。服务端 decisions 只返回非 show 条目，所以纯 show 弹幕
  不参与密度升级。

## 协议补充：decisions 会带上弹幕文本

`POST /api/v1/decisions` 的 body 现在是 `{bvid, dmids, texts, prefs}`：
`texts` 是 `{dmid: text}` 映射，只对**没有离线缓存**的视频有意义。服务端会用规则
基线当场打标（响应字段 `online` 是本次在线打标条数，`found` 表示该视频是否已有离线
缓存），并把结果写入 `data/tags/<bvid>.json`（`backend: rules-online`），所以第二次
打开同一视频就是缓存命中。没有文本的 dmid 仍走 `miss` → `/api/v1/miss`。

面板「高级 · 本地服务」末尾有一行诊断：`拦截 N · 决策成功 N · 失败 N · 缓存 命中/未命中 ·
在线打标 N · 最近错误: ...`，用来区分「页面没变化」是拦截问题还是决策问题。

## 整池预取层（v0.4）

进视频页后不再等播放器逐段要，而是先把整池弹幕拉下来算好动作表：

1. `loadPool` 并行请求 `/x/v1/dm/list.so?oid=<cid>`（整池 1,200–3,600 条，带真实绝对时间戳），
   失败或为空时回退逐段抓 `seg.so` protobuf（未签名 seg.so 只返回第 1 段，其余 304，所以只能兜底）。
2. `labelPool`：`GET /api/v1/model?bvid=` 拿到该视频的线性模型时，用 `LinearScorer` 在浏览器内本地打分；
   没有模型则按 300 条一批、2 并发调 `POST /api/v1/decisions`（`ephemeral:true`，不写缓存）。
3. `buildActionTable` 一次算出整池 dmid→动作表（含密度抽稀，information/other 豁免且不占名额）。
4. 播放器请求 `seg.so` 时 `serveSegmentFromPool` 按表改写 protobuf（`dropSet` → `filterSegment`），
   折叠的弹幕整条不进播放器；折叠/隐藏条目进面板「折叠记录」（以整池为真相源，前 200 条）。
5. 改偏好：`reapplyPrefs` → `rebuildPoolActions` 立即重算整池，角标与面板统计显示整池数字。

等待策略：首次播放最多等 `POOL_WAIT_MS = 2500` ms，屏幕顶部胶囊显示「正在整理弹幕池 x/y 段 · N 条 · 打分中」；
超时则先边播边补，池就绪后后续段自动切到整池动作表。

低置信放行 `prefs.fail_open`（面板「全局参数」开关，默认开）：关闭后低于阈值的条目也按标签策略处理，
所以「全部隐藏 + 关闭低置信放行」会让整池 0 条显示。

## 实时兜底层（DOM）

segment 拦截只能影响还没进播放器的弹幕，所以 main.js 还有一层 DOM 兜底：

- startLiveFilter() 每 300 ms 调 liveTick()，扫描 .bpx-player-row-dm-wrap 下的
  .bili-danmaku-x-dm（同时兼容 .bpx-player-dm-wrap）。
- 每条的原文记在 node.__dmfLiveText；show 时还原，collapse 时把 textContent 换成
  「已折叠·<中文标签>」并加 .dmf-live-collapsed，hide 直接 node.remove()。
  所有决定都走同一套 decide() 与用户 prefs。
- 没见过的文本攒一批调 POST /api/v1/decisions，body 带 ephemeral: true（服务端不写
  data/tags/）与 labels: true（响应返回每条 dmid 的 {label, score}，包括最终要显示的
  条目），客户端缓存在 state.dom.labels（上限 800 条）。
- reapplyPrefs() 末尾会立刻 liveTick()，所以面板上改策略在当前进度就能看到效果；
  诊断行会显示「DOM 兜底 隐藏 N · 折叠 M」。

## 面板结构

- 浮层 `#dmf-panel`：右下方 412px、`max-height:76vh`、body 最小 220px
  （避免反馈日志为空时塌成一条），面板内独立滚动。
- 顶部：品牌 + 「过滤控制台」+ 服务状态点 + 关闭 ×；下面三格统计（显示/折叠/隐藏）。
- 三个 tab：标签策略（10 行 + 全局参数 + 高级）、折叠记录（条目卡片）、反馈日志。
- 改标签用独立的 `#dmf-popover`（fixed，10 标签网格，点空白处关闭）。
- 所有 CSS 都加 `#dmf-` 前缀，不改 B 站全局样式。

## 研究模式（study mode）

面向受控实验：同一份 userscript 里按条件预置策略、默认不渲染任何 UI、只记录事件，
最后把会话日志导出成 JSON。

**激活**（URL 参数优先）：

```
https://www.bilibili.com/video/BV1GJ411x7h7?dmf_study=pilot-01&dmf_cond=prototype
```

`dmf_cond` 取以下三个值之一：

| 条件 | 是否改写弹幕 | 生效策略 | 用途 |
| --- | --- | --- | --- |
| `all_on` | 否（record-only） | `enabled:false`，全部放行 | 只记录基线：每个 segment 的 would-be show/hide/collapse |
| `keyword` | 否（record-only） | `enabled:false`，全部放行 | 屏蔽词由 B 站原生设置负责，本脚本只记录 |
| `prototype` | 是 | attack / conflict / inflammatory / irrelevant_repetition 隐藏，spoiler 折叠，其余显示，密度关闭 | 记录真实干预效果与用户反馈 |

记录型条件（`all_on` / `keyword`）虽然不改弹幕，但仍会 `POST /api/v1/decisions`
拿**原型策略下**的决策来统计 would-be 隐藏/折叠数（请求时用 `analysisPrefs`，
即 enabled=true 的原型偏好）；事件里 `applied:false`。抓取原始字节后总是回放原请求，
所以播放与未安装脚本完全一致。

**面板与角标默认隐藏。** 研究者可在页面控制台执行：

```js
window.__dmfFilter.study.showUi();   // 临时显示角标与面板
window.__dmfFilter.study.hideUi();   // 再次隐藏
window.__dmfFilter.study.export();   // 返回导出对象（不下载）
window.__dmfFilter.study.download(); // 下载 dmf-study-<studyId>.json
window.__dmfFilter.study.id;         // 当前 study id
window.__dmfFilter.study.condition;  // all_on | keyword | prototype
window.__dmfFilter.study.recordOnly; // 是否为记录型条件
```

用户仍可改偏好（显示这条 / 总是显示此类型 / 改标签 / 面板设置），这些操作会立即生效
（prototype 条件下）并被记录，但 **`localStorage["dmf.prefs.v1"]` 不会被写入**：
受控条件不能被参与者上一次实验留下的偏好污染。研究模式下用户的持久偏好保持原样。

**localStorage 覆盖**（供研究者预置，不必改 URL）：键 `dmf.study`，支持三种格式：

- JSON：`{"study_id":"pilot-01","condition":"prototype"}` 或 `{"studyId":"...","condition":"..."}`
- 管道分隔：`"pilot-01|prototype"`
- 单独一个 id（`"pilot-01"`，条件仍需 URL 的 `dmf_cond`）或单独一个条件名
  （`"prototype"`，仅覆盖 URL 已给出的 study）。localStorage 覆盖优先于 URL，
  且可以独立激活研究模式（方便研究者远程让受试者刷新）。

**事件类型**（`state.study.events`，上限 5000 条，超出后丢弃最旧的并在
`counts.dropped_events` 计数）：

| type | 字段 | 触发点 |
| --- | --- | --- |
| `segment` | `bvid, url, segment_index, shown, hidden, collapsed, by_label, latency_ms, online, applied` | 每处理一个 seg.so 分段 |
| `pref` | `key, from, to` | 用户改任意偏好（扁平 key，如 `per_type.attack`） |
| `correction` | `dmid, text, from, to` | 面板「改标签」 |
| `restore` | `dmid, label` | 面板「显示这条」 |
| `expand` | `panel` | 研究者/用户打开面板（事件日志里同时记录展开动作） |

**导出结构**（协议 §7）：

```json
{
  "study_id": "pilot-01",
  "condition": "prototype",
  "started_at": "2026-11-18T10:00:00.000Z",
  "ended_at": "2026-11-18T10:25:00.000Z",
  "counts": {
    "segments": 42, "shown": 1200, "hidden": 87, "collapsed": 31,
    "prefs": 3, "corrections": 2, "restores": 1, "expands": 4,
    "dropped_events": 0
  },
  "events": [ ... ],
  "script_version": "0.3.0",
  "prefs": { "...": "导出时的生效偏好快照" },
  "ua": "..."
}
```

`download()` 用 `Blob` + `URL.createObjectURL` + `<a download>` 生成
`dmf-study-<id>.json`（id 中非 `[0-9A-Za-z._-]` 的字符替换为 `_`）。

## 手动测试清单（无浏览器自动化，需真机）

前置：`node extension/build.mjs` 通过、userscript 已安装并启用、本地服务已启动、
目标视频有标签缓存（默认用 `https://www.bilibili.com/video/BV1GJ411x7h7`）。

1. **加载正常**：页面能正常播放；右下出现角标「弹幕过滤 已隐藏 N · 折叠 M」；
   服务在线时不出现「本地服务未连接」；浏览器控制台无未捕获异常。
2. **默认过滤生效**：播放到有 spoiler 弹幕的时间点（该缓存有 5 条），
   画面上不再出现这些弹幕；irrelevant_repetition 类型的弹幕被替换为
   `【已折叠·无意义重复】` 文案；information/humor 正常显示。
   （DevTools Network 里 `seg.so` 的响应体积应小于未安装脚本时。）
3. **角标计数**：每过滤一段，N/M 递增；同一 dmid 只计一次，不会因分段重载重复累加。
4. **开关**：取消角标里的「过滤」勾选 → `enabled=false`，之后的 `seg.so`
   请求原样放行、弹幕恢复（已经渲染的旧弹幕不会立刻回来，属预期）；
   重新勾选后恢复过滤。刷新页面后开关状态仍保持（localStorage）。
5. **面板**：点角标里的「控制台」打开面板（点品牌或文字也行），右上「×」关闭。
   面板顶部三格是本次会话的 显示 / 折叠 / 隐藏 计数，下面三个 tab：
   - **标签策略**：10 个标签行（色点 + 中文 + 英文 + 本次处理条数 + 显示/折叠/隐藏三态
     分段按钮）；下面「全局参数」是置信度阈值、密度窗口、窗口上限、超限动作、
     启用密度控制；最下面「高级」可改服务地址、恢复默认策略。
   - **折叠记录**：被隐藏/折叠的条目卡片（时间 mm:ss、文本、标签、分数、原因/证据、
     「恢复显示」「改标签」）；已恢复显示的条目留在列表里但状态变为「已显示」。
   - **反馈日志**：本会话的改标签 / 恢复显示 / 偏好变更事件，带时间。
6. **恢复显示**：点某条目的「恢复显示」→ 状态变为「已显示」、按钮消失、角标计数减一、
   本会话不再被隐藏（即使重新加载该分段）；服务端 `data/feedback/policy_events.jsonl`
   出现 `show_once` 记录。
7. **改标签**：点「改标签」弹出 10 标签网格，选一个 → 该条按新标签重新决策（状态 chip
   与按钮随之更新），`data/feedback/label_corrections.jsonl` 与
   `data/overrides/<bvid>.json` 出现记录；之后重新加载同一分段，服务端 decisions
   按新标签返回。
8. **按类型改策略**：在「标签策略」里把某一类切成显示/隐藏/折叠 → 列表与角标立即重算；
   刷新页面后仍生效；`data/feedback/prefs_events.jsonl` 出现 prefs 事件。
9. **全局参数**：拖动阈值滑块、改密度窗口/上限/超限动作 → 立即生效；刷新后偏好仍在；
   「高级 → 恢复默认策略」后回到默认（`localStorage["dmf.prefs.v1"]` 被删除）。
10. **密度**：把「启用密度控制」打开，时间窗保持 10s、最大可见数改为 1，
    播放弹幕密集处 → 出现 reason=density 的折叠条目；把最大可见数调大或关闭
    密度控制后，折叠条目恢复原样。information/other 类型不受影响。
11. **服务未启动**：停掉本地服务后刷新视频页 → 页面正常播放、弹幕原样显示（不过滤），
    角标显示「· 本地服务未连接」；控制台没有未捕获异常。重新启动服务后点开面板
    （或等下一次请求）状态恢复为已连接。可在控制台执行
    `localStorage["dmf.debug"]="1"` 后刷新，查看 `[dmf]` 调试日志。
12. **未知 bvid**：打开一个没有标签缓存的视频 → 过滤静默不生效、页面正常；
    服务端 `data/pending/<bvid>.json` 会收到 miss 的 dmid 列表
    （`POST /api/v1/miss` 幂等）。
13. **换视频 / 切 P**：在 SPA 内切换视频或分 P 后，角标计数归零、面板条目清空，
    新视频重新过滤，不残留上一个视频的 override。
14. **XHR 与 fetch 两条路径**：B 站不同播放器/场景可能分别走 XHR 或 fetch；
    两者都应生效。若只看到其中一种生效，用步骤 11 的 debug 日志确认 hook 是否命中。
15. **快速切换/请求中断**：拖动进度条快速跳转、切清晰度时，页面不报错、弹幕不卡死
    （被 abort 的请求不会投递伪造响应，会回退原请求）。
16. **研究模式 · 记录型**：打开
    `?dmf_study=pilot-test&dmf_cond=all_on` → 页面**完全不变**（无角标、无面板、
    弹幕原样显示），控制台执行 `window.__dmfFilter.study.id` 得到 `pilot-test`；
    播放一段后执行 `window.__dmfFilter.study.export()`，应看到 `condition:"all_on"`、
    `counts.segments > 0`，且 `events` 里 segment 事件 `applied:false`、
    `hidden/collapsed` 是原型策略下的 would-be 计数（可以非 0）；刷新后
    `localStorage["dmf.prefs.v1"]` 不变。
17. **研究模式 · 原型**：打开 `?dmf_study=pilot-test&dmf_cond=prototype` →
    默认仍无角标；attack/conflict/spoiler 弹幕按原型策略消失/折叠；控制台执行
    `window.__dmfFilter.study.showUi()` 后角标与面板出现，执行 `hideUi()` 恢复隐藏。
    在面板里把 attack 改成「显示」→ 立即生效，`export()` 的事件里出现
    `type:"pref"`，且不写 `localStorage["dmf.prefs.v1"]`。
18. **研究模式 · 导出与下载**：`window.__dmfFilter.study.download()` 下载
    `dmf-study-pilot-test.json`；JSON 顶层键包含
    `study_id / condition / started_at / ended_at / counts / events / script_version / prefs / ua`；
    时间戳为 ISO；`counts` 中 dropped_events 为 0（短会话）；
    用 `JSON.parse` 校验可解析。
19. **研究模式 · 不污染正常模式**：清掉 URL 参数与 `localStorage.removeItem("dmf.study")`
    后刷新 → 恢复普通模式（角标出现、面板可用、偏好正常持久化），
    且之前 prototype 会话中的临时改动没有写进 `dmf.prefs.v1`。

## 已知限制与契约说明

- **自持请求会多拉一次 `seg.so`**：原请求被拦截后不发送，改由
  `GM_xmlhttpRequest` 重新拉同样的 URL；失败才回退原 `send`。因此
  Network 面板里可能看到一次（或两次）`seg.so` 请求，属预期实现方式。
- **`filterSegment` 的 JS 返回对象** `{data, hidden, collapsed}`，
  Python 版返回 tuple `(bytes, hidden, collapsed)`，语义一致、仅形状不同。
- **JS prefs 额外有 `density.enabled`**（面板「启用密度控制」），Python
  `normalize_prefs` 会忽略未知键；其余字段与 §5 默认值一致。
- **密度语义**：见上文「工作方式」最后一条。服务端只返回非 show 决策，
  所以客户端能拿到的 label 有限；纯 show 弹幕不会被密度折叠。
- **`decide` 在 `enabled=false` 时返回原 label + action=show**（reason=disabled），
  与 Python 版一致；userscript 层面开关关闭时直接放行原请求、根本不发 decisions。
- **bvid 解析**：优先页面 URL `/video/(BV\w+)`，其次 `__INITIAL_STATE__.bvid`、
  `localStorage["dmf.bvid"]`；解析不到就 fail-open，不发任何请求。
- **只拦截 seg.so**：其它弹幕接口（`list.so`、互动弹幕、BAS 代码弹幕）不处理。
  2023 年后播放器改走带签名的 `/x/v2/dm/wbi/web/seg.so`，脚本同时匹配旧
  `/x/v2/dm/web/seg.so` 与新 wbi 路径（`SEG_PATHS`）；再改签名路径时只需改这里。
- **元数据/版本**：`@version` 从 `extension/package.json` 的 `version` 生成，
  发版前改 package.json 再重新 build。
- 脚本依赖 Tampermonkey 的 `GM_xmlhttpRequest`；没有 GM 环境（例如直接把
  dist 当普通页面脚本注入）时全部 fail-open，只有 fetch hook 可能工作。
- **研究模式（study mode）**：
- **研究模式的计数口径**：记录型条件（all_on/keyword）用 `analysisPrefs`
  （prototype 策略 + enabled=true）向服务端要决策，否则 enabled=false 会让服务端
  对全部 dmid 返回 show，would-be 隐藏/折叠恒为 0；导出的 `prefs` 仍是实际生效的
  记录型偏好（enabled=false）。`latency_ms` 含 GM 重新拉取 seg.so 的耗时。
- **研究模式的 expand 事件**：协议 §7 的事件表没有定义面板展开事件，这里把
  「打开面板」记为 `{type:"expand", panel}`，语义即展开动作；记录型条件下
  用户主动打开 UI 才会出现（研究者调用 `showUi()` 也会记录）。
- **研究模式下的响应类型**：XHR 的 `responseType` 为 json/document 时无法逐字节
  合成，记录型条件也会直接放行（不计该段）；fetch 路径正常。
- **研究模式不自动上传**：事件只存在页面内存与导出的 JSON 里，刷新即丢失；
  正式实验需在会话结束前调用 `download()`（或把 `export()` 的返回值另行保存）。
`lib/study.js` 与其它 lib 一样零依赖、可被 `node --test` 直接 import，
  以后要迁移成别的构建方式也只需改 `build.mjs` 的拼接逻辑。
