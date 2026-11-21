"""把目录域里的占位文字换成真实章节列表。

Word 的 TOC 域只有在打开后按 F9 才会生成条目，直接交付 .docx 时
评委看到的是占位提示。这里用 python-docx 把域的结果段替换成
真实章节列表（保留 begin / instr / separate / end 结构）：
任何阅读器里都能看到目录，在 Word 里按 F9 又会刷新成带页码的版本。
"""

import pathlib
import sys

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _docx_common import DOCS, set_run_font, line_spacing  # noqa: E402

FILES = ["01_需求分析", "02_原型设计", "03_数据库设计",
         "04_接口设计", "05_初步验证", "06_其他"]

LEVEL_STYLE = {"Heading 1": 1, "Heading 2": 2}  # 官方：目录仅到二级标题
SIZE = {1: 12, 2: 12}  # 官方：目录为小四
PREFIX = {1: "", 2: "　　"}
WORK = DOCS / "_tmp_build"  # 页码映射（由 Word 预先导出）放这里
BODY_START_PAGE = 3  # 封面 1 页 + 目录 1 页，正文从第 3 页起，页码重编为 1


def page_map(path, count):
    """读 Word 预先导出的「物理页码|级别」清单，换算成显示页码。

    映射文件缺失就返回全 None（目录不带页码），不在这里起 Word——
    批处理里反复启动 Word 会卡死，页码改为离线导出。
    """
    src = WORK / f"{path.stem}.txt"
    if not src.exists():
        return [None] * count
    pages = []
    for line in src.read_text(encoding="utf-16", errors="ignore").splitlines():
        parts = line.strip().split("|")
        if len(parts) == 2 and parts[0].isdigit():
            pages.append(int(parts[0]) - (BODY_START_PAGE - 1))
    if len(pages) != count:
        print(f"    {path.stem}: 页码映射 {len(pages)} 条与标题 {count} 条不符，本份不加页码")
        return [None] * count
    return pages


def find_toc_paragraph(doc):
    for p in doc.paragraphs:
        if "TOC \\o" in p._p.xml:
            return p
    return None


def collect_headings(doc, after_p):
    out, started = [], False
    for p in doc.paragraphs:
        if p._p is after_p._p:
            started = True
            continue
        if not started:
            continue
        lvl = LEVEL_STYLE.get(p.style.name)
        txt = p.text.strip()
        if lvl and txt:
            out.append((lvl, txt))
    return out


def rebuild(p, headings, pages):
    line_spacing(p, 1.5)
    p.paragraph_format.tab_stops.add_tab_stop(
        Cm(15.4), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
    for r in list(p.runs):
        r._r.getparent().remove(r._r)

    head = p.add_run()
    b = OxmlElement("w:fldChar")
    b.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-2" \\h \\z \\u '
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    head._r.append(b)
    head._r.append(instr)
    head._r.append(sep)
    set_run_font(head, size=9)

    first = p.add_run()
    first.add_break()
    set_run_font(first, size=6)

    for idx, (lvl, txt) in enumerate(headings):
        br = p.add_run()
        br.add_break()
        set_run_font(br, size=SIZE[lvl])
        run = p.add_run(PREFIX[lvl] + txt)
        run.bold = lvl == 1
        set_run_font(run, size=SIZE[lvl], bold=(lvl == 1))
        if pages[idx] is not None:
            tab = p.add_run("\t" + str(pages[idx]))
            tab.bold = False
            set_run_font(tab, size=SIZE[lvl])

    tail = p.add_run()
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), "end")
    tail._r.append(e)
    set_run_font(tail, size=9, color=(0x98, 0xA1, 0xB0))


def main():
    for name in FILES:
        path = DOCS / f"{name}.docx"
        doc = Document(str(path))
        toc_p = find_toc_paragraph(doc)
        if toc_p is None:
            print(f"{name}: 未找到目录域，跳过")
            continue
        heads = collect_headings(doc, toc_p)
        if not heads:
            print(f"{name}: 未收集到标题，跳过")
            continue
        pages = page_map(path, len(heads))
        rebuild(toc_p, heads, pages)
        doc.save(str(path))
        got = sum(1 for x in pages if x is not None)
        print(f"{name}: 目录写入 {len(heads)} 行（带页码 {got} 行）")


if __name__ == "__main__":
    main()
