# 插件开发计划 v1

> 目标：把现有的 Python 分类原型变成**可演示、可评测**的 B 站网页端个性化弹幕过滤器。
> 前提：只看本机渲染，不修改平台数据；分类 ≠ 审核（见 [reconciliation.md](reconciliation.md)）。

## 0. 一句话范围

- **做**：B 站 PC 网页视频页；离线预计算 7 类标签 → 运行时按用户偏好 show / hide / collapse → 每条过滤可解释 → 用户可纠正 → 纠正回流。
- **不做（MVP 阶段）**：直播、手机端、多用户共享缓存、自定义标签、自动删除/审核。
- **技术底线**：运行时**不逐条调 LLM**（标签离线算好、本地缓存）；模型/服务不可用时保持原样渲染（fail-open）。

## 1. 现状

已有（可用）：

- `danmaku_filter/`：7 类 taxonomy、XML 弹幕拉取、rules / LLM 两个分类后端、FeedbackStore 覆盖、P/R/F1、CLI；9 个 unittest 通过。
- `docs/taxonomy.md`、`docs/design/reconciliation.md`：7 类定义、架构与待办。
- 环境：python3、node v24（可跑 JS 侧单测）；**没有** playwright / 浏览器自动化。

缺：

- 多标签中间表示（`{tag: score}`）、policy 层、密度控制；
- 本地服务 + 标签缓存格式；
- userscript（拦截、渲染决策、解释/纠正 UI）；
- 评测数据（gold 集）与标注工具；
- LLM prompt 注入防护（弹幕文本不可信）。

## 2. 今天探测到的事实（2026-10-21）

这些直接决定架构，先记下来：

| # | 发现 | 影响 |
|---|---|---|
| 1 | 播放器实际用的弹幕源是 `GET https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid={cid}&pid={aid}&segment_index={i}`，返回**裸 protobuf**，匿名请求（UA + Referer）即可 200 | 预计算和运行时都应以 seg.so 为准 |
| 2 | 分段 = 360 s：探测 2682 s 视频，seg1 progress 0–359820 ms、seg2 360416–719944、seg3 720175–1079665；越界段返回**39 字节空 protobuf**，老视频偶发 HTTP 304 + `bili-status-code: -304` | 分段数 = ceil(duration/360)，遇空/304 即停；需退避重试 |
| 3 | seg.so 元素 wire 格式已解出：顶层 field 1 = DanmakuElem；内部 1=dmid(varint)、2=progress(ms)、3=mode、4=fontsize、5=color、6=midHash、7=content(str)、8=ctime、9=weight、12=idStr、26=cid、27=type。通用 wire 解析 + 重编码可行（删 1 条后重建 163252→1405 条，仍可解析） | 可以只删/改整条 elem，保留未知字段，不需要完整 .proto |
| 4 | 旧接口 `/x/v1/dm/list.so`（现有 fetch.py 用的）**最多 1200 条且有采样**：213 s 测试视频 seg 池 1406 条，list.so 1200 条，id 交集仅 54 | **不能**用 list.so 建标签缓存，否则运行时大量 miss |
| 5 | 高峰视频（40k 弹幕统计）单分段实际只回 ~700–800 条，说明服务端有池/采样 | 运行时必然有 miss → 需要「未见弹幕先原样显示 + 异步补标」 |

## 3. 架构

```text
离线（python，随便什么时候跑）
  bvid → aid/cid/duration
       → seg.so segment 1..N（360s/段，遇空即停，退避重试）
       → 清洗（去重、去空、保留 dmid）
       → LLM + 规则 → 多标签 {tag: score} + evidence
       → data/tags/<bvid>.json（按 dmid 索引，含 text 便于解释/审计）

本地服务（python，127.0.0.1:8765，仅本机）
  GET  /api/v1/tags?bvid=&dmids=   → 批量查标签（含 override）
  POST /api/v1/miss                → 记录运行时没见过的 dmid（异步补标）
  GET  /api/v1/policy?bvid=        → 用户偏好
  POST /api/v1/feedback            → 双通道事件：policy_event / label_correction

运行时（Tampermonkey userscript，document-start）
  页面播放器请求 seg.so
    → hook XHR / fetch，拿到原始 protobuf
    → 查本地服务拿 tags
    → policy.decide(tags, prefs, state) → show | hide | collapse
    → 删除 / 替换(折叠文案) 对应 elem，重编码回给播放器
    → 播放器原生渲染（canvas 也无需处理 DOM）
    → 悬浮角标「已隐藏 N / 折叠 M」+ 过滤清单面板（原因、标签、分数、纠正按钮）
    → 纠正 → POST feedback → 覆盖立即生效
```

**关键取舍**：不碰 DOM、不自己画弹幕，只改播放器收到的数据；这样播放器换 canvas/DOM 渲染都不影响，且滚动/透明度/屏蔽词等原生行为全部保留。

## 4. 数据格式

`data/tags/<bvid>.json`（schema v2）：

```json
{
  "schema": 2,
  "bvid": "BV1...",
  "aid": 0, "cid": 0,
  "created": "2026-10-21T00:00:00Z",
  "model": "deepseek-flash",
  "segments": 3,
  "items": {
    "34576873522987015": {
      "text": "80年代迪厅必放金曲",
      "tags": {"information": 0.12, "humor": 0.05, "emotional_resonance": 0.61},
      "dominant": "emotional_resonance",
      "evidence": "nostalgic praise",
      "source": "llm",
      "overrides": {"dominant": "humor", "by": "user", "at": "..."}
    }
  }
}
```

- 标注 gold set 仍用**单主标签**（κ / P-R-F1）；运行时用多标签。
- `overrides` 由用户纠正写入，决策时优先级最高。

## 5. policy 层（纯函数，先写测试）

```python
decide(tags, prefs, state) -> Decision(action, label, reason, score)
```

规则顺序（先命中先返回）：

1. 用户 override / 「总是显示」「总是隐藏」 → 直接定；
2. 该类型偏好为 `show` → show；
3. 置信度 < `min_confidence`（默认 0.6）→ show（安全，宁可漏杀）；
4. 命中该类型的 hide/collapse 偏好 → hide / collapse；
5. 密度控制：最近 10 s 已显示超上限 → collapse 或 hide（按优先级）；
6. 默认 show。

密度控制器只从**已有**弹幕里选，不创造内容；重复弹幕按规范化文本合并。

## 6. 里程碑

| M | 内容 | 产出（写入范围） | 验收 | 估时 |
|---|---|---|---|---|
| M0 | 数据源改造 + protobuf 编解码 | `danmaku_filter/dmproto.py`、改 `fetch.py`、`tests/test_dmproto.py` | seg.so 全分段能拉、解码 dmid 与原文、删条目重编码后仍可解析；测试绿 | 0.5–1 d |
| M1 | 多标签 schema + policy 层 + 单测 | `types.py`、`classify/*`、`metrics.py`、`policy.py`、`tests/test_policy.py` | 表格化 12 条 policy 用例全过；LLM 输出 schema 校验 + 注入防护 | 1–2 d |
| M2 | 本地服务 + 标签缓存 + `precompute` | `server.py`、`cache.py`、`cli.py` | curl 往返通；1000 个 dmid 查询 < 20 ms；缓存文件可离线复用 | 1 d |
| M3 | userscript MVP（风险最高） | `extension/bili-danmaku-filter.user.js`、`extension/dmproto.js` | 真实视频页：attack/spoiler 默认隐藏；角标数 = 服务端决策数；关掉开关恢复原样；播放不卡 | 2–3 d |
| M4 | 解释 + 纠正 + 双通道反馈 | `feedback.py`、`server.py`、userscript 面板 | 纠正后立即生效、刷新仍在；policy 事件与 label 纠正分开落盘 | 1–2 d |
| M5 | 密度控制 + 偏好面板 | `density.py`、userscript UI | 密度上限生效；偏好可存可读；日志可用于研究分析 | 1 d |
| M6 | 评测与演示 | `eval/`、标注工具、`docs/design/user-study.md` | gold 集 P/R/F1 + κ；延迟 p50/p95；demo 录屏 + 安装说明 | 2–3 d |

合计约 **8–12 个工作日**（不含正式用户研究）。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 播放器升级 / hook 失效 | 只依赖 seg.so 数据格式；hook 失败时 **fail-open**（不拦不改）；userscript 里带版本探测与日志 |
| canvas 渲染导致 DOM 方案不可行 | 已选网络层拦截，不碰 DOM |
| list.so 采样造成漏标 | 预计算改用 seg.so；运行时 miss 记录 + 异步补标 |
| 服务端 304 / 限流 | 退避重试、单次运行限速；必要时让用户提供浏览器 cookie 或改用页面内 capture |
| LLM 成本/延迟 | 每视频一次批量分类 + 缓存；运行时零调用；只对 miss 的少量 id 补标 |
| 弹幕文本是 untrusted input | prompt 中放数据区并声明不执行指令；输出 JSON schema 校验；标签白名单 |
| 隐私 | 全部本地，不上传视频/账号信息；标签缓存可清空 |
| 伦理 | 不修改 B 站数据，仅本机渲染；演示/研究时说明 |
| 标注主观性 | 双人标注 pilot，Cohen's κ ≥ 0.7 才继续 |

## 8. 需要用户拍板

1. **形态**：先 Tampermonkey userscript（推荐，迭代快），还是直接 Chrome MV3 扩展？
2. **场景**：只做视频页，直播以后再说？（推荐）
3. **补标策略**：运行时未见弹幕先原样显示、后台异步补标——接受这个「首次略漏」的取舍吗？
4. **开工**：M0 可完全离线做（不需要浏览器），要不要现在开始？
5. 是否要拉 teammate 并行：M1 之后可并行「标注工具 + 评测」与「服务/前端」，需要的话我按写作用域拆任务。

## 9. 下一步（默认动作）

先做 M0：`dmproto.py` + `fetch.py` 改成 seg.so 数据源 + 测试。它不改运行时、不依赖浏览器，做完就能验证「预计算得到的 dmid 能覆盖播放器实际拿到的弹幕池」这个最关键假设。
