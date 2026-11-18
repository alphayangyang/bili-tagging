# Pilot Coding Guide (1 page)

Short version of [../taxonomy.md](../taxonomy.md) for the pilot session. Code each
danmaku with **exactly one** label; use `other` when nothing fits and write why in `note`.

| id | one-line rule | typical cue |
|---|---|---|
| information | adds facts, sources, explanations, timestamps, translations | "BGM 出自…", "注意右下角", "CV 是…" |
| humor | a joke/pun/meme/witty remark meant to amuse | "导演：我谢谢你", "这波反向带货" |
| emotional_resonance | expresses or echoes emotion/empathy/agreement | "泪目了", "太真实了", "我也是" |
| irrelevant_repetition | low-information spam/repetition/flooding | "23333", "哈哈哈哈", "前排", "第一" |
| attack | hostile, insulting, demeaning a person/group | "这UP真丑", "滚吧", 人身攻击 |
| conflict | disagreement, argument, dispute over an opinion | "你懂什么就在这里说", "明明不是这样" |
| spoiler | reveals plot/ending/twist or a future event | "后面他会死", "结局是…" |
| other | none of the above, or genuinely ambiguous | 无语义、无法判断 |

## Tie-breakers (use in this order)

1. **Is it about the video content or about another commenter?** About a commenter → attack/conflict/humor, not information.
2. **Attack vs conflict**: attack targets a person/group with hostility; conflict disputes an opinion/claim without (mainly) insulting. If both, code **attack** (higher risk).
3. **Pure laughter/repetition** ("哈哈哈哈哈", "？？？", "2333"): `irrelevant_repetition`, even if it expresses amusement. A joke with content → `humor`.
4. **Emotion vs humor**: "笑死我了" as a reaction → emotional_resonance; a constructed joke/wordplay → humor.
5. **Information vs opinion**: verifiable fact/explanation → information; personal evaluation → other (or emotional_resonance if it is mainly feeling).
6. **Spoiler wins** when the comment reveals plot/ending, even if it is also emotional ("我哭了，他最后死了" → spoiler).
7. **Nothing fits or unsure** → `other` + note. Never invent a category.

## Session rules

- Work independently; do not discuss items with the other coder until both sheets are saved.
- Do not look at `data/annotation/sample_120.jsonl` (it contains model labels) and do not open the other coder file.
- Label what the comment does **in this video**, not whether you like it.
- Speed target: ~20 comments/minute; when stuck >20 s, choose `other` and note the reason.
- Record doubts in `note` — they are the data for revising the taxonomy.

## After coding

`python3 scripts/cohen_kappa.py --a data/annotation/coder_a.csv --b data/annotation/coder_b.csv`

κ ≥ 0.70 → freeze the definitions; κ < 0.70 → read the disagreement list together,
agree on 2–3 extra rules, recode only the disputed items, and report both κ values.
