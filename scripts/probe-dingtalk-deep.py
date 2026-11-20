"""截一张钉钉窗口的图，确认它现在显示的是什么，并再试两种 UIA 读取方式。"""
import ctypes
import sys
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as auto

auto.SetGlobalSearchTimeout(2)

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindows = user32.EnumWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

found = []


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
    if "DingTalk" in cls.value or "钉" in buf.value:
        found.append((hwnd, buf.value, cls.value))
    return True


EnumWindows(WNDENUMPROC(cb), 0)
if not found:
    print("钉钉窗口没找到")
    sys.exit(1)

hwnd = found[0][0]
ctrl = auto.ControlFromHandle(hwnd)
r = ctrl.BoundingRectangle
print(f"窗口 hwnd={hwnd} rect=({r.left},{r.top},{r.right},{r.bottom})")

# 截图
try:
    from PIL import ImageGrab

    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    out = r"C:\Users\75090\Desktop\DEEP SEEK\_ego\feishu-setup-20261102-1750\ding-uia-01.png"
    img.save(out)
    print("截图:", out, img.size)
except Exception as e:
    print("截图失败:", e)

# 方式 1：control view（默认）
print()
print("=== 方式1 ControlView ===")
kids = ctrl.GetChildren()
print(f"  children = {len(kids)}")
for k in kids[:5]:
    print(f"    [{k.ControlTypeName}] {k.Name!r} cls={k.ClassName}")

# 方式 2：raw view
print()
print("=== 方式2 RawViewWalker ===")
try:
    walker = auto.GetControlViewWalker()
    raw = auto.GetRawViewWalker()
    first = raw.GetFirstChild(ctrl)
    n = 0
    while first is not None and n < 8:
        print(f"    [{first.ControlTypeName}] {first.Name!r} cls={first.ClassName}")
        first = raw.GetNextSibling(first)
        n += 1
    print(f"  raw 子节点 = {n}")
except Exception as e:
    print("  raw view 失败:", str(e)[:80])

# 方式 3：Win32 子窗口
print()
print("=== 方式3 Win32 EnumChildWindows ===")
EnumChildWindows = user32.EnumChildWindows
children = []


def ccb(ch, lp):
    n2 = user32.GetWindowTextLengthW(ch)
    b2 = ctypes.create_unicode_buffer(n2 + 1)
    user32.GetWindowTextW(ch, b2, n2 + 1)
    c2 = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(ch, c2, 256)
    children.append((ch, b2.value, c2.value))
    return True


EnumChildWindows(hwnd, WNDENUMPROC(ccb), 0)
print(f"  子窗口 {len(children)} 个")
for ch, t, c in children[:12]:
    print(f"    hwnd={ch} cls={c!r} title={t[:30]!r}")
