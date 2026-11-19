"""用 matplotlib 画文档要用的流程图 / 状态图（中文字体）。"""

import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT = pathlib.Path(__file__).parent / "figs"
OUT.mkdir(exist_ok=True)

INK = "#1a1d21"
LINE = "#98a1b0"
BRAND = "#2f6bff"
BRAND_BG = "#eef3ff"
GRAY_BG = "#f6f7f9"
GREEN = "#17916c"
GREEN_BG = "#e8f7f1"
ORANGE = "#d98216"
ORANGE_BG = "#fdf4e6"


def box(ax, x, y, w, h, text, fc=GRAY_BG, ec=LINE, fs=11, bold=False, tc=INK):
    ax.add_patch(
        mpatches.FancyBboxPatch(
            (x - w / 2, y - h / 2), w, h,
            boxstyle="round,pad=0.02,rounding_size=0.08",
            facecolor=fc, edgecolor=ec, linewidth=1.2, zorder=2,
        )
    )
    ax.text(x, y, text, ha="center", va="center", fontsize=fs,
            color=tc, fontweight="bold" if bold else "normal", zorder=3, linespacing=1.5)


def arrow(ax, x1, y1, x2, y2, color=LINE, style="-|>", lw=1.4, ls="-", rad=0.0):
    ax.add_patch(
        FancyArrowPatch(
            (x1, y1), (x2, y2),
            arrowstyle=style, mutation_scale=14,
            color=color, linewidth=lw, linestyle=ls,
            connectionstyle=f"arc3,rad={rad}", zorder=1,
        )
    )


def canvas(w=12, h=9):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    return fig, ax


# ---------------------------------------------------------------- 主闭环

def flow_main():
    fig, ax = canvas(11, 8.4)

    # 四个平台
    plats = [
        (1.55, "飞书\n长连接事件"),
        (3.35, "邮箱\nIMAP 轮询"),
        (5.15, "Telegram\nBot 长轮询"),
        (6.95, "RPA / 采集器\nHTTP 推送"),
    ]
    for x, name in plats:
        box(ax, x, 9.15, 1.55, 0.82, name, fc="#fff", ec=LINE, fs=9.5)

    ax.text(8.55, 9.15, "① 接入\n四个来源", fontsize=9, color=BRAND,
            ha="left", va="center", fontweight="bold")

    # 适配层
    box(ax, 4.25, 7.72, 8.0, 0.72,
        "适配层 Adapter：各平台原始报文 → UnifiedMessage（同一套字段）",
        fc=BRAND_BG, ec=BRAND, fs=10.5, bold=True)
    for x, _ in plats:
        arrow(ax, x, 8.72, x if abs(x - 4.25) < 3.9 else 4.25, 8.1, color=LINE)

    # 入库
    box(ax, 4.25, 6.45, 8.0, 0.72,
        "入库 message：按 (platform, platform_msg_id) 幂等去重",
        fc="#fff", ec=LINE, fs=10.5)
    arrow(ax, 4.25, 7.36, 4.25, 6.81)

    # 统一展示
    box(ax, 2.6, 4.75, 3.9, 1.15,
        "统一界面\n会话列表 / 消息流 / 待办 / 看板",
        fc="#fff", ec=LINE, fs=10.5, bold=True)
    arrow(ax, 4.25, 6.09, 3.4, 5.33)

    # 自动回复旁路
    box(ax, 7.0, 4.75, 3.4, 1.15,
        "自动回复规则\n关键词命中 → 直接进 Outbox",
        fc=ORANGE_BG, ec=ORANGE, fs=10)
    arrow(ax, 4.25, 6.09, 7.0, 5.33, color=ORANGE, ls="--")

    # 处理
    box(ax, 2.6, 3.05, 3.9, 1.0,
        "人工处理\n读 / 回复 / 转待办 / 改状态",
        fc="#fff", ec=LINE, fs=10.5)
    arrow(ax, 2.6, 4.17, 2.6, 3.55)

    # Outbox
    box(ax, 3.55, 1.62, 3.7, 0.85,
        "Outbox 投递\n限流 → 发送 → 重试/退避",
        fc=BRAND_BG, ec=BRAND, fs=10, bold=True)
    arrow(ax, 2.6, 2.55, 2.9, 2.05)
    arrow(ax, 7.0, 4.17, 4.6, 2.05, color=ORANGE, ls="--")

    # 回到平台
    box(ax, 7.75, 1.62, 2.9, 0.85, "② 回到原平台\n对方收到消息", fc="#fff", ec=LINE, fs=9.5)
    arrow(ax, 5.42, 1.62, 6.28, 1.62)

    # 状态回流
    box(ax, 3.55, 0.55, 5.0, 0.6,
        "③ 状态回流：消息标记 replied / closed，可回执给对方",
        fc=GREEN_BG, ec=GREEN, fs=10)
    arrow(ax, 3.55, 1.19, 3.55, 0.86, color=GREEN)

    ax.text(0.35, 0.55, "闭环", fontsize=11, color=GREEN, fontweight="bold", va="center")
    arrow(ax, 1.0, 0.55, 1.9, 0.55, color=GREEN, style="-|>")

    fig.tight_layout()
    p = OUT / "flow-main.png"
    fig.savefig(p, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("saved", p)


# ---------------------------------------------------------------- 状态机

def flow_state():
    fig, ax = canvas(11, 4.2)

    states = [
        (1.05, "received\n待处理", "#fff", LINE, INK),
        (3.55, "claimed\n处理中", BRAND_BG, BRAND, BRAND),
        (6.05, "replied\n已回复", GREEN_BG, GREEN, GREEN),
        (8.55, "closed\n已关闭", GRAY_BG, LINE, "#5a6474"),
    ]
    for x, name, fc, ec, tc in states:
        box(ax, x, 5.3, 1.55, 0.95, name, fc=fc, ec=ec, fs=10.5, bold=(ec != LINE), tc=tc)

    # 正向
    labels = ["开始处理", "发出回复", "归档"]
    for i in range(3):
        x1 = states[i][0] + 0.8
        x2 = states[i + 1][0] - 0.8
        arrow(ax, x1, 5.3, x2, 5.3, color=LINE)
        ax.text((x1 + x2) / 2, 5.62, labels[i], fontsize=8.5, color="#5a6474",
                ha="center", va="bottom")

    # 回退
    arrow(ax, 3.55, 4.82, 1.05, 4.82, color=ORANGE, ls="--")
    ax.text(2.3, 4.5, "放弃处理", fontsize=8.5, color=ORANGE, ha="center", va="top")

    # 任意状态可关闭
    arrow(ax, 1.05, 5.78, 8.55, 5.78, color="#c8cfd9", ls=":", rad=-0.16)
    ax.text(4.8, 6.85, "任意阶段都可以直接关闭", fontsize=8.5, color="#8a94a6", ha="center")

    # 说明
    ax.text(1.05, 3.6, "入站", fontsize=9, color="#8a94a6", ha="center")
    ax.text(8.55, 3.6, "终态", fontsize=9, color="#8a94a6", ha="center")
    ax.text(5.0, 3.0,
            "status 记录「处理到哪一步」，read_at 记录「看没看过」——两件事分开存，"
            "所以可以「已读但未处理」。",
            fontsize=9, color="#5a6474", ha="center")

    fig.tight_layout()
    p = OUT / "flow-state.png"
    fig.savefig(p, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("saved", p)


if __name__ == "__main__":
    flow_main()
    flow_state()
