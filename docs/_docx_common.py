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

FONT_CN = "微软雅黑"
FONT_EN = "Consolas"


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


def heading(doc, text, level=1):
    h = doc.add_heading(level=level)
    line_spacing(h, 1.5)
    h.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    h.paragraph_format.space_after = Pt(6)
    sizes = {1: 16, 2: 13, 3: 11.5}
    run = h.add_run(text)
    set_run_font(run, size=sizes.get(level, 11), bold=True, color=(0x1A, 0x1D, 0x21))
    return h


def caption(doc, text, note=None):
    """图表的编号 + 说明（官方要求每个图表附 100-200 字说明）。

    支持两种叫法：caption(doc, "表 1　内容") 或 caption(doc, "表 1", "内容")。
    """
    if note:
        # 用半角空格而不是全角空格：编号里带 ASCII 数字（如「表 2-1」），
        # 全角空格紧跟 ASCII 时，Word 会按西文字体找字形，找不到就画成豆腐块。
        text = f"{text}  {note}"
    p = doc.add_paragraph()
    line_spacing(p, 1.15)
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(10)
    set_run_font(p.add_run(text), size=9, color=(0x5A, 0x64, 0x74))
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


def code_block(doc, text):
    p = doc.add_paragraph()
    line_spacing(p, 1.0)
    p.paragraph_format.left_indent = Pt(12)
    p.paragraph_format.space_after = Pt(10)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), "F6F7F9")
    p._p.get_or_add_pPr().append(shd)
    for i, line in enumerate(text.strip("\n").split("\n")):
        if i:
            p.add_run().add_break()
        set_run_font(p.add_run(line), size=8.5, en=FONT_EN, cn=FONT_CN)
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
    instr.text = ' TOC \\o "1-3" \\h \\z \\u '
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
    for _ in range(6):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(p.add_run(title), size=26, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(10)
    set_run_font(p.add_run(subtitle), size=12, color=(0x5A, 0x64, 0x74))

    for _ in range(8):
        doc.add_paragraph()

    info = [
        ("参赛队伍", "19 组"),
        ("参赛赛道", "赛题 A：跨平台消息互通"),
        ("队伍成员", "陈志明　李家杰"),
        ("指导教师", "无"),
        ("提交日期", "2026 年 11 月 22 日"),
    ]
    t = doc.add_table(rows=0, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for k, v in info:
        cells = t.add_row().cells
        for i, val in enumerate((k, v)):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if i == 0 else WD_ALIGN_PARAGRAPH.LEFT
            line_spacing(p, 1.5)
            set_run_font(p.add_run(val), size=11, bold=(i == 0))

    # 目录另起一页：用段前分页而不是 add_page_break()，
    # 后者会在新页顶部多留一个空段落，整份文档白多一页。
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.page_break_before = True
    p.paragraph_format.space_after = Pt(14)
    set_run_font(p.add_run("目　录"), size=16, bold=True)

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
