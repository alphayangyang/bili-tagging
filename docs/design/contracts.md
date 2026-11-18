# 接口契约 v1（MVP 并行开发）

> 状态：MVP 期间**冻结**。任何字段/签名变更必须先在这里改，并通知 lead 与另一位开发者。
> 相关：[plan-v1.md](plan-v1.md)（总计划）、[reconciliation.md](reconciliation.md)（设计决策）、[../BACKLOG.md](../BACKLOG.md)（活文档）。

## 0. 所有权与写作用域（互不重叠）

| 角色 | 写作用域 | 说明 |
|---|---|---|
| lead | `danmaku_filter/{types,fetch,dmproto,policy,feedback,metrics,cli}.py`、`tests/test_{dmproto,policy,fetch,rules,metrics,feedback}.py`、`docs/`、`README.md`、`examples/`、`config/`、`scripts/` | 数据源、编解码、policy、CLI、文档 |
| server-dev | `danmaku_filter/{server,cache,pipeline}.py`、`tests/test_{server,cache,pipeline}.py` | 本地 HTTP 服务、缓存、预计算流水线 |
| userscript-dev | `extension/`（`lib/`、`src/`、`dist/`、`test/`、`build.mjs`、`README.md`） | 油猴脚本、JS 侧 policy/密度、UI、构建与 JS 单测 |

所有人：不 git commit；不装 npm/pip 依赖（python 只用标准库，JS 只用 Node 24 内置模块）；不改别人作用域；发现契约问题先 `send_message` 给 lead。

## 1. 数据目录

~~~text
data/tags/<bvid>.json          # 预计算标签缓存（schema v2，见 §3）
data/overrides/<bvid>.json     # 用户纠正后的标签覆盖
data/pending/<bvid>.json       # 运行时未见过的 dmid 队列（异步补标用）
data/feedback/label_corrections.jsonl   # 分类层事件
data/feedback/policy_events.jsonl       # 策略层事件（用户改偏好/临时显示）
data/feedback/prefs_events.jsonl        # 偏好快照（研究分析用）
data/raw/                      # 原始拉取（不入库）
~~~

所有目录由使用它的模块按需 `mkdir(parents=True, exist_ok=True)`。文件名只用 bvid，不用 cid。

## 2. Python 模块 API（签名冻结）

### types.py（lead）

```python
@dataclass
class Danmaku:
    text: str
    time_ms: int = 0
    mode: int = 1
    dmid: str = ""
    uid: str = ""
    extra: dict = field(default_factory=dict)

@dataclass
class ClassificationResult:
    label: str                      # 主标签（dominant）
    confidence: float = 1.0
    rationale: str = ""
    source: str = "rules"           # rules | llm | user
    tags: dict[str, float] = field(default_factory=dict)  # 新增：多标签 {tag: score}
```
向后兼容：`label/confidence/rationale/source` 不变；`tags` 为空时调用方按 `{label: confidence}` 处理。

### fetch.py（lead）

```python
SEG_URL = "https://api.bilibili.com/x/v2/dm/web/seg.so"

def get_video_info(bvid: str) -> dict:
    """{"bvid","aid","cid","duration"(秒,int),"title"}；API 错误抛 RuntimeError。"""

def fetch_segment(aid: int, cid: int, index: int, retries: int = 3) -> bytes:
    """GET seg.so（type=1&oid=cid&pid=aid&segment_index=index），匿名 UA+Referer。
    返回裸 protobuf 字节；越界段返回 b""（不是错误）。网络错误重试+退避后抛 RuntimeError。"""

def fetch_all_segments(aid: int, cid: int, duration: int, max_segments: int = 60) -> list[bytes]:
    """按 360s 分段拉取，index 从 1 到 max_segments；遇 b"" 即停；返回非空段按序列表。"""

def parse_xml(raw: bytes) -> list[Danmaku]   # 保留，旧接口兼容
def fetch_danmaku(bvid: str) -> list[Danmaku]  # 保留（list.so，仅兼容/对比，不再作为主数据源）
```

### dmproto.py（lead）

```python
def parse_segment(data: bytes) -> list[Danmaku]
    # 每个 elem：dmid=str(field12 idStr 或 field1 varint)、text=field7、time_ms=field2、
    # mode=field3、uid=field6(midHash)；跳过空文本/坏数据。

def parse_element(raw: bytes) -> dict
    # {"dmid","text","time_ms","mode","uid","cid","id_str"}，原始 elem bytes 不返回

def iter_elements(data: bytes) -> Iterator[bytes]   # 逐个返回顶层 field1 的原始 payload

def filter_segment(data: bytes, drop: set[str], collapse: dict[str, str]) -> tuple[bytes, int, int]
    # 返回 (新 segment bytes, 隐藏条数, 折叠条数)。
    # drop 中的 dmid 整条删除；collapse 中的 dmid 只把 field7 content 换成给定文案，
    # 其余字段与未知字段原样保留；顶层 field 按原顺序重建。

def encode_field(number: int, payload: bytes) -> bytes    # wire type 2
def replace_field(raw: bytes, number: int, payload: bytes) -> bytes
```

### policy.py（lead）

```python
DEFAULT_PREFS = {  # 见 §5
    "version": 1, "enabled": True, "min_confidence": 0.6,
    "per_type": {"information": "show", "humor": "show", "emotional_resonance": "show",
                 "irrelevant_repetition": "collapse", "attack": "hide",
                 "conflict": "hide", "spoiler": "hide", "other": "show"},
    "density": {"window_seconds": 10, "max_visible": 30, "exceed": "hide"},
}

def resolve_item(item: dict, override: dict | None) -> dict
    # 有 override 时：dominant=override["label"], tags={label:1.0}, score=1.0,
    # source="user", evidence="user correction"；否则原样返回（不修改入参）。

def decide(item: dict | None, prefs: dict | None = None) -> dict
    # 返回 {"action": "show"|"hide"|"collapse", "label": str, "score": float, "reason": str}
    # item 为 None / 缓存里没有 → show, label="other", score=0.0, reason="not_tagged"
    # prefs 为 None → DEFAULT_PREFS；prefs["enabled"] is False → show, reason="disabled"
    # 规则顺序见 §5。
```

### cache.py（server-dev）

```python
def tags_dir(root: str | Path = "data/tags") -> Path
def tags_path(bvid: str, root: str | Path = "data/tags") -> Path
def load_tags(bvid: str, root: str | Path = "data/tags") -> dict | None      # 文件不存在/坏 JSON → None
def save_tags(bvid: str, payload: dict, root: str | Path = "data/tags") -> None  # 原子写（tmp+replace）
def load_overrides(bvid: str, root: str | Path = "data/overrides") -> dict   # {dmid: {"label":..., "at":...}}，无文件返回 {}
def save_overrides(bvid: str, overrides: dict, root: str | Path = "data/overrides") -> None
def add_pending(bvid: str, dmids: Iterable[str], root: str | Path = "data/pending") -> int  # 去重，返回新增数
```

### pipeline.py（server-dev）

```python
def precompute(bvid: str, backend: str = "rules", tags_dir: str | Path = "data/tags",
               limit: int = 0, video_context: str = "", max_segments: int = 60) -> dict:
    """拉全部分段 → parse → 按 dmid 去重 → 分批分类 → 写 data/tags/<bvid>.json。
    返回 {"bvid","aid","cid","segments","total","labeled","backend","model","path"}。
    backend="llm" 时模型从 classify.llm.settings() 读环境变量；失败直接抛，不留半成品文件。"""
```

### server.py（server-dev）

```python
def main(argv: list[str] | None = None) -> int   # argparse: --host 127.0.0.1 --port 8765 --tags-dir data/tags
def serve(host: str = "127.0.0.1", port: int = 8765, tags_dir: str = "data/tags") -> None
```
启动方式（README/安装说明用）：`python3 -m danmaku_filter.server`。Ctrl+C 正常退出，端口占用报清晰错误。

## 3. 标签缓存 schema v2

```json
{
  "schema": 2,
  "bvid": "BV1GJ411x7h7",
  "aid": 123, "cid": 456, "duration": 213, "segments": 1,
  "backend": "rules", "model": null,
  "created": "2026-10-21T12:00:00Z",
  "items": {
    "34576873522987015": {
      "text": "80年代迪厅必放金曲",
      "time_ms": 12345, "mode": 1,
      "tags": {"emotional_resonance": 0.61, "information": 0.12},
      "dominant": "emotional_resonance",
      "score": 0.61,
      "evidence": "matched cue: ...",
      "source": "rules"
    }
  }
}
```
约束：`tags` 值 ∈ [0,1]；`dominant` ∈ 7 类或 `other`；`score = tags[dominant]`；`items` 键为 dmid 字符串。

## 4. 本地 HTTP API（127.0.0.1:8765）

所有请求/响应 `application/json; charset=utf-8`；响应带 CORS 头 `Access-Control-Allow-Origin: *`，OPTIONS 预检返回 204。出错时 `{"ok": false, "error": "..."}` + 4xx/5xx。
服务只读本地文件/写本地 data 目录，**不访问外网**。

### GET /api/v1/health
→ `{"ok": true, "version": "0.1.0", "tag_files": 3, "tags_dir": "data/tags"}`

### POST /api/v1/decisions
请求：
```json
{"bvid": "BV1GJ411x7h7", "dmids": ["111", "222"], "prefs": {}}   // prefs 可省略，省略用 DEFAULT_PREFS
```
响应：
```json
{"ok": true, "bvid": "BV1GJ411x7h7", "found": true,
 "decisions": {"111": {"action": "hide", "label": "attack", "score": 0.9,
                        "reason": "pref_hide_attack", "text": "up主就是个傻逼", "evidence": "matched cue: 傻逼"}},
 "miss": ["222"], "n": 2}
```
- `decisions` **只包含 action != "show" 的条目**；缺失 = show。
- 缓存文件不存在：`found=false`，`decisions={}`，`miss` = 全部 dmids。
- `prefs` 原样用于 `policy.decide`；缓存中 dmid 的 override 由服务端合并（`resolve_item`）。
- dmids 去重后处理；上限 20000，超限返回 400。

### POST /api/v1/miss
请求：`{"bvid": "...", "dmids": ["..."]}` → `{"ok": true, "added": 2, "pending": 5}`
（写入 `data/pending/<bvid>.json`，对同一设备重复调用幂等。）

### POST /api/v1/feedback
请求：
```json
{"bvid": "...", "dmid": "345...", "kind": "label_correction",
 "payload": {"text": "...", "old_label": "attack", "new_label": "humor"}}
{"bvid": "...", "dmid": "345...", "kind": "policy_event",
 "payload": {"action": "show_once", "label": "attack", "reason": "user_show"}}
{"bvid": "...", "kind": "prefs", "payload": {"prefs": {}}}
```
→ `{"ok": true}`
副作用：`label_correction` 写 `data/overrides/<bvid>.json`（`{"items": {dmid: {"label","at"}}}`）+ 追加 `label_corrections.jsonl`；`policy_event` 追加 `policy_events.jsonl`；`prefs` 追加 `prefs_events.jsonl`。未知 kind → 400。

### GET /api/v1/tags?bvid=...&limit=100
调试用，返回缓存全文（items 截断到 limit，默认 100）→ `{"ok": true, "payload": {...}}`。

### 错误码约定
400 参数缺失/非法 JSON；404 未知路径；405 方法不对；500 未预期异常（响应含 `error` 文本，不打印堆栈到响应）。

## 5. prefs 与决策规则

```json
{"version": 1, "enabled": true, "min_confidence": 0.6,
 "per_type": {"information": "show", "humor": "show", "emotional_resonance": "show",
              "irrelevant_repetition": "collapse", "attack": "hide",
              "conflict": "hide", "spoiler": "hide", "other": "show"},
 "density": {"window_seconds": 10, "max_visible": 30, "exceed": "hide"}}
```
`policy.decide` 顺序：item 缺失→show(not_tagged)；enabled=false→show(disabled)；
override 已由 `resolve_item` 合并；`score < min_confidence`→show(low_confidence)；
`per_type[dominant]` 为 show→show(default_show)；否则返回该 action（reason=`pref_<action>_<label>`）。
密度控制**只在客户端**做（简单窗口计数）：`window_seconds` 内可见弹幕超过 `max_visible` 时，
新到的非 show 弹幕按 `exceed` 处理；show 类弹幕不因密度被隐藏（避免误杀信息类）。日志记 `density` 原因。

## 6. userscript 契约

- 文件：`extension/dist/bili-danmaku-filter.user.js`（构建产物，安装它）；源码 `extension/src/main.js` + `extension/lib/{dmproto,policy}.js`；`node extension/build.mjs` 生成 dist。
- 头：`@match https://www.bilibili.com/video/*`、`@run-at document-start`、`@grant GM_xmlhttpRequest`、`@connect 127.0.0.1`、`@connect api.bilibili.com`、`@version`。
- 拦截 `XMLHttpRequest` 与 `window.fetch` 中 URL 含 `/x/v2/dm/web/seg.so` 的请求：拿到 arraybuffer → `dmproto.parseSegment` 取 dmid 列表 → 向本地服务 POST decisions → 按响应 `dmproto.filterSegment(data, drop, collapse)` 改写 → 交回播放器。
- URL 里解析 `oid`(=cid) 与 `pid`(=aid)；bvid 从页面 URL/localStorage 获取；解析不到 bvid 时直接放行（fail-open）。
- 本地服务不可达 / 超时（1500 ms）/ 解析异常 / 未知 bvid 缓存 → **原样放行**，UI 显示「本地服务未连接」；绝不阻断播放。
- collapse 文案：`【已折叠·{中文标签}】`，映射：information 信息 / humor 幽默 / emotional_resonance 共鸣 / irrelevant_repetition 无意义重复 / attack 攻击 / conflict 争论 / spoiler 剧透 / other 其他。
- 角标：右下角固定，`弹幕过滤 已隐藏 N · 折叠 M`（N/M 本次会话累计隐藏/折叠条数），旁边开关；点击打开面板。
- 面板：过滤条目列表（时间 mm:ss、文本、标签、分数、原因、3 个按钮：`显示这条`、`总是显示此类型`、`改标签`下拉）；设置区（每类 show/hide/collapse、min_confidence、密度开关、服务地址、清空本地偏好）。
- 偏好存 `localStorage["dmf.prefs.v1"]`，每次变更 POST `/api/v1/feedback` kind=prefs（失败忽略）；`显示这条` 发 policy_event（action=show_once）；`总是显示此类型` 改 per_type + 发 policy_event；`改标签` 发 label_correction 并本地覆盖立刻生效（下次 decisions 由服务端 override 生效）。
- 首次运行默认 prefs = §5 默认；开关关闭 = enabled=false，全部放行且不改写数据。
- 幂等/健壮：同一 seg 请求可能重复；按 (cid, segment_index) 缓存本轮决策，避免重复 POST；hook 只装一次；任何异常 try/catch 后调用原函数。
- 节点测试（`node --test extension/test/` 或 `node --test`）必须覆盖：dmproto parse/filter roundtrip（用 `tests/fixtures/seg_sample.bin`）、policy decide 规则、密度窗口、prefs 合并。浏览器内行为写进 `extension/README.md` 的手动测试清单。

## 7. CLI 新增

```bash
python3 -m danmaku_filter.cli precompute --bvid BV1GJ411x7h7 [--backend rules|llm] [--tags-dir data/tags] [--limit 0] [--context "..."]
python3 -m danmaku_filter.cli serve [--port 8765]      # 等价 python3 -m danmaku_filter.server
```
`precompute` 输出一行 JSON 摘要（同 `pipeline.precompute` 返回值）。

## 8. 验收命令

```bash
python3 -m unittest discover -s tests -v          # Python 单测全绿
node --test extension/test/                       # JS 单测全绿（userscript-dev 提供）
python3 -m danmaku_filter.cli precompute --bvid <BV> --backend rules
python3 -m danmaku_filter.server &                # 另开终端
curl -s -X POST 127.0.0.1:8765/api/v1/decisions -H 'Content-Type: application/json' \
  -d '{"bvid":"<BV>","dmids":["<dmid>"],"prefs":{}}'
```

## 9. 变更流程

1. 谁想改契约 → 先改本文件对应小节，写清原因；
2. `send_message` 通知 lead 与受影响方；
3. lead 更新 `docs/BACKLOG.md` 的 NEW REQ/决策记录；
4. 收到确认后再改代码。违反契约的代码在集成时由 lead 打回。


## 10. 实现偏差记录（集成时确认，均不改变对外语义）

| # | 契约 | 实际实现 | 处理 |
|---|---|---|---|
| 1 | §2 dmproto 函数名 `parse_segment / parse_element / filter_segment` | JS 版为 `parseSegment / parseElement / filterSegment`；JS `filterSegment` 返回 `{data, hidden, collapsed}`，Python 返回 tuple | 语义一致、仅命名/形状差异；JS 测试已覆盖同一 fixture |
| 2 | §5 prefs 无 `density.enabled` | JS prefs 多一个 `density.enabled`（面板密度开关）；JS `window_seconds` clamp 0.1–3600 且允许小数，Python 取 int | Python `normalize_prefs` 忽略未知键；服务端不受影响 |
| 3 | §5 密度语义「show 类弹幕不因密度被隐藏」 | 客户端实现：最终 action != hide 占可见位；超过 `max_visible` 时只对「服务端非 show 决策且 label 非 information/other」的条目按 `exceed` 升级 | 服务端只返回非 show 条目，纯 show 弹幕天然不参与密度；information/other 受保护 |
| 4 | §6 「拦截 seg.so 请求」 | XHR 路径为自持请求：不发送原请求，改用 `GM_xmlhttpRequest` 重拉同一 URL；`responseType=json/document` 时直接放行原请求 | Network 面板可能看到两次 seg.so，属预期；失败一律回退原 `send` |
| 5 | §2 cache 只有 `add_pending` | 额外加了 `cache.load_pending`；pending 文件形状 `{"bvid","dmids","updated"}`；`add_pending` 仅在新增 >0 时写盘（幂等 added=0） | miss 端点需要返回 pending 计数 |
| 6 | §4 `GET /api/v1/tags` 未规定缓存缺失 | 返回 `{"ok": true, "payload": null}` | 调试接口，不参与过滤 |
| 7 | §4 `/decisions` 的 `n` | `n` = 去重后请求条数；缓存缺失 found=false 且 miss=全部；>20000 去重后 → 400 | 客户端把「缺失/不返回」都当 show |
| 8 | §8 验收命令 `node --test extension/test/` | Node 24 把目录当入口模块报错，新增 `extension/test/index.js` 聚合入口 | `npm test`（`node --test test/`）正常；裸 `node --test` 会重复计数但全绿 |
| 9 | §2 pipeline 返回值 | `precompute` 返回 9 键摘要（bvid/aid/cid/segments/total/labeled/backend/model/path），写盘为原子 tmp+fsync+replace，失败不留半成品 | CLI `precompute` 原样打印该摘要 |
| 10 | §2 userscript 前置 | 示例缓存 `data/tags/BV1GJ411x7h7.json`（rules 后端 1407 条，随仓库保留）用于演示与手动测试 | `data/tags/` 不入 .gitignore，其余 `data/raw`、`data/private` 不入库 |

## 11. 验收结果快照（2026-10-21）

```
python3 -m unittest discover -s tests        # Ran 91 tests ... OK
node --test extension/test/                  # tests 40 / pass 40 / fail 0
node --check extension/dist/bili-danmaku-filter.user.js   # OK
node extension/build.mjs                     # sha256 稳定（重复构建一致）
python3 scripts/smoke_test.py                # PASS: 15 项端到端（health/decisions/miss/feedback/tags/400/404）
python3 -m danmaku_filter.cli precompute --bvid BV1GJ411x7h7   # 1 segment / 1407 条 → data/tags/BV1GJ411x7h7.json
```

浏览器内行为（hook 命中、面板、fail-open）按 `extension/README.md` 的 15 条手动测试清单验收（本环境无浏览器自动化）。

