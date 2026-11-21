# -*- coding: utf-8 -*-
"""按官方「初赛提交资料要求」改名，并生成 00_提交清单.xlsx。"""
import pathlib
import shutil
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DOCS = pathlib.Path(__file__).parent

RENAME = {
    "01-需求分析.docx": "01_需求分析.docx",
    "02-原型设计.docx": "02_原型设计.docx",
    "03-数据库设计.docx": "03_数据库设计.docx",
    "04-接口设计.docx": "04_接口设计.docx",
    "05-初步验证.docx": "05_初步验证.docx",
    "06-AI协作过程记录.docx": "06_其他.docx",
}

def rename_all() -> None:
    for old, new in RENAME.items():
        src, dst = DOCS / old, DOCS / new
        if src.exists():
            if dst.exists():
                dst.unlink()
            src.rename(dst)
            print(f"renamed: {old} -> {new}")
        elif dst.exists():
            print(f"already: {new}")
        else:
            print(f"MISSING: {old}")

ROWS = [
    # 序号, 文件名, 格式, 一句话内容, 对应官方要求
    ("00", "00_提交清单.xlsx", "excel",
     "本文件。列出七份提交物各自包含什么内容。", "一、需提交文档 第 1 项"),
    ("01", "01_需求分析.docx", "word",
     "对赛题 A 五条要求的逐条理解、22 项功能清单（P0/P1/P2）、"
     "题目之外新增的 5 项功能、端到端闭环流程图与四态状态机、九步用户旅程。",
     "第 2 项：需求理解 / 功能清单 / 新增功能 / 核心业务流程"),
    ("02", "02_原型设计.docx", "word",
     "三栏布局的软件界面清单（按一级菜单 / 二级菜单 / 主要界面三级组织）、"
     "每个界面的展示内容与功能点、6 张真实运行截图及设计说明、交互约定与响应式规则。",
     "第 3 项：界面清单 / 每界面功能 / 主要界面设计"),
    ("03", "03_数据库设计.docx", "word",
     "9 张数据表清单与关联关系、每张表逐字段的中文名与解释、"
     "9 段可直接执行的建表 SQL（取自实际库的 sqlite_master，与 ORM 模型一致）。",
     "第 4 项：表清单及关联 / 字段含义 / 建表 SQL"),
    ("04", "04_接口设计.docx", "word",
     "50 个接口（46 条路径）的全量清单（路由 + 方法 + 用途），"
     "8 个核心接口的请求参数与返回数据结构、错误码约定、鉴权方式、curl 复现命令。",
     "第 5 项：接口清单 / 路由与请求参数及返回数据"),
    ("05", "05_初步验证.docx", "word",
     "技术栈选型与六个真实技术难点（每个含现象/根因/解法）、"
     "验证内容清单、验证结果（36 项自动化测试、四平台实测数据、附件 sha256 一致性、RPA 发送通道）。",
     "第 6 项：技术栈与难点 / 验证内容 / 验证结果"),
    ("06", "06_其他.docx", "word",
     "代码仓库地址、部署方式、AI 协作过程记录（四次判断纠错与六个踩坑）、"
     "对话过程链接索引、三家平台 RPA 可行性探查结论。",
     "第 7 项：代码仓库地址 / 演示材料（不限格式）"),
]

EXTRA = [
    ("--", "支撑材料/证据/", "文件夹",
     "运行证据：8 份平台探活日志、官方宣讲会纪要原文。",
     "支撑 05 的原始材料"),
    ("--", "支撑材料/平台自检输出.txt", "文本",
     "四个平台（飞书/邮箱/钉钉/Telegram）连通性自检的实测输出，一条命令可复现。",
     "支撑 05 表 2-1"),
    ("--", "支撑材料/自检报告.md", "markdown",
     "对照赛题五条与宣讲会五条的逐项自检，含未达标项与整改记录。",
     "自查用，非必交"),
    ("--", "支撑材料/审查材料索引.md", "markdown",
     "结论与证据的对应表：每条结论指向哪份日志、哪个接口、哪次测试。",
     "自查用，非必交"),
    ("--", "ai-session-export/（仓库内）", "文件夹",
     "本次开发的完整对话导出（9,560 行对话版 / 50,417 行完整版），可按关键词检索；"
     "对话过程链接索引见 06 文档第 8 章，未打进提交包以免体积过大。",
     "宣讲会要求：使用 AI 须附对话过程链接"),
]

HEADERS = ("序号", "文件名称", "格式", "内容概要", "对应官方要求")

def build_xlsx() -> pathlib.Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "提交清单"

    title_font = Font(name="微软雅黑", size=14, bold=True)
    head_font = Font(name="微软雅黑", size=10.5, bold=True, color="FFFFFF")
    body_font = Font(name="微软雅黑", size=10)
    head_fill = PatternFill("solid", fgColor="2F6BFF")
    thin = Side(style="thin", color="D9DEE8")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells("A1:E1")
    c = ws["A1"]
    c.value = "2026 智能应用创新开发大赛 · 初赛提交清单（赛题 A：多平台连接器）"
    c.font = title_font
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells("A2:E2")
    c = ws["A2"]
    c.value = ("参赛队伍：19 组    成员：陈志明、李家杰    指导教师：无    "
               "提交日期：2026 年 11 月 22 日")
    c.font = Font(name="微软雅黑", size=10, color="5A6474")
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 24

    for col, text in enumerate(HEADERS, start=1):
        cell = ws.cell(row=3, column=col, value=text)
        cell.font = head_font
        cell.fill = head_fill
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[3].height = 26

    row = 4
    for seq, name, fmt, desc, req in ROWS:
        for col, val in enumerate((seq, name, fmt, desc, req), start=1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.font = body_font
            cell.border = border
            cell.alignment = Alignment(
                vertical="top",
                horizontal="center" if col in (1, 3) else "left",
                wrap_text=True,
            )
        ws.row_dimensions[row].height = 58
        row += 1

    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    c = ws.cell(row=row, column=1, value="附：支撑材料（不占必交名额，供评审查阅）")
    c.font = Font(name="微软雅黑", size=10.5, bold=True, color="1A1D21")
    c.fill = PatternFill("solid", fgColor="EEF3FF")
    c.border = border
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[row].height = 24
    row += 1

    for seq, name, fmt, desc, req in EXTRA:
        for col, val in enumerate((seq, name, fmt, desc, req), start=1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.font = body_font
            cell.border = border
            cell.alignment = Alignment(
                vertical="top",
                horizontal="center" if col in (1, 3) else "left",
                wrap_text=True,
            )
        ws.row_dimensions[row].height = 48
        row += 1

    widths = (6, 26, 10, 62, 30)
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + idx)].width = w

    ws.freeze_panes = "A4"
    out = DOCS / "00_提交清单.xlsx"
    wb.save(out)
    print(f"written: {out} ({out.stat().st_size} B)")
    return out

if __name__ == "__main__":
    rename_all()
    build_xlsx()