"""生成初赛文档（数据库设计 / 接口设计）的 Word 版本。

排版按官方宣讲会要求：
- 首页仅队伍信息，不放正文
- 第二页自动目录（TOC 域，在 Word 里按 F9 更新）
- 正文页码从 1 起（用 sectPr 分节）
- 正文 1.5 倍行距
- 标题最多三级
- 图表顶格居中，每个图表附说明文字
"""

import json
import pathlib
import sqlite3

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

DOCS = pathlib.Path(__file__).parent
DB = pathlib.Path(__file__).parent.parent / "backend" / "local.db"

FONT_CN = "宋体"
FONT_EN = "Consolas"

# 官方要求封面固定四行，且「不要有其他内容」
PROJECT_NAME = "统一消息收件箱"
TEAM_MEMBERS = "队伍人员：陈志明　李家杰"
ENTRY_NO = "报名序号：19 组"


# ---------------------------------------------------------------- 基础排版

def set_run_font(run, size=10.5, bold=False, cn=FONT_CN, en=FONT_CN, color=None):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = en
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), cn)
    rfonts.set(qn("w:ascii"), en)
    rfonts.set(qn("w:hAnsi"), en)
    if color:
        run.font.color.rgb = RGBColor(*color)


def line_spacing(paragraph, value=1.5):
    pf = paragraph.paragraph_format
    pf.line_spacing = value
    pf.space_before = Pt(0)
    pf.space_after = Pt(6)


def body(doc, text, indent=True, size=10.5, bold=False, align=None, italic=False):
    p = doc.add_paragraph()
    line_spacing(p)
    if indent:
        p.paragraph_format.first_line_indent = Pt(21)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    set_run_font(run, size=size, bold=bold)
    if italic:
        run.italic = True
    return p


# 部分脚本里叫 para，语义相同，给个别名省得两份调用风格
para = body


def lead_body(doc, lead, text, size=10.5, indent=True):
    """首句加粗的正文段：「结论。补充说明……」"""
    p = doc.add_paragraph()
    line_spacing(p)
    if indent:
        p.paragraph_format.first_line_indent = Pt(21)
    r1 = p.add_run(lead)
    set_run_font(r1, size=size, bold=True)
    r2 = p.add_run(text)
    set_run_font(r2, size=size)
    return p


def chapter_lead(doc, text):
    """章首一句结论，让读者不读正文也知道这一章要说什么。"""
    p = doc.add_paragraph()
    line_spacing(p, 1.35)
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(8)
    r1 = p.add_run("本章结论　")
    set_run_font(r1, size=9.5, bold=True, color=(0x2B, 0x55, 0xC8))
    r2 = p.add_run(text)
    set_run_font(r2, size=9.5, color=(0x3C, 0x45, 0x52))
    return p


def heading(doc, text, level=1):
    h = doc.add_heading(level=level)
    line_spacing(h, 1.5)
    h.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    h.paragraph_format.space_after = Pt(6)
    # 官方：一级标题宋体小四加粗、二级宋体五号加粗、三级及正文宋体五号
    sizes = {1: 12, 2: 10.5, 3: 10.5}
    run = h.add_run(text)
    set_run_font(run, size=sizes.get(level, 11), bold=True, color=(0x1A, 0x1D, 0x21))
    return h


def caption(doc, text, note=None):
    """图表题注。

    官方排版要求：表序号与表名在**表正上方居中**，图序号与图名在**图下方居中**。
    调用方一律是「table() 之后紧跟 caption()」或「image() 之后紧跟 caption()」，
    所以这里按前一个块级元素自动判断：前面是表格就把题注移到表格之前。
    """
    if note:
        # 用半角空格而不是全角空格：编号里带 ASCII 数字（如「表 2-1」），
        # 全角空格紧跟 ASCII 时，Word 会按西文字体找字形，找不到就画成豆腐块。
        text = f"{text}  {note}"
    p = doc.add_paragraph()
    line_spacing(p, 1.15)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.space_before = Pt(6)
    set_run_font(p.add_run(text), size=9, color=(0x5A, 0x64, 0x74))

    # 题注默认落在表格后面；官方要求表题在表上方，这里把它前移
    body = doc.element.body
    prev = p._p.getprevious()
    tbl = None
    if prev is not None and prev.tag == qn("w:tbl"):
        tbl = prev
    elif prev is not None and prev.tag == qn("w:p"):
        # 空段落夹在表格与题注之间的情况
        pp = prev.getprevious()
        if pp is not None and pp.tag == qn("w:tbl"):
            tbl = pp
    if tbl is not None:
        tbl.addprevious(p._p)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(3)
    return p


def image(doc, path, width_cm=15.0):
    """插入图片，顶格居中。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(4)
    p.add_run().add_picture(str(path), width=Cm(width_cm))
    return p


def table(doc, headers, rows, widths=None, header_fill="EEF3FF", font_size=9):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = True
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        line_spacing(p, 1.15)
        set_run_font(p.add_run(h), size=font_size, bold=True)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), header_fill)
        hdr[i]._tc.get_or_add_tcPr().append(shd)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            line_spacing(p, 1.15)
            txt = "" if v is None else str(v)
            set_run_font(p.add_run(txt), size=font_size, en=FONT_EN if i == 0 else FONT_CN)
    if widths:
        for r in t.rows:
            for i, w in enumerate(widths):
                r.cells[i].width = Cm(w)
    return t


def code_block(doc, text, size=8, space_after=10):
    p = doc.add_paragraph()
    line_spacing(p, 1.0)
    p.paragraph_format.left_indent = Pt(12)
    p.paragraph_format.space_after = Pt(space_after)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), "F6F7F9")
    p._p.get_or_add_pPr().append(shd)
    for i, line in enumerate(text.strip("\n").split("\n")):
        if i:
            p.add_run().add_break()
        set_run_font(p.add_run(line), size=size, en=FONT_EN, cn=FONT_CN)
    return p


# ---------------------------------------------------------------- 页码 / 目录

def add_page_field(paragraph):
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)
    set_run_font(run, size=9)


def add_toc_field(paragraph):
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-2" \\h \\z \\u '
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "（在 Word 里右键此处 → 更新域，即可生成目录）"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(sep)
    run._r.append(placeholder)
    run._r.append(fld_end)
    set_run_font(run, size=9, color=(0x98, 0xA1, 0xB0))


def cover_and_toc(doc, title, subtitle):
    """第 1 节：封面 + 目录（不编页码）。"""
    # 封面：官方要求「项目名称（宋体加粗三号居中）、文档名称（宋体加粗四号居中）、
    # 队伍人员（宋体四号居中）、报名序号（宋体四号居中），不要有其他内容」。
    for _ in range(7):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(18)
    set_run_font(p.add_run(PROJECT_NAME), size=16, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(30)
    set_run_font(p.add_run(title), size=14, bold=True)

    for _ in range(6):
        doc.add_paragraph()

    for txt in (TEAM_MEMBERS, ENTRY_NO):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(10)
        set_run_font(p.add_run(txt), size=14)

    # 目录另起一页：用段前分页而不是 add_page_break()，
    # 后者会在新页顶部多留一个空段落，整份文档白多一页。
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.page_break_before = True
    p.paragraph_format.space_after = Pt(14)
    set_run_font(p.add_run("目　录"), size=12, bold=True)

    add_toc_field(doc.add_paragraph())


def start_body_section(doc):
    """第 2 节：正文，页码从 1 开始。"""
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    sec.footer.is_linked_to_previous = False
    footer_p = sec.footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(footer_p.add_run("第 "), size=9)
    add_page_field(footer_p)
    set_run_font(footer_p.add_run(" 页"), size=9)

    # 页码重新从 1 开始
    sect_pr = sec._sectPr
    pg_num = sect_pr.find(qn("w:pgNumType"))
    if pg_num is None:
        pg_num = OxmlElement("w:pgNumType")
        sect_pr.append(pg_num)
    pg_num.set(qn("w:start"), "1")
    return sec


def new_doc():
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = FONT_CN
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
    sec = doc.sections[0]
    sec.page_height = Cm(29.7)
    sec.page_width = Cm(21.0)
    sec.left_margin = Cm(3.0)
    sec.right_margin = Cm(3.0)
    sec.top_margin = Cm(2.5)
    sec.bottom_margin = Cm(2.5)
    return doc
