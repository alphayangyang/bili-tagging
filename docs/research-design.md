# Research Objectives, Questions, and Design (draft v1)

> Deliverable for the 4 Nov submission (revised objectives + questions/hypotheses +
> pilot study + research design). Numbers marked `[pilot]` are filled from
> `docs/pilot/report-template.md` after the pilot sessions.

## 1. Revised research objectives

This project examines how the **type and volume** of Bilibili danmaku shape the
viewing experience, and whether a **viewer-side, explainable, low-burden filter**
can improve that experience without removing the comments viewers value. Three
objectives follow:

1. Characterise the relationship between comment type, comment volume, and
   viewer outcomes (discomfort, perceived control, FOMO, co-viewing, comprehension).
2. Build and evaluate a hybrid (rule + LLM, human-in-the-loop) classifier that
   labels Chinese danmaku by social function accurately and quickly enough for
   real-time filtering.
3. Evaluate whether personalised filtering improves experience and reduces the
   burden reported for platform-level moderation tools [Jhaver et al., 2023].

## 2. Research questions and hypotheses

**RQ1. How do comment type and comment volume shape viewers experience?**

Aggressive and spoiler comments are expected to raise discomfort, while
information, humour and emotional resonance support co-viewing. Volume is
expected to amplify both effects (more good comments help, more hostile comments
hurt) and to interact with type.

- H1a: conditions with attack/conflict/spoiler comments score higher on discomfort
  than the same video without them.
- H1b: information/humour/emotional-resonance comments improve co-viewing and
  perceived value compared with no comments.
- H1c: the effect of comment type is stronger at high density than at low density.

**RQ2. Can a hybrid classifier assign social-function labels to Chinese danmaku
accurately enough to drive filtering?**

- H2: the hybrid classifier reaches macro-F1 >= 0.70 `[pilot]` on a human-annotated
  gold set, with p50 latency low enough for offline precomputation (no per-comment
  LLM call at runtime). Spoiler detection is expected to be the weakest class
  because it depends on video context.

**RQ3. Does personalised, category-based filtering improve the viewing experience
and reduce user burden compared with existing platform controls?**

- H3a: the prototype reduces discomfort and increases perceived control relative to
  the all-on and platform-keyword conditions.
- H3b: setup time and correction count stay low (target: <= 3 min setup, <= 1
  correction per 10 min of video) `[pilot]`.
- H3c: the prototype does not reduce comprehension of information-type videos.
- H3d: hiding behaviour is explainable: participants can state why a comment was
  hidden, and FOMO remains a concern to monitor rather than a solved problem.

## 3. Design overview

Three linked studies:

| Study | Type | Sample | Main output |
|---|---|---|---|
| S1 Annotation + classifier evaluation | Offline | 600-1,000 comments from 6 videos; 2-3 coders | gold set, per-class P/R/F1, latency |
| S2 Controlled viewing experiment | Within-subject, 3 filter conditions x 2 video genres | 30 participants (power analysis below) | RQ1 + RQ3 effects |
| S3 Short deployment / interview (optional) | 1-week field trial with 5-8 participants | 5-8 | ecological validity, failure modes |

**S2 conditions** (counterbalanced order, Latin square): (a) all comments on,
(b) platform keyword filter on, (c) our social-function filter (attack, conflict,
irrelevant repetition hidden; spoiler collapsed with a click; information, humour,
emotional resonance shown).

**S2 stimuli**: 6 short videos (3-5 min), 3 information-type and 3
entertainment-type, each with its real danmaku stream. Density is manipulated by
sampling the stream to low (~30 comments/min) and high (~150 comments/min) levels,
crossed with condition where feasible.

**Participants and power**: repeated-measures design, 3 conditions, alpha = .05,
power = .80, assumed medium effect (f = 0.25) -> ~24 participants; recruit 30 to
allow counterbalancing and attrition. Inclusion: regular Bilibili viewers (>= 3
hours/week), desktop browser, normal or corrected vision.

## 4. Measures

| RQ | Construct | Measure | When |
|---|---|---|---|
| RQ1 | Discomfort | 5-item scale adapted from moderation literature | after each video |
| RQ1 | Co-viewing / belonging | 3-item scale | after each video |
| RQ1 | Comprehension | 5 multiple-choice + free recall (information videos) | after video |
| RQ2 | Label quality | per-class precision/recall/F1, macro-F1, Cohen kappa | offline |
| RQ2 | Latency | p50/p95 ms per comment, precompute time per video | offline logs |
| RQ3 | Perceived control | 3-item scale | after each condition |
| RQ3 | FOMO | single item + open question | after each condition |
| RQ3 | Burden | setup time, correction count, NASA-TLX (6 subscales) | logs + after condition |
| RQ3 | Explainability | can the participant justify each hidden comment (yes/no) | after condition |

## 5. Procedure (S2, ~60 min per participant)

1. Consent, demographics, baseline Bilibili habits (5 min).
2. Practice video with the prototype, think-aloud (5 min).
3. Six video blocks: 3 conditions x 2 videos, counterbalanced; after each block,
   discomfort/co-viewing/control/FOMO items and, for information videos, the
   comprehension test (40 min).
4. Post-session interview: surprises, disagreements with decisions, would you keep
   it (10 min).

## 6. Analysis plan

- RQ1/RQ3: linear mixed-effects models with condition, density and genre as fixed
  effects, random intercepts for participant and video; planned contrasts against
  the all-on baseline. Non-normal scales analysed with aligned-rank or ordinal
  models, reported with effect sizes and 95% CIs.
- RQ2: per-class precision/recall/F1 with bootstrap CIs; macro-F1 against the 0.70
  threshold; kappa for coding reliability; latency distribution.
- Qualitative: thematic analysis of interviews and correction notes (two coders,
  codebook from taxonomy + emergent themes).

## 7. Ethics and data

Public danmaku only; no usernames or user ids stored; screen recordings optional
and deleted after coding; participants can stop at any time. The prototype fails
open (comments render unchanged if anything breaks), which is also a safety
property to verify in the pilot.

## 8. Pilot-informed revisions (fill after pilot)

- Coding guide: `[pilot]` rules added after kappa < 0.70 on [label pair].
- Task length: `[pilot]` videos shortened to 10 min if fatigue appears.
- Sample: `[pilot]` final N, number of videos per condition.
- Measures: `[pilot]` dropped/rewritten items.

## 9. Threats to validity

- Novelty effect for the prototype; mitigate with practice block and a use week (S3).
- Danmaku streams differ across videos; treat video as random effect and use 6 videos.
- Single-site (Bilibili) and Chinese-language sample limits generalisation.
- Classifier errors may hide wanted comments; we log corrections and report them as
  a burden/trust measure rather than hiding the errors.
