# S2 controlled viewing study: analysis

Generated 2026-11-18T12:18:30Z from 6 questionnaire rows and 6 session logs.

## Data cleaning

Included: 6 blocks / 1 participants. Excluded: 0 blocks.

Blocks by condition: A=2, B=2, C=2

## Descriptives by condition

| outcome | A (n=2) | B (n=2) | C (n=2) |
|---|---|---|---|
| discomfort | 2.30 (SD 0.99) | 2.80 (SD 0.57) | 3.50 (SD 0.42) |
| coview | 3.00 (SD 0.00) | 3.50 (SD 1.18) | 3.17 (SD 2.12) |
| control | 2.83 (SD 0.71) | 3.00 (SD 0.94) | 3.00 (SD 0.94) |
| nasa | 4.25 (SD 0.82) | 3.33 (SD 0.24) | 3.83 (SD 0.24) |
| comp | 0.60 (SD 0.00) | 0.50 (SD 0.42) | 0.60 (SD 0.00) |
| explain | 3.00 (SD 0.00) | 3.25 (SD 0.35) | 3.00 (SD 0.00) |
| overall | 4.25 (SD 1.06) | 2.00 (SD 0.71) | 3.25 (SD 0.35) |
| fomo | 2.00 (SD 0.00) | 2.00 (SD 0.00) | 2.00 (SD 0.00) |

## Planned contrasts (paired Wilcoxon, Holm over 3) 

### discomfort

| contrast | n | mean diff | 95% CI | d | p / p_holm | test |
|---|---|---|---|---|---|---|
| C vs A | 1 | - | - | - | - | too few complete pairs |
| C vs B | 1 | - | - | - | - | too few complete pairs |
| B vs A | 1 | - | - | - | - | too few complete pairs |

### control

| contrast | n | mean diff | 95% CI | d | p / p_holm | test |
|---|---|---|---|---|---|---|
| C vs A | 1 | - | - | - | - | too few complete pairs |
| C vs B | 1 | - | - | - | - | too few complete pairs |
| B vs A | 1 | - | - | - | - | too few complete pairs |

### coview

| contrast | n | mean diff | 95% CI | d | p / p_holm | test |
|---|---|---|---|---|---|---|
| C vs A | 1 | - | - | - | - | too few complete pairs |
| C vs B | 1 | - | - | - | - | too few complete pairs |
| B vs A | 1 | - | - | - | - | too few complete pairs |

### fomo

| contrast | n | mean diff | 95% CI | d | p / p_holm | test |
|---|---|---|---|---|---|---|
| C vs A | 1 | - | - | - | - | too few complete pairs |
| C vs B | 1 | - | - | - | - | too few complete pairs |
| B vs A | 1 | - | - | - | - | too few complete pairs |

### nasa

| contrast | n | mean diff | 95% CI | d | p / p_holm | test |
|---|---|---|---|---|---|---|
| C vs A | 1 | - | - | - | - | too few complete pairs |
| C vs B | 1 | - | - | - | - | too few complete pairs |
| B vs A | 1 | - | - | - | - | too few complete pairs |

### comp

| contrast | n | mean diff | 95% CI | d | p / p_holm | test |
|---|---|---|---|---|---|---|
| C vs A | 1 | - | - | - | - | too few complete pairs |
| C vs B | 1 | - | - | - | - | too few complete pairs |
| B vs A | 1 | - | - | - | - | too few complete pairs |

### overall

| contrast | n | mean diff | 95% CI | d | p / p_holm | test |
|---|---|---|---|---|---|---|
| C vs A | 1 | - | - | - | - | too few complete pairs |
| C vs B | 1 | - | - | - | - | too few complete pairs |
| B vs A | 1 | - | - | - | - | too few complete pairs |

### explain

| contrast | n | mean diff | 95% CI | d | p / p_holm | test |
|---|---|---|---|---|---|---|
| C vs A | 1 | - | - | - | - | too few complete pairs |
| C vs B | 1 | - | - | - | - | too few complete pairs |
| B vs A | 1 | - | - | - | - | too few complete pairs |

## Mixed model

statsmodels/pandas not installed (ModuleNotFoundError); use the planned Wilcoxon contrasts

## RQ1: condition A, genre x density (exploratory)

exploratory: participants see only two videos per condition, genre x density is video-level

| genre | density | blocks | videos | discomfort | coview | control | fomo |
|---|---|---|---|---|---|---|---|
| entertainment | low | 1 | 1 | 1.60 | 3.00 | 3.33 | 2.00 |
| information | high | 1 | 1 | 3.00 | 3.00 | 2.33 | 2.00 |

## RQ2: latency and behaviour

| condition | segments | p50 ms | p95 ms | mean ms |
|---|---|---|---|---|
| A | 2 | 0.00 | 1.00 | 0.50 |
| B | 2 | 0.00 | 2.00 | 1.00 |
| C | 2 | 0.00 | 0.00 | 0.00 |
| overall | 6 | 0.00 | 2.00 | 0.50 |

Corrections: 0 (0.00 per 10 min), restores: 0, expands: 0, pref changes: 0, online segments: 1.000.

## RQ2: gold evaluation

n=120 accuracy=0.558 macro-F1=0.496 (skipped 0 rows).

| label | precision | recall | F1 | support |
|---|---|---|---|---|
| humor | 0.763 | 0.763 | 0.763 | 38 |
| other | 1.000 | 0.147 | 0.256 | 34 |
| information | 0.520 | 0.619 | 0.565 | 21 |
| emotional_resonance | 0.423 | 0.733 | 0.537 | 15 |
| attack | 0.667 | 1.000 | 0.800 | 6 |
| irrelevant_repetition | 0.111 | 0.250 | 0.154 | 4 |
| conflict | 0.250 | 1.000 | 0.400 | 2 |

## Double annotation

double-annotation file missing or empty: /home/alphayang/bili-tagging/data/study/gold_double.jsonl

- gold evaluation uses per-video models in data/study/models; skipped rows are counted
- Wilcoxon p-values use the normal approximation with tie correction; the bootstrap CI resamples participants

