"""Build "Group + Introduction + pilot study" (Times New Roman 11, double spaced, justified).

Usage:
    python3 scripts/build_pilot_doc.py
Outputs:
    submission/ZonE + Introduction + pilot study.docx
    submission/figures/figure1-design.png
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt

REPO = Path(__file__).resolve().parents[1]
SUBMISSION = REPO / "submission"
FIG_DIR = SUBMISSION / "figures"
TAGS_FILE = REPO / "data" / "tags" / "BV1GJ411x7h7.json"
AUDIT_FILE = Path("/tmp/audit_sample.json")

TITLE = ("Filtering Danmaku: How Comment Type and Volume Shape Viewer Experience "
         "and How a Viewer-Side Filter Can Help")
GROUP_LINE = "Group ZonE   |   Zhuang Qian, Tian Muyuan"
FONT = "Times New Roman"
BODY = 11

# Audit numbers produced by scripts/audit_sample.py + manual labels (2026-11-04).
AUDIT_N = 29
AUDIT_MATCH = 26
AUDIT_MAE = 0.05

REFERENCES = [
    "Chen, Y., Gao, Q., & Rau, P.-L. P. (2017). Watching a movie alone yet together: "
    "Understanding reasons for watching Danmaku videos. International Journal of "
    "Human\u2013Computer Interaction, 33(9), 731\u2013743. "
    "https://doi.org/10.1080/10447318.2017.1282187",
    "He, C., He, L., Lu, T., & Li, B. (2021). Beyond entertainment: Unpacking danmaku "
    "and comments\u2019 role of information sharing and sentiment expression in online "
    "crisis videos. Proceedings of the ACM on Human\u2013Computer Interaction, "
    "5(CSCW2), 1\u201327. https://doi.org/10.1145/3479555",
    "Jhaver, S., Zhang, A. Q., Chen, Q. Z., Natarajan, N., Wang, R., & Zhang, A. X. "
    "(2023). Personalizing content moderation on social media: User perspectives on "
    "moderation choices, interface design, and labor. Proceedings of the ACM on "
    "Human\u2013Computer Interaction, 7(CSCW2), 1\u201333. https://doi.org/10.1145/3610080",
    "Mu, D., Zou, M., & Chen, Y. (2025). How do different cognitive styles learners "
    "deal with the bullet screen interruption in instructional videos? An eye-tracking "
    "study. BMC Psychology, 13(1). https://doi.org/10.1186/s40359-025-03182-1",
    "Wang, L., Yurechko, K., Dani, P., Chen, Q. Z., & Zhang, A. X. (2025). End user "
    "authoring of personalized content classifiers: Comparing example labeling, rule "
    "writing, and LLM prompting. In Proceedings of the 2025 CHI Conference on Human "
    "Factors in Computing Systems (pp. 1\u201321). ACM. "
    "https://doi.org/10.1145/3706598.3713691",
    "Wu, Q., Sang, Y., Zhang, S., & Huang, Y. (2018). Danmaku vs. forum comments: "
    "Understanding user participation and knowledge sharing in online videos. In "
    "Proceedings of the 2018 ACM Conference on Supporting Groupwork (pp. 209\u2013218). "
    "ACM. https://doi.org/10.1145/3148330.3148344",
]

INTRO = [
    "Danmaku, the bullet comments that scroll across the video frame, has become a "
    "defining feature of Chinese video platforms. Viewers value it because it restores a "
    "sense of shared viewing. Chen, Gao, and Rau (2017) found that people watch danmaku "
    "videos for companionship, information and entertainment, and Wu, Sang, Zhang, and "
    "Huang (2018) show that danmaku lowers the cost of participation compared with forum "
    "comments while supporting knowledge sharing in the video itself. He, He, Lu, and Li "
    "(2021) add that danmaku carries both information and sentiment, and that these "
    "functions matter well beyond entertainment. The same stream, however, also carries "
    "conflicts, personal attacks and spoilers, and platforms still offer only crude "
    "controls: danmaku fully on, fully off, or a short list of blocked keywords. A viewer "
    "who wants the jokes without the attacks has no way to say so.",

    "Existing research frames our study with three findings. First, bullet screens affect "
    "viewers unevenly: field-dependent learners show reduced retention and greater "
    "attention drift toward the danmaku area, whereas field-independent learners are "
    "largely unaffected, indicating that interference operates through attention "
    "allocation rather than uniform cognitive overload (Mu, Zou, & Chen, 2025). Second, "
    "personalised moderation interfaces must be understandable, contextual and low-effort: "
    "users fear over-filtering, ask for clear category definitions and examples, and "
    "resist bearing the full burden of moderation labour (Jhaver et al., 2023). Third, "
    "among methods for authoring personal classifiers, prompting a large language model "
    "(LLM) reaches the fastest initial performance, especially in recall and F1, yet "
    "example labelling, rule writing and prompting all struggle with sustained refinement "
    "(Wang et al., 2025).",

    "These studies leave a clear gap. Mu, Zou, and Chen (2025) compared danmaku presence "
    "with absence in instructional videos rather than personalised filtering of comment "
    "type or volume. Jhaver et al. (2023) studied general social-media moderation with a "
    "small purposive sample and categories built around text-based toxicity. Wang et al. "
    "(2025) relied on English YouTube comments and binary keep-or-remove decisions. The "
    "domain studies are largely descriptive: Chen et al. (2017), Wu et al. (2018) and He "
    "et al. (2021) explain why viewers post danmaku, but they do not test how different "
    "kinds of danmaku change the viewing experience, and none of them designs an "
    "understandable, low-effort, personal filter for Chinese danmaku.",

    "We therefore ask three questions. (RQ1) How do danmaku type and volume shape viewers\u2019 "
    "experience? (RQ2) Can an explainable classifier tag Chinese danmaku by social function "
    "with enough accuracy and speed for filtering? (RQ3) Can a user-controlled filter "
    "improve viewers\u2019 comfort and sense of control without increasing effort or the fear "
    "of missing out (FOMO)? To answer them, we run a controlled experiment that manipulates "
    "danmaku type and volume, build a viewer-side browser plugin for Bilibili whose separate "
    "policy layer decides whether each comment is shown, hidden or folded, and evaluate the "
    "tagger together with viewers\u2019 effort and experience. The plugin explains every "
    "filtered comment and accepts one-click corrections, which are fed back to the tagger "
    "so that filtering improves with use.",
]

SCOPE = [
    "Research on danmaku has developed along three strands. The first strand explains why "
    "viewers use it. Chen et al. (2017) interviewed and surveyed danmaku viewers and "
    "identified companionship, information and entertainment as the main gratifications, "
    "stressing that text moving over the video creates the feeling of watching together. "
    "Wu et al. (2018) compared danmaku with forum comments and found that danmaku lowers "
    "the cost of participation and supports knowledge sharing inside the video. He et al. "
    "(2021) examined crisis videos and showed that danmaku carries both information "
    "sharing and sentiment expression, which suggests that one comment stream mixes "
    "several social functions at once.",

    "The second strand measures the cognitive cost of the overlay. Mu et al. (2025) used "
    "eye tracking to compare learners with different cognitive styles while they watched "
    "instructional videos with and without bullet screens, and reported lower retention "
    "and greater attention drift for field-dependent learners, while field-independent "
    "learners were largely unaffected.",

    "The third strand designs support for personal moderation. Jhaver et al. (2023) "
    "interviewed social-media users about personalised moderation and derived design "
    "requirements: understandable category definitions, contextual explanations, low "
    "effort, and protection against over-filtering. Wang et al. (2025) compared three ways "
    "for end users to author personal classifiers, namely example labelling, rule writing "
    "and LLM prompting, and found that prompting reaches usable performance fastest while "
    "none of the three is easy to refine over time.",
]

LIMITS = [
    "Each strand leaves something untested. The gratifications studies are descriptive: "
    "they explain why people post danmaku and which functions the stream serves, but they "
    "do not manipulate what viewers see, so they cannot tell us whether attack, humour or "
    "spoilers change comfort, control or comprehension. They also treat danmaku as a "
    "single stream rather than a mixture of types that a viewer may want to separate.",

    "Mu et al. (2025) manipulated only presence versus absence, used instructional videos, "
    "and gave viewers no control, so their design cannot separate the effect of danmaku "
    "type from the effect of volume. Jhaver et al. (2023) worked on general social media "
    "with a small purposive sample and categories built around toxicity, which do not cover "
    "the humour, repetition and spoiler functions that dominate entertainment danmaku. "
    "Wang et al. (2025) used English YouTube comments, binary keep-or-remove decisions and "
    "a short study horizon, so their findings do not establish whether a tagger can be "
    "refined over weeks of real use.",

    "Across the three strands, no study targets the multi-functional register of Chinese "
    "danmaku, separates semantic understanding from presentation decisions, evaluates "
    "comment type and volume jointly, or makes filtering decisions explainable and "
    "correctable while the viewer is watching.",
]

OBJECTIVES = [
    "The study has three objectives, each tied to one research question and to hypotheses "
    "that the main study will test. The pilot reported in Section 6 checks feasibility "
    "only and does not test these hypotheses.",

    "Objective 1 (RQ1) is to characterise how danmaku type and volume shape viewing "
    "experience. H1a predicts that streams dominated by attack and conflict increase "
    "discomfort and reduce perceived control relative to humour and information at equal "
    "volume. H1b predicts that higher volume amplifies these effects by drawing attention "
    "away from the video, consistent with the attention-allocation account of Mu et al. "
    "(2025). H1c predicts an inverted-U: informational danmaku helps comprehension when "
    "sparse but hurts it when dense.",

    "Objective 2 (RQ2) is to build and evaluate an explainable tagger that scores Chinese "
    "danmaku along the seven social-function tags. H2 predicts that a hybrid tagger, which "
    "starts from LLM relevance scores and is refined by keyword rules and accumulated user "
    "corrections, reaches at least .80 macro-averaged F1 against human labels and a mean "
    "absolute error below .15 on the relevance scale, while remaining cheap enough to "
    "precompute a whole video offline and fast enough to decide during playback.",

    "Objective 3 (RQ3) is to evaluate whether a user-controlled filter improves the "
    "experience. H3 predicts that, compared with all-on danmaku, type-based filtering "
    "reduces discomfort and increases perceived control without increasing NASA-TLX "
    "workload or FOMO. H4 predicts that showing a one-line explanation and accepting "
    "one-click corrections increases acceptance of filtering and reduces the concern "
    "about over-filtering that Jhaver et al. (2023) observed.",
]

DESIGN = [
    "The design has four phases (Figure 1). Phase 0 fixes the measurement instrument, "
    "Phase 1 tests how type and volume shape experience, Phase 2 builds the system, and "
    "Phase 3 evaluates it technically and in the field. The phases are sequential because "
    "each one depends on the tag set, the annotation guideline or the system produced "
    "earlier.",

    "Phase 0 (taxonomy and instrument). Seven social-function tags, namely information, "
    "humour, emotional resonance, irrelevant repetition, attack, conflict and spoiler, are "
    "defined with examples and boundary cases in an annotation guideline. Each comment "
    "receives up to three tags with an intensity between 0 and 1, because one danmaku "
    "often serves several functions at once (He et al., 2021). Two coders independently "
    "annotate 240 stratified comments; agreement is reported with Krippendorff\u2019s alpha, "
    "and disagreements are adjudicated to produce the evaluation set.",

    "Phase 1 (controlled experiment). In a within-subject design, participants watch short "
    "entertainment videos under four conditions: no danmaku, all danmaku, danmaku filtered "
    "to keep humour and information, and danmaku filtered to keep attack and conflict, each "
    "at a low and a high volume. Order and video assignment are counterbalanced. Measures "
    "are discomfort (valence and arousal), perceived control, FOMO, co-viewing presence, "
    "comprehension and recall of the video content, and NASA-TLX. Where possible we add eye "
    "tracking, following Mu et al. (2025), to test whether attention allocation explains "
    "the effect of volume.",

    "Phase 2 (system). The tagger runs offline. For each video the danmaku stream is "
    "downloaded, scored into a relevance vector by an LLM prompted with the tag "
    "definitions, then adjusted by keyword rules and by corrections the viewer has already "
    "made. Scoring costs well under a second per comment, so nothing is computed during "
    "playback: the plugin reads the cached vector for the current video. At document start "
    "the plugin hooks the fetch and XHR requests that load the danmaku segment stream, "
    "decodes the protobuf payload, and asks a local policy layer for a decision on each "
    "comment. The policy combines per-tag thresholds with a density rule, in which at most "
    "a set number of comments stay visible in a ten-second window, and returns show, hide "
    "or fold. Folded comments leave the stream and are listed in a side panel as a "
    "count that can be expanded; this replaces the impossible clickable fold over the "
    "canvas and doubles as the explanation interface. Every decision stores its reason, and "
    "one click corrects a tag, which is cached and reused the next time the video plays.",

    "Phase 3 (evaluation). Tag accuracy is measured per tag, as precision, recall and F1 at "
    "each viewer\u2019s threshold, together with mean absolute error and Spearman correlation "
    "against the human relevance scores. Latency is reported as offline seconds per comment "
    "and as runtime p50 and p95 per decision. A two-week field study with ten viewers then "
    "logs installation time, corrections per ten minutes, failed interceptions and "
    "fail-open events, and post-study scales measure comfort, control, FOMO and workload, "
    "supported by short interviews about why viewers hid what they hid.",
]

PILOT = [
    "The pilot answered three feasibility questions rather than testing the hypotheses: "
    "whether the tagging pipeline returns a stable, parseable relevance vector on real "
    "Chinese danmaku at acceptable cost; whether a single dominant label is a defensible "
    "representation of what a comment does; and whether a browser plugin can intercept the "
    "live danmaku stream without breaking playback.",

    "The corpus is one entertainment video (BV1GJ411x7h7) with 1,407 scrolling comments "
    "(display modes 1, 4 and 5). We first labelled the corpus with the keyword-rule "
    "baseline of the project, then re-labelled it with a hybrid tagger that sends batches "
    "of 100 comments to a chat-completions endpoint with the tag definitions and asks for "
    "a JSON relevance vector per comment. To check label quality, one author manually "
    "labelled a stratified sample of 29 comments, blind to the model output, using the "
    "annotation guideline, and we compared the dominant tag and its intensity.",

    "The pipeline is feasible at the scale of a video. The 1,407 comments were labelled in "
    "twelve and a half minutes, which is 0.53 seconds per comment at a batch size of 100, and "
    "every response parsed into the expected schema without a retry. The resulting "
    "distribution is dominated by humour (902 comments, 64%), emotional resonance (233, 17%) "
    "and information (200, 14%), with irrelevant repetition (65, 5%), spoiler (5), conflict "
    "(1), attack (0) and other (1) much rarer; this matches the character of a music and meme "
    "video and confirms that the seven tags cover the everyday stream rather than only its "
    "pathological cases.",

    "A single dominant label is not defensible. The rule baseline put 1,324 of the 1,407 "
    "comments (94.1%) into \u201cother\u201d, because most danmaku are short and contain no "
    "keyword, whereas the LLM assigned a substantive dominant tag to most of those same "
    "comments. In the audit, 26 of 29 dominant tags agreed with the human label (89.7%) and "
    "the mean absolute error of the predicted intensity was 0.05. The three disagreements "
    "were a question about the source of the subtitles, which the model called humour at "
    "low confidence instead of information; a raw danmaku parameter string, which it "
    "called humour instead of irrelevant repetition; and a comment that is at once a joke "
    "and praise, where the model chose emotional resonance and the coder chose humour. "
    "Errors therefore cluster on short comments whose function depends on context, which "
    "is exactly the case that a single label cannot represent. Two observations qualify this "
    "reading. Only 11 comments (0.8%) passed the 0.5 mark on two tags at once, so genuine "
    "multi-functionality is rarer than the literature suggests, and the case for a vector "
    "rests less on simultaneous tags than on near-ties between two plausible tags and on the "
    "fact that viewers disagree about which tag matters. Humour also became the model’s "
    "default for short positive comments, which is the source of all three audit disagreements "
    "and the first target for prompt refinement.",

    "Interception works. The plugin installs at document start, rewrites the text field of "
    "the danmaku protobuf payload, and falls back to the untouched stream on any unexpected "
    "payload; the forty unit assertions of the extension pass, including the codec, the "
    "policy and the fail-open path. Because Bilibili renders danmaku on a canvas, an "
    "individual comment cannot be clicked, so the design moved the explanation and the "
    "correction control into a side panel that lists folded comments by tag.",

    "The pilot changed the design in five ways. First, the seven-category single label was "
    "replaced by a tag vocabulary with a relevance vector, so that multi-functional "
    "comments keep all of their scores and each viewer can set a threshold per tag. Second, "
    "LLM calls were moved out of playback into an offline cache with a local bridge, "
    "because scoring costs about half a second per comment while a decision must be "
    "instantaneous. Third, clickable folding was replaced by removal from the stream plus "
    "an expandable side panel. Fourth, the annotation protocol was simplified to ticking "
    "one to three tags with a three-level intensity on a single screen, since a "
    "free-form protocol takes far longer than a short pilot can afford. Fifth, density "
    "became an explicit dimension of the policy layer, because the pilot made clear that "
    "volume is a property of the stream rather than of a single comment.",

    "The pilot has clear limits: one video, one coder, an offline corpus rather than a live "
    "session, and no participants yet. The usability session with three viewers, summarised "
    "in Table 3, runs in the first week of the main study, followed by the controlled "
    "experiment and the two-week field study.",
]

TABLE1 = [
    ["Research question", "Study", "Primary measures"],
    ["RQ1: How do danmaku type and volume shape experience?",
     "Controlled experiment, within-subject, four conditions at two volumes",
     "Discomfort (valence, arousal), perceived control, FOMO, co-viewing presence, comprehension and recall, NASA-TLX, eye movements"],
    ["RQ2: Can an explainable tagger label danmaku by social function accurately and fast enough?",
     "Tagging pipeline: LLM relevance scores plus rules plus user corrections, evaluated on the adjudicated human labels",
     "Per-tag precision, recall and F1, mean absolute error, Spearman correlation, seconds per comment offline"],
    ["RQ3: Can a user-controlled filter improve experience without adding effort?",
     "Browser plugin and two-week field study with ten viewers",
     "Installation time, corrections per ten minutes, runtime p50 and p95, comfort, control, FOMO, NASA-TLX, interviews"],
]

TABLE2 = [
    ["Pilot measure", "Result"],
    ["Corpus", "1,407 danmaku (BV1GJ411x7h7), display modes 1, 4 and 5"],
    ["Labelling cost", "0.53 s per comment (batch of 100); whole video in 12.5 minutes"],
    ["Dominant tags", "humour 902 (64%), emotional resonance 233 (17%), information 200 (14%), irrelevant repetition 65 (5%), spoiler 5, conflict 1, attack 0, other 1"],
    ["Comments with two tags above 0.5", "11 of 1,407 (0.8%)"],
    ["Schema validity", "100% of responses parsed; no retry needed"],
    ["Rule baseline \u201cother\u201d share", "94.1% (1,324 of 1,407)"],
    ["Audit sample", "29 comments, stratified, one coder blind to model output"],
    ["Dominant-tag agreement", "26 of 29 (89.7%)"],
    ["Mean absolute intensity error", "0.05"],
    ["Interception", "40 unit assertions pass; protobuf rewrite and fail-open verified"],
]

TABLE3 = [
    ["Session step", "Task", "Logged measures", "Success criterion"],
    ["Install", "Install the plugin from the repository and enable it in the browser",
     "Time to first filtering decision, errors", "Under five minutes, without support"],
    ["Playback", "Watch a ten-minute video with the default preferences",
     "Share of comments intercepted, player errors, fail-open events", "At least 95% intercepted, player never frozen"],
    ["Control", "Hide one tag, fold one tag, correct three labels",
     "Corrections per ten minutes, time per correction", "A correction takes under fifteen seconds"],
    ["Experience", "Post-session scales and short interview",
     "Comfort, control, FOMO, NASA-TLX, comments on explanations", "Reported qualitatively; informs Phase 3 measures"],
]


def build_figure() -> Path:
    from make_figure import build_figure as _build
    return _build()


def add_caption(doc, text):
    p = doc.add_paragraph()
    fmt = p.paragraph_format
    fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    fmt.line_spacing_rule = WD_LINE_SPACING.SINGLE
    fmt.space_after = Pt(10)
    fmt.space_before = Pt(4)
    run = p.add_run(text)
    run.font.name = FONT
    run.font.size = Pt(9.5)
    run.italic = True
    return p


def add_table(doc, rows, widths=None, header=True):
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = table.cell(r, c)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.space_before = Pt(2)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(text)
            run.font.name = FONT
            run.font.size = Pt(9)
            if header and r == 0:
                run.bold = True
    if widths:
        for r in range(len(rows)):
            for c, w in enumerate(widths):
                table.cell(r, c).width = Cm(w)
    return table


def main() -> None:
    style = Document()
    style_font = style.styles["Normal"]
    style_font.font.name = FONT
    style_font.font.size = Pt(BODY)
    rpr = style_font.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), FONT)
    rfonts.set(qn("w:hAnsi"), FONT)
    rfonts.set(qn("w:eastAsia"), "SimSun")

    section = style.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(2.8)
    section.right_margin = Cm(2.8)

    doc = Document()
    doc.styles["Normal"].font.name = FONT
    doc.styles["Normal"].font.size = Pt(BODY)
    normal = doc.styles["Normal"]
    nrpr = normal.element.get_or_add_rPr()
    nrfonts = nrpr.get_or_add_rFonts()
    nrfonts.set(qn("w:ascii"), FONT)
    nrfonts.set(qn("w:hAnsi"), FONT)
    nrfonts.set(qn("w:eastAsia"), "SimSun")

    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.54)
    sec.bottom_margin = Cm(2.54)
    sec.left_margin = Cm(2.8)
    sec.right_margin = Cm(2.8)

    def para(text, align="justify", bold=False, indent=0.5, size=BODY, single=False, space_after=0):
        p = doc.add_paragraph()
        fmt = p.paragraph_format
        fmt.alignment = {"justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
                         "center": WD_ALIGN_PARAGRAPH.CENTER,
                         "left": WD_ALIGN_PARAGRAPH.LEFT}[align]
        fmt.line_spacing_rule = WD_LINE_SPACING.SINGLE if single else WD_LINE_SPACING.DOUBLE
        fmt.space_after = Pt(space_after)
        fmt.space_before = Pt(0)
        if indent:
            fmt.first_line_indent = Inches(indent)
        run = p.add_run(text)
        run.font.name = FONT
        run.font.size = Pt(size)
        run.bold = bold
        return p

    def section_heading(text):
        p = doc.add_paragraph()
        fmt = p.paragraph_format
        fmt.line_spacing_rule = WD_LINE_SPACING.DOUBLE
        fmt.space_before = Pt(6)
        fmt.space_after = Pt(0)
        fmt.keep_with_next = True
        run = p.add_run(text)
        run.font.name = FONT
        run.font.size = Pt(BODY)
        run.bold = True
        return p

    # Title page block
    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run(TITLE)
    r.font.name = FONT
    r.font.size = Pt(14)
    r.bold = True

    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    p.paragraph_format.space_after = Pt(10)
    r = p.add_run(GROUP_LINE)
    r.font.name = FONT
    r.font.size = Pt(BODY)

    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    p.paragraph_format.space_after = Pt(12)
    r = p.add_run("Revised introduction with research objectives, design, and pilot study  |  4 November 2026")
    r.font.name = FONT
    r.font.size = Pt(10)
    r.italic = True

    section_heading("1. Introduction")
    for text in INTRO:
        para(text)

    section_heading("2. Specialized Scope: What Previous Studies Have Done")
    for text in SCOPE:
        para(text)

    section_heading("3. Limitations of Previous Studies")
    for text in LIMITS:
        para(text)

    section_heading("4. Research Objectives and Research Questions")
    for text in OBJECTIVES:
        para(text)

    section_heading("5. Research Design")
    for text in DESIGN:
        para(text)

    fig_path = build_figure()
    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(fig_path), width=Inches(6.0))
    add_caption(doc, "Figure 1. Revised research design. The completed pilot feeds the Phase 0 "
                     "instrument and the Phase 1 experiment, and field corrections feed back into "
                     "the Phase 2 policy layer.")

    add_caption(doc, "Table 1. Research questions, studies and primary measures.")
    add_table(doc, TABLE1, widths=[4.6, 5.2, 5.6])
    doc.add_paragraph()

    section_heading("6. Pilot Study")
    for text in PILOT:
        para(text)

    add_caption(doc, "Table 2. Pilot study results.")
    add_table(doc, TABLE2, widths=[4.6, 10.8])
    doc.add_paragraph()

    add_caption(doc, "Table 3. Usability session protocol, run with three viewers in the first "
                     "week of the main study.")
    add_table(doc, TABLE3, widths=[2.6, 4.6, 4.6, 3.6])

    section_heading("7. References")
    for ref in REFERENCES:
        p = doc.add_paragraph()
        fmt = p.paragraph_format
        fmt.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        fmt.line_spacing_rule = WD_LINE_SPACING.DOUBLE
        fmt.space_after = Pt(0)
        fmt.left_indent = Inches(0.5)
        fmt.first_line_indent = Inches(-0.5)
        run = p.add_run(ref)
        run.font.name = FONT
        run.font.size = Pt(BODY)

    SUBMISSION.mkdir(parents=True, exist_ok=True)
    out = SUBMISSION / "ZonE + Introduction + pilot study.docx"
    doc.save(out)

    words = sum(len(t.split()) for group in (INTRO, SCOPE, LIMITS, OBJECTIVES, DESIGN, PILOT)
                for t in group)
    print(json.dumps({"out": str(out), "figure": str(fig_path),
                      "words_body": words, "references": len(REFERENCES)}, indent=1))


if __name__ == "__main__":
    main()
