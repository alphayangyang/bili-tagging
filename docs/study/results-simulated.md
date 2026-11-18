# 模拟运行结果（S2 dry run，AI 被试，非人类数据）

> 状态：2026-11-19。数据来自 6 个 LLM 子智能体模拟被试（participant-p01 … p06），每人的每块作答见
> `data/study/agents/out/`，组装与分析的输入见 `data/study/questionnaire.csv` 与 `data/study/raw/`。
> **这不是人类被试数据**，只能用于管线验证、设计检查与 IMR 的示意性结果；对外提交时必须标注 simulated。

## 1. 做了什么

1. 修正拉丁方：6 名被试 × 6 块 = 36 个 block，每名被试看全部 6 支视频一次，每条件 2 块；
   每支视频在 A/B/C 各恰好出现 2 次（改前 BV1Ho4y1D7Wz 在 A 出现 4 次、C 为 0）。
2. 生成每块材料：视频事实、两帧截图（30%、75%）、整池条数与低密度抽稀后条数、
   条件实际显示/隐藏/折叠计数、14 条显示样例、8 条折叠样例、6 条隐藏样例。
3. 6 个 LLM 被试按手册作答：每块 7 点量表（不适感 5、共看 3、控制 3、FOMO 1、NASA-TLX 6、
   理解 5、可解释 2、总体 2）+ debrief 三问 + 交互行为（开面板/展开/恢复/改偏好）。
4. 组装成 `questionnaire.csv`（36 行）+ 36 个 session JSON，跑 `scripts/study/analyze.py --bootstrap 5000`。

## 2. 描述统计（条件均值，1–7）

| 量表 | A all-on | B platform keyword | C prototype |
|---|---|---|---|
| 不适感 discomfort | 3.65 (SD 1.07) | 3.43 (0.93) | **2.63 (0.69)** |
| 共看感 co-viewing | 4.25 | 4.06 | 4.22 |
| 控制感 control | 2.67 | 2.92 | **4.89** |
| FOMO | 4.08 | 4.00 | 4.42 |
| NASA-TLX 负荷 | 3.15 | 3.14 | 2.89 |
| 理解（自评） | 4.18 | 4.15 | 4.28 |
| 总体评价 | 3.71 | 3.88 | **4.88** |
| 可解释性（仅 C） | — | — | 5.29 |

图：`submission/figures/imr-figure2-simulated.png`（三个量表的三条件均值与 95% bootstrap 区间，
由 `python3 scripts/study/agents/make_figure.py` 从 questionnaire.csv 生成，已嵌入 IMR docx 的 Figure 2）。

## 3. 计划对比（被试均值，配对 Wilcoxon + Holm，n = 6）

| 对比 | 差值 [95% bootstrap CI] | Cohen's d_z | p | p(Holm) |
|---|---|---|---|---|
| C − A 不适感 | −1.02 [−1.90, −0.03] | −0.79 | .115 | .344 |
| C − B 不适感 | −0.80 | — | .116 | — |
| C − A 控制感 | +2.22 [+1.11, +3.28] | +1.50 | .043 | .129 |
| C − B 控制感 | +1.97 | — | .043 | .129 |
| B − A 控制感 | +0.25 | — | .066 | — |
| C − A 总体 | +1.17 | — | .225 | — |

方向都与假设一致（C 不适感最低、控制感最高、总体最高），但 n = 6 时 Wilcoxon 的最小可能 p ≈ .031，
经 Holm 校正后没有对比达到 .05，区间也只是勉强不跨 0。这正是「模拟被试不能当证据」的具体表现：
效应量大而样本小。

共看感、NASA-TLX、理解三个量表三条件之间没有可分辨差异（全部 p > .1）。

## 4. RQ1（仅 A 条件的 genre × density 探索，每格 2–4 块）

| 类型 | 密度 | 块数 | 不适感 | 共看感 | 控制感 | FOMO |
|---|---|---|---|---|---|---|
| 娱乐 | high | 2 | 3.60 | 5.50 | 2.50 | 4.50 |
| 娱乐 | low | 4 | 3.80 | 3.42 | 2.75 | 4.00 |
| 信息 | high | 4 | 4.00 | 3.75 | 2.67 | 3.75 |
| 信息 | low | 2 | 2.70 | 5.67 | 2.67 | 4.50 |

没有稳定模式，且与「密度越高越不适」的直觉相反（娱乐 low 反而 3.80 > high 3.60）。原因很可能是
LLM 被试读的是截图与样例而不是连续观看，密度操纵（30 条/分 vs 原样）对它们几乎是文字信息。
**这条 RQ1 完全不能用**，必须真人。

## 5. RQ2（系统侧，来自 preflight 实测，不是模拟出来的）

| 条件 | 段数 | 段决策 p50 | p95 | 均值 |
|---|---|---|---|---|
| A all-on | 12 | 85 ms | 135 ms | 72.2 ms |
| B keyword | 12 | 78 ms | 168 ms | 92.5 ms |
| C prototype | 12 | 1 ms | 31 ms | 8.5 ms |
| 总体 | 36 | 31 ms | 163 ms | 57.7 ms |

C 的段延迟接近 0，因为整池在开播前已抓取并改写，段请求直接由池内字节供应；
代价是每块开播前 0.2–3.2 s 的整池加载（材料里记录了该值，analyze.py 的延迟表不含它）。
交互行为：改标签 0 次、恢复显示 1 次、打开面板 2 次、改偏好 6 次（6 人 × 6 块）。

## 6. 标注器效度（去泄漏口径）

120 条人工金标（`data/study/gold_v2.jsonl`，`other` 已按「必须有功能才给低分」重判）上，
在**剔除金标文本**的训练集上重训后（`scripts/f1_eval.py --labeled data/study/teacher_v4.jsonl
--gold data/study/gold_v2.jsonl`）：

- dominant 一致率 **0.525**，macro-F1 **0.350**（95% CI [0.255, 0.430]），weighted F1 0.509，家族级 0.437；
- 逐标签 F1：information .615、humor .431、conflict .364、emotional_resonance .250、
  irrelevant_repetition .250、other .177、attack 0（金标 6 条全未召回）；
- 二分类「该不该折叠/隐藏」：precision 0.38、recall 0.17、F1 0.286。

注意：analyze.py 的 RQ2 gold 表读的是 `data/study/models/` 里随包发布的模型，它们在训练池与金标有重叠，
数字偏高（.558 / .496）。IMR 里引用的是上面这组**去泄漏**数字。

## 7. 结论（能给课程报告用的部分）

1. 管线通了：材料 → 作答 → 组装 → 分析 → 表/图，可以一键重建（见 agents/README.md 的复现命令）。
2. 三个真实 bug 被这条管线揪出来（拉丁方不平衡、familiar 编码、延迟匹配），都已修。
3. 模拟数据的方向与假设一致，但**不能当证据**：n=6、被试不真看视频、评分被材料里的计数锚定。
4. 真实验只需要换输入：真人填 `data/study/questionnaire.csv` + 真人 session JSON，
   `analyze.py` 不用改；N=16 时 Wilcoxon 才可能达到显著。

## 8. 复现

~~~bash
python3 scripts/study/agents/build_materials.py   # 36 块材料（先 rm -rf data/study/agents/blocks）
python3 scripts/study/agents/assemble.py          # questionnaire.csv + raw/*.json
python3 scripts/study/analyze.py --bootstrap 5000 # analysis.json + analysis.md
python3 scripts/f1_eval.py --labeled data/study/teacher_v4.jsonl --gold data/study/gold_v2.jsonl \
  --bootstrap 1000 --out data/experiments/results/f1_leakfree_gold2.json
~~~

## 9. 局限（写进论文限制段）

- 被试是 LLM 智能体，不是人：没有真实视觉注意、没有真实情绪反应，量表锚定依赖文字材料。
- 6 个人格是人为设定的六个点，方差比真人小，效应量不可外推。
- 理解测验退化为自评（comp 1–5 为子项自评），不能替代内容 MCQ。
- 条件 C 的体验差异部分来自材料中显式报告的「挡住了多少条」，存在需求特征。
- 标注器去泄漏后 macro-F1 0.350，意味着 C 条件的过滤本身有相当多误判，真人实验里要一并报告。