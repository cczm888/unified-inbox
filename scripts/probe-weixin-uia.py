"""实测微信主窗口的 UIA 可读性，判断能否做 RPA。"""
import ctypes
import sys
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as auto

auto.SetGlobalSearchTimeout(2)

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def cls_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def title_of(h):
    n = user32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


tops = []


def cb(h, lp):
    if not user32.IsWindowVisible(h):
        return True
    t = title_of(h)
    if t:
        tops.append((h, t, cls_of(h)))
    return True


user32.EnumWindows(WNDENUMPROC(cb), 0)

targets = [(h, t, c) for h, t, c in tops if "微信" in t or "Weixin" in c or "WeChat" in c]
print("=== 微信窗口 ===")
for h, t, c in targets:
    print(f"  hwnd={h} title={t!r} cls={c!r}")

if not targets:
    print("没找到微信窗口")
    sys.exit(1)

hwnd = targets[0][0]

# 环境对照：同时试一个已知可读的窗口
print()
print("=== 环境对照（cmd 窗口）===")
for h, t, c in tops:
    if c == "CASCADIA_HOSTING_WINDOW_CLASS":
        try:
            cc = auto.ControlFromHandle(h)
            print(f"  {c} children={len(cc.GetChildren())}")
        except Exception as e:
            print("  ERR", str(e)[:50])
        break

print()
print("=== 微信主窗口 ===")
ctrl = auto.ControlFromHandle(hwnd)
r = ctrl.BoundingRectangle
print(f"  Name={ctrl.Name!r} Class={ctrl.ClassName} Rect=({r.left},{r.top},{r.right},{r.bottom})")
kids = ctrl.GetChildren()
print(f"  UIA children = {len(kids)}")
for k in kids[:10]:
    try:
        print(f"    [{k.ControlTypeName}] {(k.Name or '')[:34]!r} cls={k.ClassName}")
    except Exception:
        pass

print()
print("=== 子窗口（Win32）===")
children = []


def ccb(ch, lp):
    children.append(ch)
    return True


user32.EnumChildWindows(hwnd, WNDENUMPROC(ccb), 0)
print(f"  共 {len(children)} 个")
for ch in children[:15]:
    c = cls_of(ch)
    t = title_of(ch)
    n_kids = 0
    try:
        n_kids = len(auto.ControlFromHandle(ch).GetChildren())
    except Exception:
        pass
    print(f"    cls={c!r:<28} title={t[:24]!r:<28} UIA children={n_kids}")

print()
print("=== 判定 ===")
top_n = len(kids)
sub_readable = sum(
    1
    for ch in children
    if (lambda: (
        lambda x: len(x.GetChildren()) if x else 0
    )(None))()
)
total_sub = 0
for ch in children:
    try:
        total_sub += len(auto.ControlFromHandle(ch).GetChildren())
    except Exception:
        pass
print(f"  主窗口 children = {top_n}")
print(f"  子窗口 children 之和 = {total_sub}")
if top_n == 0 and total_sub == 0:
    print("  → 完全读不到，微信和钉钉一样是自绘界面")
else:
    print("  → 有可读节点，值得继续挖")
