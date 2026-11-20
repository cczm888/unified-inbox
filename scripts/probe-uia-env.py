"""对照实验：判断「读不到控件树」是钉钉的问题，还是这个进程看不到桌面的问题。

做法：Win32 枚举所有可见顶层窗口，挑几个不同类型的，各自 ControlFromHandle
后数子节点。如果别的程序也是 0，说明是环境限制（非交互窗口站），不是钉钉的问题。
"""
import ctypes
import sys
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as auto

auto.SetGlobalSearchTimeout(2)

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindows = user32.EnumWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

wins = []


def cb(hwnd, lparam):
    if not user32.IsWindowVisible(hwnd):
        return True
    n = user32.GetWindowTextLengthW(hwnd)
    if n == 0:
        return True
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    wins.append((hwnd, buf.value, cls.value))
    return True


EnumWindows(WNDENUMPROC(cb), 0)

print(f"=== 可见顶层窗口 {len(wins)} 个 ===")
for hwnd, title, cls in wins[:25]:
    print(f"  hwnd={hwnd:<9} cls={cls:<32} title={title[:40]!r}")

print()
print("=== 逐个试 ControlFromHandle + GetChildren ===")
env_total = 0
for hwnd, title, cls in wins[:12]:
    try:
        ctrl = auto.ControlFromHandle(hwnd)
        kids = ctrl.GetChildren()
        n = len(kids)
        env_total += n
        names = [((k.Name or "")[:18]) for k in kids[:3]]
        print(f"  {cls:<32} children={n:<3} {names}")
    except Exception as e:
        print(f"  {cls:<32} ERR {str(e)[:50]}")

print()
print("=== 结论 ===")
if env_total == 0:
    print("  所有窗口的子节点都是 0 → 这个进程读不到 UIA 树（非交互窗口站），")
    print("  不能据此判断钉钉是否支持 UIA。需要在交互桌面里复测。")
else:
    print(f"  有窗口能读到子节点（共 {env_total} 个）→ UIA 环境正常，")
    print("  钉钉窗口子节点为 0 说明它本身没向 UIA 暴露控件树。")
