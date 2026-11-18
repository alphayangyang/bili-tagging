# Danmaku Filtering: Methods and Results Section Design

Class X  |  Group ZonE  |  Zhuang Qian, Tian Muyuan

Note: replace "Class X" with the class number before submission.

## 1. Method

### 1.1 Design

We evaluate the prototype in a within-subject laboratory experiment with four conditions: no filtering (A), platform keyword blocking (B), the semantic prototype (C), and a sham (D) that shows the same panel, the same whole-pool loading step and the same folded list, but filters nothing. Every participant views six Bilibili videos in eight blocks of three to five minutes, two blocks per condition. For each condition one video is informational and one is entertainment, and for each pair one video keeps its original danmaku density while the other is thinned to 30 comments per minute. Because there are four conditions and six videos, every participant sees each video once and each condition twice; condition assignment and the order of conditions follow a Latin square with four rows, so that no condition is always experienced first. The design is therefore a 4 (condition) by 2 (genre) by 2 (density) fully crossed manipulation, with condition and density varied within participants and genre crossed with the video pairs. A session lasts about 55 minutes.

### 1.2 Participants and samples

Sixteen participants take part, and twenty are recruited to allow for dropouts, unusable sessions and withdrawals. Participants are students at the same university, at least 18 years old, watch Bilibili for at least three hours per week, use desktop Chrome or Edge, and read Chinese. We exclude members of the research team, anyone who has already seen three or more of the six stimuli (asked at recruitment and again after the session) and anyone who took part in earlier pilot sessions. Recruitment uses campus mailing lists and social media, so the sample is one of convenience and we report it as such.

With N = 16 and two blocks per condition, a paired comparison of condition means detects a standardized difference of d = 0.8 with power above .90 and d = 0.7 with power around .80, so the primary contrasts are powered for medium-to-large effects; smaller differences are reported with effect sizes and confidence intervals rather than as significant. Each participant receives course credit or 50 CNY. Before the session the experimenter explains that the plugin records danmaku identifiers, filtering actions and timestamps, that no screen or audio is recorded, and that the participant may stop at any time without losing compensation. Participants sign a consent form; the procedure was approved by the course instructor and follows the course ethics guidelines. Data are stored under participant codes (P01 to P20) and the mapping from codes to names is kept in a separate file that is destroyed after the course ends.

### 1.3 Materials and apparatus

Six videos were selected from Bilibili to cover three pairs, each pair containing one informational and one entertainment video and one high-density and one low-density cell (Table 1). All videos last between three and five minutes, are playable without a paid membership, contain at least 300 danmaku comments in the pool, and reach a peak of at least 150 comments per minute. The informational videos are explainers with verifiable facts (bismuth crystals, sleep duration, a financial worldview); the entertainment videos are a gaming rant, a freestyle debate and a reaction to Japanese viewers abandoning a game. Attack, conflict and spoiler comments occur across the entertainment videos, which is necessary for the prototype condition to have something to filter.

Table 1. Stimulus materials.
| Pair | Video (topic) | Genre | Density cell | Duration | Pool | Mean per min | Peak per min |
| P1 | BV1Ho4y1D7Wz (bismuth crystals) | Information | High | 186 s | 1,200 | 387 | 750 |
| P1 | BV18E1NByEYv (worst game network) | Entertainment | Low | 290 s | 1,200 | 248 | 690 |
| P2 | BV1VMRKBCEDp (sleep duration) | Information | Low | 214 s | 1,172 | 329 | 445 |
| P2 | BV1X7zGY4E45 (freestyle debate) | Entertainment | High | 194 s | 1,200 | 371 | 521 |
| P3 | BV1Z4RHYKEcB (financial worldview) | Information | High | 281 s | 725 | 155 | 225 |
| P3 | BV1CzW26vEXz (Japanese viewers quitting) | Entertainment | Low | 271 s | 1,119 | 248 | 470 |

The prototype is delivered as a Tampermonkey userscript (version 0.4.2) that runs on Bilibili video pages and communicates with a local Python service on 127.0.0.1:8765. When a page opens, the script collects the whole danmaku pool for the video once, scores every comment with a per-video linear classifier, and builds a single action table for the entire video. As the player requests danmaku segments, the script rewrites the segment bytes: hidden comments are removed, folded comments are removed from the stream and listed in the panel, and the remaining comments pass through unchanged. The classifier is a centroid model over hashed character n-grams trained offline from the 117 to 197 comments per video that a large language model labelled as teacher data. Ten labels are available: information, humour, emotional resonance, irrelevant repetition, attack, conflict, spoiler, political stance, inflammatory bait, and other. If the local service is unreachable the script fails open and shows danmaku unfiltered, and a DOM-level pass re-applies the current policy to comments already on screen so that preferences take effect within 300 ms.

### 1.4 Conditions

Table 2. Experimental conditions.
| Condition | What the participant sees | Script state |
| A: all-on | Every danmaku comment as Bilibili shows it; native keyword blocking switched off | Record only: never modifies the stream |
| B: platform-keyword | Comments matching five Bilibili blocked words are removed (剧透, 前方高能, 傻逼, 打卡, ???) | Record only: the platform removes, the script logs |
| C: prototype | Attack, conflict, inflammatory bait and irrelevant repetition hidden; spoilers folded (recoverable in the panel); everything else shown; confidence threshold 0.30 | Full filtering: whole-pool scoring, byte-level rewriting, panel |
| D: sham | The same panel, progress capsule and folded list as C, but every comment is shown and nothing is folded | Interface and whole-pool loading on, classification and rewriting off |

The sham condition separates the interface from the filtering. Comparing A with D measures what the panel, the loading step and the expectation of filtering do on their own; comparing C with D measures what semantic filtering adds beyond that interface. The density manipulation applies to all four conditions: high-density blocks keep the original stream, and low-density blocks are thinned to 30 comments per minute with fixed 10-second windows and a stable hash over comment identifiers, so that the same comments survive regardless of condition. Condition C uses the same thinned stream, which keeps the comparison between conditions fair. All materials, including the on-screen panel and the experimenter script, describe only what is visible and never name the condition.

### 1.5 Measures

Exposure and filtering performance (primary). For every block the script logs how many comments were shown, hidden, collapsed and thinned, together with the label and score of each comment. Because the pool is fixed and every comment is known before playback, these counts are exact rather than sampled. We define the unwanted set as attack, conflict, inflammatory bait, irrelevant repetition and spoiler, and the desired set as information, emotional resonance and humour. Two rates are computed per block: removal recall, the share of unwanted comments that the condition removed or folded, and the false-removal rate, the share of desired comments that it removed or folded. Comparing C with B isolates what semantics buys over literal keyword matching; comparing C with A gives the total effect of filtering.

Interaction burden. The logs also record every restore (a folded comment brought back), every label correction made in the panel, every panel opening and every preference change, per block. Low counts indicate that the filter matched the participant's preferences without requiring repair work.

System performance. The script records the decision latency of each segment (median and 95th percentile per block), the time needed to load and score the whole pool, the number of blocks that failed open, and the share of comments labelled by the local model rather than by the fallback rules.

Instrument validation. A reference set of 120 comments (20 per video, sampled at equal intervals across each video) was labelled by the first author using the annotation guide, then compared with the tagger's output. The comparison is made under a leave-reference-out protocol: every reference comment is removed from the tagger's training pool before the per-video models are re-fitted, so the figures are not inflated by memorised text. The tagger reproduces the reference dominant label on 52.5% of comments and reaches a macro-F1 of 0.350 (95% bootstrap CI [0.255, 0.430]); Table 3 shows the per-label breakdown. The weak categories bound the filtering gain we can expect in condition C and are reported as a limitation rather than treated as a result.

Table 3. Tagger validation on the 120-comment reference set.
| Label | Precision | Recall | F1 | Support |
| Humour | .64 | .64 | .64 | 47 |
| Information | .59 | .68 | .63 | 25 |
| Emotional resonance | .50 | .57 | .53 | 21 |
| Conflict | .43 | .38 | .40 | 8 |
| Irrelevant repetition | .25 | .25 | .25 | 4 |
| Other | .00 | .00 | .00 | 9 |
| Attack | .00 | .00 | .00 | 6 |

Note. Leave-reference-out evaluation, n = 120; dominant-label agreement = .525, macro-F1 = .350, weighted F1 = .509. Labels that do not occur in the reference set are omitted. The weak categories (other, attack, irrelevant repetition) bound the filtering gain expected in condition C and are treated as a limitation rather than a result.

Post-block ratings. After each block the participant completes a short five-item form on a 7-point scale: discomfort while watching (single item), felt control over what appeared (single item), overall evaluation of the block (single item), and expectancy (two items: whether the setting was useful, and whether they trusted it to hide what they did not want). The form takes under a minute and is the only self-report in the study; together with the logs it lets the sham condition be interpreted, because expectancy and control can rise from the interface alone.

Debrief notes. After the last block the experimenter asks three open questions: which comments they noticed, whether they wanted anything back, and whether anything surprised them. Answers are written down as short notes and analysed thematically.

### 1.6 Procedure

Sessions run individually in a quiet room. The experimenter first explains the study and obtains consent (5 minutes), then opens the browser, installs the userscript on a clean profile, checks the local service and runs one practice video that is not part of the stimuli (5 minutes). For condition B the five blocked words are entered in the Bilibili settings before the block and removed afterwards; for conditions C and D the experimenter confirms that the whole pool has loaded (the script shows a progress capsule and waits up to 2.5 seconds). The experimenter never names the condition and uses the same wording for every block. Each block then follows the same sequence: the participant watches the video for its full duration, completes the five-item rating form, the experimenter exports the session log with the script's export command, and the participant answers whether they had seen the video before. Blocks are separated by about one minute of rest. The order of blocks is read from the run sheet prepared for each participant number. After the eighth block the experimenter conducts the debrief (5 minutes), thanks the participant and records the compensation.

Figure 1. Session procedure. Each participant completes eight blocks; the order of conditions follows a Latin square and the density cell alternates within each video pair.

### 1.7 Analysis plan

All measures are aggregated to participant means before inference, and blocks are the unit of description. Six planned contrasts are tested: C versus A (total effect of filtering), C versus B (semantic versus keyword filtering), C versus D (semantic filtering beyond the sham interface), D versus A (the interface alone), D versus B and B versus A (platform baseline). All use two-tailed Wilcoxon signed-rank tests on participant means with Holm correction across the six contrasts. Effect sizes are Cohen's d_z for paired comparisons and Cliff's delta as a distribution-free check, each with 95% percentile bootstrap confidence intervals from 10,000 resamples. Hypotheses H1 to H4 are directional and tested as listed in Table 4; H5 is exploratory and reported descriptively with an interaction plot. The manipulation check compares rendered comments per minute between density cells within each condition. Missing or failed blocks are reported, and blocks with a failed pool load are excluded from filtering analyses but kept in the counts.

Statistical inference follows the conventions of the field. A two-tailed Wilcoxon signed-rank test compares the two conditions on participant means; the null hypothesis is that the median within-participant difference is zero, and a p value below .05 is read as evidence against that null, never as the probability that the hypothesis is true. Because six contrasts are tested, p values are Holm-adjusted: the smallest of the six is compared with .05/6, the next with .05/5, and so on down to .05, which keeps the family-wise error rate at .05. A non-significant contrast is reported as "no detectable difference", not as evidence of equivalence. Effect sizes are Cohen's d_z (mean difference divided by the standard deviation of the within-participant differences; 0.2, 0.5 and 0.8 are read as small, medium and large) and Cliff's delta as a distribution-free alternative, each accompanied by a 95% percentile bootstrap interval computed from 10,000 resamples of participants.

Table 4. Hypotheses, measures and tests.
| Hypothesis | Comparison | Measure | Test |
| H1: semantic filtering removes more unwanted comments than keyword blocking | C vs B | Removal recall | Wilcoxon signed-rank, one-tailed, d_z and bootstrap CI |
| H2: semantic filtering wrongly removes fewer desired comments than keyword blocking | C vs B | False-removal rate | Wilcoxon signed-rank, one-tailed |
| H3: filtering requires little repair work | C | Restores + corrections per block | Descriptive; median restores and corrections at or below 1 per block |
| H4: filtering is fast enough to be invisible | C | Segment p95 latency, pool load time, fail-open blocks | Descriptive against thresholds 50 ms, 3 s and 1% |
| H5 (exploratory): filtering benefit depends on density and genre | C vs A, C vs B by cell | Removal recall and false-removal rate | Descriptives and interaction plot |
| H6: the prototype's benefit exceeds the sham interface | C vs D | Discomfort, control, expectancy | Wilcoxon signed-rank, d_z and bootstrap CI |

## 2. Results (design)

The results section will contain five subsections. The tables below are shells: rows, columns and units are fixed now, and values will be entered after data collection. Each subsection ends with a reporting template so that the text can be written quickly and consistently.

### 2.1 Manipulation check and exposure

Table 5. Rendered comments per block, by condition and density cell (shell).
| Condition | Density cell | Comments rendered per min, M (SD) | Thinned comments, M (SD) | Blocks |
| A | High | __ (__) | 0 (0) | 16 |
| A | Low | __ (__) | __ (__) | 16 |
| B | High | __ (__) | 0 (0) | 16 |
| B | Low | __ (__) | __ (__) | 16 |
| C | High | __ (__) | 0 (0) | 16 |
| C | Low | __ (__) | __ (__) | 16 |
| D | High | __ (__) | 0 (0) | 16 |
| D | Low | __ (__) | __ (__) | 16 |

Template: "Thinning reduced the rendered stream from M = __ comments per minute (SD = __) in the high-density cells to M = __ (SD = __) in the low-density cells, a difference of __ comments per minute. The manipulation therefore held in all four conditions, which allows the density factor to be interpreted in the analyses that follow."

### 2.2 Filtering performance (H1 and H2)

Table 6. Removal recall and false-removal rate by condition (shell).
| Condition | Removal recall, % (95% CI) | False-removal rate, % (95% CI) | Folded but recoverable, % |
| A | __ (__, __) | __ (__, __) | 0 |
| B | __ (__, __) | __ (__, __) | 0 |
| C | __ (__, __) | __ (__, __) | __ |
| D | 0 (by construction) | 0 (by construction) | __ |

Figure 2 (planned). Stacked bars of shown, folded and hidden comments for conditions A, B, C and D, separately for the unwanted and the desired sets.

Template: "Condition C removed or folded __% of unwanted comments (95% CI [__, __]), compared with __% in condition B; the difference was V = __, p = __, d_z = __, supporting H1. Condition C removed __% of desired comments (95% CI [__, __]) against __% in condition B (V = __, p = __), so the gain in recall did not come at the cost of removing wanted content, consistent with H2. Against the unfiltered baseline, condition A removed 0% of unwanted comments by construction, and the contrast C versus A therefore quantifies the total effect of filtering."

### 2.3 Interaction burden (H3)

Table 7. Repair behaviour in condition C per block (shell).
| Measure | Median | Maximum | Threshold | Blocks above threshold |
| Restores | __ | __ | 1 per block | __ |
| Label corrections | __ | __ | 1 per block | __ |
| Panel openings | __ | __ | 3 per block | __ |
| Preference changes | __ | __ | 1 per block | __ |

Template: "Participants restored M = __ comments per block (range __ to __) and corrected M = __ labels, both below the pre-registered thresholds; H3 is therefore supported, and the filter matched most participants' preferences without requiring repair work."

### 2.4 System performance (H4)

Table 8. Latency and reliability by condition (shell).
| Condition | Segment decision p50, ms | Segment decision p95, ms | Pool load, s | Fail-open blocks | Comments scored locally, % |
| A | __ | __ | __ | __ | 0 |
| B | __ | __ | __ | __ | 0 |
| C | __ | __ | __ | __ | __ |
| D | __ | __ | __ | __ | 0 |

Template: "In condition C the median segment decision took __ ms and the 95th percentile __ ms, the whole pool loaded in M = __ s (SD = __), __ blocks failed open and __% of comments were scored by the local model. All values are inside the thresholds set in H4, so filtering was fast enough to be invisible during playback."

### 2.5 Exploratory analysis and debrief themes

Figure 4 (planned). Removal recall for C versus A, C versus B and C versus D by density cell and genre, with 95% bootstrap confidence intervals.

The exploratory analysis asks whether the benefit of filtering is larger when the stream is dense and whether it differs between informational and entertainment videos; because N = 16 and the cells contain two blocks per participant, the analysis is descriptive and will be reported as an interaction plot with confidence intervals rather than as a formal test. The debrief notes will be grouped into three themes: comments participants wanted removed, comments they wanted back, and comments they did not notice at all. Quotations will be translated into English and reported with participant codes.

Reporting conventions: p values are reported to three decimals, effect sizes with 95% bootstrap confidence intervals, alpha is .05 and the six planned contrasts are Holm-corrected; all tests are two-tailed except the directional tests specified in Table 4. When a result contradicts a hypothesis we report the direction, the effect size and the interval rather than only the p value.

### 2.6 Simulated dry run (illustrative only)

Before recruiting, the full pipeline was exercised with eight simulated participants: LLM agents, each given a distinct viewer persona, that read the block materials (video facts, two screenshots, the comment samples and the visible counts) and completed the same rating form. They did not watch the videos, and the materials never named the condition, so the agents could not infer whether they were in A, B, C or D. Their data are labelled simulated everywhere, are analysed with the identical script, and are reported here only to show what the tables above will look like; they are not evidence for or against H1 to H6.

Figure 3. Simulated condition means for discomfort, control and overall (1-7, eight simulated participants, 48 blocks), with 95% participant-level bootstrap intervals.

Table 9. Simulated dry run, condition means (1-7, eight agents, 48 blocks in total).
| Measure | A all-on (n = 12) | B keyword (n = 12) | C prototype (n = 12) | D sham (n = 12) |
| Discomfort | 3.00 (0.89) | 3.03 (0.92) | 2.95 (0.92) | 3.13 (1.05) |
| Co-viewing | 4.03 (1.02) | 4.00 (1.11) | 3.75 (1.17) | 3.97 (1.02) |
| Control | 2.42 (0.62) | 2.33 (0.57) | 3.75 (1.03) | 3.28 (0.66) |
| FOMO | 3.67 (1.30) | 3.58 (0.90) | 3.67 (1.07) | 3.50 (1.24) |
| NASA-TLX load | 3.01 (0.42) | 3.22 (0.60) | 3.26 (0.60) | 3.08 (0.48) |
| Comprehension (self-rated) | 4.28 (0.72) | 4.48 (0.64) | 4.30 (0.56) | 4.40 (0.57) |
| Overall | 3.92 (0.97) | 3.96 (1.18) | 4.04 (1.05) | 3.79 (1.05) |
| Expectancy and trust | 2.12 (0.53) | 2.17 (0.61) | 3.58 (1.15) | 2.62 (0.61) |

The most instructive result is a null one. In an earlier, unblinded dry run with six agents and three conditions, condition C had clearly lower discomfort than A (difference -1.02, 95% CI [-1.90, -0.03]); once the materials stopped naming the condition and a sham was added, that difference disappeared (C 2.95 versus A 3.00, all six Holm-adjusted contrasts p = 1.000). What remained is a graded pattern in control and expectancy: C 3.75 and 3.58, D 3.28 and 2.62, A and B below 2.5. Participants therefore appear to respond to the interface and to the fact that something is being removed, not to the semantic quality of the removal; with eight agents the contrasts are directional (C versus A control p = .025 raw, .148 adjusted; C versus A expectancy p = .012 raw, .070 adjusted) but not significant after correction, which is expected at this sample size.

The latency table in 2.4 is populated from the instrumented preflight rather than from the simulation (C: segment p50 1 ms, p95 31 ms, because the pool is served locally). The dry run is reported in full in docs/study/results-simulated.md, together with the design bugs it exposed and the limitation that the low-density thinning is not shown in the panel counts.

## 3. References

Chen, Y., Gao, Q., & Rau, P.-L. P. (2017). Watching a movie alone yet together: Understanding reasons for watching Danmaku videos. International Journal of Human–Computer Interaction, 33(9), 731–743. https://doi.org/10.1080/10447318.2017.1282187

He, C., He, L., Lu, T., & Li, B. (2021). Beyond entertainment: Unpacking danmaku and comments' role of information sharing and sentiment expression in online crisis videos. Proceedings of the ACM on Human–Computer Interaction, 5(CSCW2), 1–27. https://doi.org/10.1145/3479555

Jhaver, S., Zhang, A. Q., Chen, Q. Z., Natarajan, N., Wang, R., & Zhang, A. X. (2023). Personalizing content moderation on social media: User perspectives on moderation choices, interface design, and labor. Proceedings of the ACM on Human–Computer Interaction, 7(CSCW2), 1–33. https://doi.org/10.1145/3610080

Mu, D., Zou, M., & Chen, Y. (2025). How do different cognitive styles learners deal with the bullet screen interruption in instructional videos? An eye-tracking study. BMC Psychology, 13(1). https://doi.org/10.1186/s40359-025-03182-1

Wang, L., Yurechko, K., Dani, P., Chen, Q. Z., & Zhang, A. X. (2025). End user authoring of personalized content classifiers: Comparing example labeling, rule writing, and LLM prompting. In Proceedings of the 2025 CHI Conference on Human Factors in Computing Systems (pp. 1–21). ACM. https://doi.org/10.1145/3706598.3713691

Wu, Q., Sang, Y., Zhang, S., & Huang, Y. (2018). Danmaku vs. forum comments: Understanding user participation and knowledge sharing in online videos. In Proceedings of the 2018 ACM Conference on Supporting Groupwork (pp. 209–218). ACM. https://doi.org/10.1145/3148330.3148344
