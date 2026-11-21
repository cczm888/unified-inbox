"""把目录域里的占位文字换成真实章节列表。

Word 的 TOC 域只有在打开后按 F9 才会生成条目，直接交付 .docx 时
评委看到的是占位提示。这里用 python-docx 把域的结果段替换成
真实章节列表（保留 begin / instr / separate / end 结构）：
任何阅读器里都能看到目录，在 Word 里按 F9 又会刷新成带页码的版本。
"""

import pathlib
import sys

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _docx_common import DOCS, set_run_font  # noqa: E402

FILES = ["01_需求分析", "02_原型设计", "03_数据库设计",
         "04_接口设计", "05_初步验证", "06_其他"]

LEVEL_STYLE = {"Heading 1": 1, "Heading 2": 2, "Heading 3": 3}
SIZE = {1: 11, 2: 10, 3: 9.5}
PREFIX = {1: "", 2: "　　", 3: "　　　　"}


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


def rebuild(p, headings):
    for r in list(p.runs):
        r._r.getparent().remove(r._r)

    head = p.add_run()
    b = OxmlElement("w:fldChar")
    b.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-3" \\h \\z \\u '
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    head._r.append(b)
    head._r.append(instr)
    head._r.append(sep)
    set_run_font(head, size=9)

    first = p.add_run()
    first.add_break()
    set_run_font(first, size=6)

    for lvl, txt in headings:
        br = p.add_run()
        br.add_break()
        set_run_font(br, size=SIZE[lvl])
        run = p.add_run(PREFIX[lvl] + txt)
        run.bold = lvl == 1
        set_run_font(run, size=SIZE[lvl], bold=(lvl == 1))

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
        rebuild(toc_p, heads)
        doc.save(str(path))
        print(f"{name}: 目录写入 {len(heads)} 行")


if __name__ == "__main__":
    main()
