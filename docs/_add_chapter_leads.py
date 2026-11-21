"""给六份文档的每一章补一句「本章结论」。

用法：python _add_chapter_leads.py
只在章节一级标题下面插入一行 chapter_lead，不重复插入。
"""

import pathlib
import re
import sys

DOCS = pathlib.Path(__file__).parent

LEADS = {
    "_build_ui_doc.py": {
        "一": "三个原则：一屏看完、一栏一职、配置收进抽屉。",
        "二": "六个界面里只有主界面常驻，其余五个都是从它打开再回来。",
        "三": "六个界面逐一给图，每张图下面写清它为什么这样摆。",
        "四": "颜色、位置、空状态全站统一，用户不必每次重新判断。",
        "五": "900 像素以下折成单栏；键盘可达、对比度达标。",
    },
    "_build_db_doc.py": {
        "一": "9 张表，流水与配置分开：流水只追加，配置每个平台一行。",
        "二": "字段按组集中列，同组同表共用首列，扫一眼就能看出分组。",
        "三": "外键一律 SET NULL：撤销授权不该删掉已经收到的历史消息。",
        "四": "入站与出站共用一张 message 表，区别只在 direction。",
        "五": "同一模型 + 运行时分支，SQLite 与 PostgreSQL 都能跑，不维护两套 SQL。",
    },
    "_build_api_doc.py": {
        "一": "48 个接口，收件箱占一半：一个用户动作一个接口，换来逐接口可审的权限。",
        "二": "按模块列出全部 48 个，后面只详述「少了它闭环就断」的 8 个。",
        "三": "8 个核心接口各给路由、请求、返回、错误四个格子。",
        "四": "两层鉴权、一张状态码表、三条 curl 就能复现全部结论。",
    },
    "_build_verify_doc.py": {
        "一": "选型的唯一标准是：评审能不能在自己机器上跑起来。",
        "二": "六个「看起来能跑但其实是错的」问题，都给了现象与修法。",
        "三": "六个闭环环节逐一给界面证据，其中 RPA 那条走通了真人会话。",
        "四": "26 项测试，含一条守护四平台字段集合完全一致的断言。",
        "五": "30,648 秒要连跨夜一起看，它不是性能指标，是使用节奏。",
        "六": "两条平台硬约束、几条实现取舍，都写清影响与绕法。",
    },
    "_build_ai_doc.py": {
        "一": "一个仓库、两种跑法：本地 SQLite 直跑，容器验证交付完整性。",
        "二": "赛题禁止直接交 AI 生成结果，这份文档就是那份说明。",
        "三": "AI 负责实现与验证，判断与取舍由人做，边界逐条写明。",
        "四": "四次判断错在哪、怎么发现的、最后按什么改。",
        "五": "六条坑与对应防护，其中四条是工具用法错误而非逻辑错误。",
        "六": "五个由 AI 提出、经人判断后采纳的架构决定。",
        "七": "每个结论都能在仓库里用一条或两条命令复现。",
        "八": "完整对话已导出，按关键节点索引，不必通读。",
        "九": "AI 出草稿，人审内容与结论，交付物由人负责。",
    },
}


def patch(fname, leads):
    path = DOCS / fname
    text = path.read_text(encoding="utf-8")

    if "chapter_lead" not in text.split("FIG")[0]:
        # 在 from _docx_common import ( 的括号里补一行
        m = re.search(r"from _docx_common import \(\s*# noqa: E402\n", text)
        if m:
            text = text[: m.end()] + "    chapter_lead,\n" + text[m.end():]

    added = 0
    for cn, lead in leads.items():
        pattern = re.compile(r'(?m)^(\s*)heading\(doc, "' + cn + r'、[^"]*", 1\)$')
        m = pattern.search(text)
        if not m:
            print(f"  ! {fname}: 未找到第{cn}章标题")
            continue
        after = text[m.end():]
        if after.lstrip().startswith("chapter_lead"):
            continue
        insert = f'\n{m.group(1)}chapter_lead(doc, "{lead}")'
        text = text[: m.end()] + insert + text[m.end():]
        added += 1

    path.write_text(text, encoding="utf-8")
    print(f"{fname}: 插入 {added} 条")


if __name__ == "__main__":
    for f, leads in LEADS.items():
        patch(f, leads)
