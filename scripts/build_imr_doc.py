"""Build "Class + Group Name + IMR" docx from docs/study/imr-methods-results.md.

Usage: python3 scripts/build_imr_doc.py
Output: submission/Class X + ZonE + IMR.docx  (Times New Roman 11, double spaced, justified)
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt

REPO = Path(__file__).resolve().parents[1]
MD = REPO / "docs" / "study" / "imr-methods-results.md"
OUT = REPO / "submission" / "Class X + ZonE + IMR.docx"
FIG1 = REPO / "submission" / "figures" / "imr-figure1.png"
FONT = "Times New Roman"
BODY = 11


def set_run(run, size=BODY, bold=False, italic=False):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), FONT)


def para(doc, text, size=BODY, bold=False, italic=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY,
         indent=0.5, hanging=False, before=0, after=0, line=2.0, keep_next=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.alignment = align
    pf.line_spacing = line
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.keep_with_next = keep_next
    if hanging:
        pf.left_indent = Inches(0.5)
        pf.first_line_indent = Inches(-0.5)
    elif indent:
        pf.first_line_indent = Inches(indent)
    run = p.add_run(text)
    set_run(run, size, bold, italic)
    return p


def add_table(doc, rows):
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=ncols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for r, row in enumerate(rows):
        for c in range(ncols):
            text = row[c] if c < len(row) else ""
            cell = table.cell(r, c)
            cell.text = ""
            p = cell.paragraphs[0]
            pf = p.paragraph_format
            pf.line_spacing = 1.0
            pf.space_before = Pt(0)
            pf.space_after = Pt(0)
            pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(text)
            set_run(run, 9, bold=(r == 0))
    return table


def is_separator(cells):
    if not cells:
        return False
    return all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells)


def main():
    text = MD.read_text(encoding="utf-8")
    lines = text.splitlines()

    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.54)
    section.left_margin = section.right_margin = Cm(2.8)

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(BODY)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    body_words = 0
    in_references = False
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            i += 1
            continue
        if line.startswith("# "):
            p = para(doc, line[2:].strip(), size=14, bold=True,
                     align=WD_ALIGN_PARAGRAPH.CENTER, indent=0, line=2.0)
        elif line.startswith("## "):
            in_references = "references" in line[3:].strip().lower()
            para(doc, line[3:].strip(), size=12, bold=True,
                 align=WD_ALIGN_PARAGRAPH.LEFT, indent=0, before=12, after=4, keep_next=True)
        elif line.startswith("### "):
            para(doc, line[4:].strip(), size=BODY, bold=True,
                 align=WD_ALIGN_PARAGRAPH.LEFT, indent=0, before=10, after=2, keep_next=True)
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not is_separator(cells):
                    rows.append(cells)
                i += 1
            add_table(doc, rows)
            continue
        elif re.match(r"^Table \d+\.", line):
            para(doc, line, size=10, bold=True, align=WD_ALIGN_PARAGRAPH.LEFT,
                 indent=0, before=10, after=2, line=1.0, keep_next=True)
        elif line.startswith("Note."):
            para(doc, line, size=9, align=WD_ALIGN_PARAGRAPH.LEFT, indent=0,
                 before=0, after=10, line=1.0)
        elif re.match(r"^Figure \d+\.", line):
            if line.startswith("Figure 1."):
                doc.add_picture(str(FIG1), width=Inches(6.4))
                pic_par = doc.paragraphs[-1]
                pic_par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                pic_par.paragraph_format.space_before = Pt(8)
            para(doc, line, size=10, align=WD_ALIGN_PARAGRAPH.LEFT, indent=0,
                 before=4, after=12, line=1.0)
        elif line.startswith("Class X"):
            para(doc, line, align=WD_ALIGN_PARAGRAPH.CENTER, indent=0, after=2)
        elif line.startswith("Note: replace"):
            pass
        elif in_references:
            para(doc, line, hanging=True, align=WD_ALIGN_PARAGRAPH.LEFT, before=0, after=0)
            body_words += len(line.split())
        else:
            para(doc, line)
            body_words += len(line.split())
        i += 1

    doc.core_properties.title = "Danmaku Filtering: Methods and Results Section Design"
    doc.core_properties.author = "Group ZonE"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print(f"{OUT}  words={body_words}  size={OUT.stat().st_size}")


if __name__ == "__main__":
    main()
