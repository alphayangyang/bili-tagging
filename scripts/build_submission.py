#!/usr/bin/env python3
"""Build the Introduction submission Word documents.

Body source of truth: docs/intro/README.md (section "合并稿").
Change GROUP_NAME / AUTHORS / SIGNERS below, then rerun:

    python3 scripts/build_submission.py
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "docs/intro/README.md"
OUT_DIR = ROOT / "submission"

FONT = "Times New Roman"
TITLE = "Filtering Danmaku: How Comment Type and Volume Shape Viewer Experience"
GROUP_NAME = "ZonE"
AUTHORS = "Zhuang Qian, Tian Muyuan, [Your Name]"

# part number -> signature; the builder wraps it in brackets, so do not add your own
SIGNERS = {2: "Zhuang Qian", 4: "Tian Muyuan", 5: "Your Name"}

REFERENCES = [
    [
        ("Jhaver, S., Zhang, A. Q., Chen, Q. Z., Natarajan, N., Wang, R., & Zhang, A. X. (2023). "
         "Personalizing content moderation on social media: User perspectives on moderation choices, "
         "interface design, and labor. ", False),
        ("Proceedings of the ACM on Human-Computer Interaction, 7", True),
        ("(CSCW2), Article 289, 1\u201333. https://doi.org/10.1145/3610080", False),
    ],
    [
        ("Mu, D., Zou, M., & Chen, Y. (2025). How do different cognitive styles learners deal with "
         "the bullet screen interruption in instructional videos? An eye-tracking study. ", False),
        ("BMC Psychology, 13", True),
        (", Article 961. https://doi.org/10.1186/s40359-025-03182-1", False),
    ],
    [
        ("Wang, L., Yurechko, K., Dani, P., Chen, Q. Z., & Zhang, A. X. (2025). End user authoring of "
         "personalized content classifiers: Comparing example labeling, rule writing, and LLM prompting. In ", False),
        ("Proceedings of the 2025 CHI Conference on Human Factors in Computing Systems", True),
        (". Association for Computing Machinery. https://doi.org/10.1145/3706598.3713691", False),
    ],
]


def set_run(run, size=12, bold=False, italic=False):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), FONT)


def new_para(doc, text="", size=12, bold=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY,
             first_indent=None, line=1.5, before=0, after=0):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.alignment = align
    pf.line_spacing = line
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    if first_indent is not None:
        pf.first_line_indent = Inches(first_indent)
    if text:
        set_run(p.add_run(text), size=size, bold=bold)
    return p


def story_style(doc):
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(12)
    rpr = normal.element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:eastAsia"), FONT)


def load_parts():
    text = README.read_text(encoding="utf-8")
    m = re.search(r"^## 合并稿[^\n]*\n(.*?)(?=^## )", text, re.S | re.M)
    if not m:
        raise SystemExit("cannot find 合并稿 section in docs/intro/README.md")
    chunks = re.split(r"^### \d+\.[^\n]*\n", m.group(1), flags=re.M)[1:]
    if len(chunks) != 5:
        raise SystemExit(f"expected 5 parts, got {len(chunks)}")
    parts = []
    for chunk in chunks:
        body = " ".join(line.strip() for line in chunk.strip().splitlines() if line.strip())
        body = re.sub(r"\s*[\(\uff08][^\)\uff09]{1,40}[\)\uff09]\s*$", "", body).strip()
        parts.append(body)
    return parts


def signed(parts):
    return [p + (f" ({SIGNERS[i]})" if i in SIGNERS else "") for i, p in enumerate(parts, 1)]


def build_docx(parts, path, heading="Introduction", with_refs=True):
    doc = Document()
    story_style(doc)
    doc.core_properties.title = TITLE
    doc.core_properties.author = AUTHORS

    new_para(doc, TITLE, size=20, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, line=1.15, after=6)
    new_para(doc, f"{GROUP_NAME}: {AUTHORS}", size=12, align=WD_ALIGN_PARAGRAPH.CENTER, line=1.15, after=18)
    if heading:
        new_para(doc, heading, size=12, bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, after=6)
    for body in signed(parts):
        new_para(doc, body, first_indent=0.5)
    if with_refs:
        new_para(doc, "", after=6)
        new_para(doc, "References", size=12, bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, after=6)
        for segments in REFERENCES:
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
            pf.line_spacing = 1.5
            pf.left_indent = Inches(0.5)
            pf.first_line_indent = Inches(-0.5)
            pf.space_after = Pt(6)
            for text, italic in segments:
                set_run(p.add_run(text), italic=italic)
    doc.save(path)
    return doc


def count_words(parts):
    return sum(len(p.split()) for p in parts)


def main():
    parts = load_parts()
    total = count_words(parts)
    for i, p in enumerate(parts, 1):
        print(f"part {i}: {len(p.split())} words")
    print(f"total body: {total} words (target 500)")

    OUT_DIR.mkdir(exist_ok=True)
    full = OUT_DIR / "Group X + Summary writing.docx"
    build_docx(parts, full)
    print("wrote", full)

    part5 = OUT_DIR / "Part 5 - Research Questions.docx"
    build_docx([parts[4]], part5,
               heading="5. Research Questions / What We Are Going to Do",
               with_refs=False)
    print("wrote", part5)

    (OUT_DIR / "summary_writing.txt").write_text(
        "\n\n".join(signed(parts)) + "\n", encoding="utf-8")
    print("wrote", OUT_DIR / "summary_writing.txt")

    if total > 520:
        print(f"WARNING: {total} words, over the 500-word budget", file=__import__("sys").stderr)


if __name__ == "__main__":
    main()
