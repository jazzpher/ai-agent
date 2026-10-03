"""Well-formed one-page letter .docx builder, used by the create_letter_docx tool.

Small models write fragile python-docx code. This builds the layout for them:
aligned letterhead with a circular seal (generated or uploaded), real date,
recipient block, subject, body paragraphs, closing and signatory.
"""
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

NAVY = RGBColor(0x1F, 0x3A, 0x6B)
RED = RGBColor(0xB0, 0x1E, 0x1E)


def today_text(tz: str = "Asia/Manila") -> str:
    now = datetime.now(ZoneInfo(tz))
    return f"{now.strftime('%B')} {now.day}, {now.year}"


def resolve_date_text(date_text=None, tz="Asia/Manila"):
    """Use today's date unless a plausible current/future date was given.

    The model does not know today's date and often invents an old one, so a
    date more than 30 days in the past is replaced by today's real date.
    """
    if not date_text or not str(date_text).strip():
        return today_text()
    text = str(date_text).strip()
    today = datetime.now(ZoneInfo(tz)).date()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y", "%B %d %Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return text if (today - parsed).days <= 30 else today_text()
    return text


def make_seal_png(path: str, lines=("SAMPLE", "SEAL"), size: int = 600) -> str:
    """Draw a simple circular placeholder seal (double ring + text)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    col = (31, 58, 107, 255)
    d.ellipse((6, 6, size - 6, size - 6), outline=col, width=12)
    d.ellipse((50, 50, size - 50, size - 50), outline=col, width=5)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size // 7)
    except OSError:
        font = ImageFont.load_default()
    lines = [str(x) for x in lines if str(x).strip()][:3] or ["SAMPLE"]
    total = len(lines) * (size // 6)
    y = (size - total) // 2
    for line in lines:
        w = d.textlength(line, font=font)
        d.text(((size - w) / 2, y), line, fill=col, font=font)
        y += size // 6
    img.save(path)
    return path


def _bottom_border(paragraph, color="1F3A6B"):
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "12"), ("w:space", "1"), ("w:color", color)):
        bottom.set(qn(k), v)
    borders.append(bottom)
    ppr.append(borders)


def _no_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tbl_pr.append(borders)


def build_letter(out_path, *, agency, office="", seal_lines=("SAMPLE", "SEAL"), seal_image=None,
                 date_text=None, recipient_lines=(), subject="", paragraphs=(), closing="Respectfully,",
                 signatory="", signatory_title="", footer_note="", font="Times New Roman"):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(1)
    sec.top_margin, sec.bottom_margin = Inches(0.8), Inches(0.8)
    base = doc.styles["Normal"]
    base.font.name = font
    base.font.size = Pt(11.5)
    base.element.rPr.rFonts.set(qn("w:eastAsia"), font)
    base.paragraph_format.space_after = Pt(0)

    # Letterhead: seal left, centred agency text right, rule underneath.
    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _no_borders(table)
    left, right = table.rows[0].cells
    left.width, right.width = Inches(1.3), Inches(5.2)
    tmp = None
    img = seal_image if seal_image and os.path.isfile(seal_image) else None
    if img is None:
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        img = make_seal_png(tmp, seal_lines)
    left.paragraphs[0].add_run().add_picture(img, width=Inches(1.15))
    if tmp:
        os.remove(tmp)
    p = right.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(agency.upper())
    r.bold, r.font.size, r.font.color.rgb = True, Pt(17), NAVY
    if office:
        p2 = right.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r2 = p2.add_run(office)
        r2.font.size, r2.font.color.rgb = Pt(12), NAVY
    rule = doc.add_paragraph()
    _bottom_border(rule)
    rule.paragraph_format.space_after = Pt(14)

    def para(text="", *, bold=False, italic=False, after=0, align=None, indent=False, size=None, color=None, underline=False):
        q = doc.add_paragraph()
        if align:
            q.alignment = align
        q.paragraph_format.space_after = Pt(after)
        if indent:
            q.paragraph_format.first_line_indent = Inches(0.5)
        if text:
            run = q.add_run(text)
            run.bold, run.italic, run.underline = bold, italic, underline
            if size:
                run.font.size = Pt(size)
            if color:
                run.font.color.rgb = color
        return q

    para(resolve_date_text(date_text), after=12)
    for i, line in enumerate(recipient_lines):
        para(line, after=12 if i == len(recipient_lines) - 1 else 0)
    if subject:
        para(subject, bold=True, underline=True, after=10)
    for text in paragraphs:
        para(text, after=10, align=WD_ALIGN_PARAGRAPH.JUSTIFY, indent=True)
    para(closing, after=34)
    if signatory:
        para(signatory, bold=True)
    if signatory_title:
        para(signatory_title)
    if footer_note:
        para("", after=18)
        para(footer_note, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, size=9.5, color=RED)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path)
    return out_path
