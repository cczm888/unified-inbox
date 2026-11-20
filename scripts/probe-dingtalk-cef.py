"""对钉钉的每个子窗口分别试 UIA —— 顶层是 Qt 外壳，内容在 CEF 的 RenderWidgetHost 里。"""
import ctypes
import sys
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as auto

auto.SetGlobalSearchTimeout(2)

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def enum_children(hwnd):
    out = []

    def cb(ch, lp):
        out.append(ch)
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return out


def win_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, b, n + 1)
    return b.value


def win_class(hwnd):
    c = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, c, 256)
    return c.value


tops = []


def cb_top(hwnd, lp):
    if not user32.IsWindowVisible(hwnd):
        return True
    if "DingTalk" in win_class(hwnd):
        tops.append(hwnd)
    return True


user32.EnumWindows(WNDENUMPROC(cb_top), 0)
if not tops:
    print("钉钉顶层窗口没找到")
    sys.exit(1)

top = tops[0]
print(f"顶层 hwnd={top} cls={win_class(top)}")
print()

for ch in enum_children(top):
    cls = win_class(ch)
    title = win_title(ch)
    if cls not in ("Chrome_RenderWidgetHostHWND", "DingChatWnd", "Navigator", "DTWebTabWin", "Qt5152QWindowIcon"):
        continue
    print(f"=== {cls} title={title[:24]!r} hwnd={ch} ===")
    try:
        ctrl = auto.ControlFromHandle(ch)
        kids = ctrl.GetChildren()
        print(f"    UIA children = {len(kids)}")
        for k in kids[:6]:
            try:
                print(f"      [{k.ControlTypeName}] {(k.Name or '')[:36]!r} cls={k.ClassName}")
            except Exception:
                pass
        if not kids:
            # 再往下一层挖：有些 CEF 结构要先进 Document
            doc = ctrl.DocumentControl(searchDepth=2)
            if doc.Exists(1):
                print(f"    DocumentControl 找到，children={len(doc.GetChildren())}")
                for k in doc.GetChildren()[:8]:
                    print(f"      [{k.ControlTypeName}] {(k.Name or '')[:36]!r}")
    except Exception as e:
        print("    ERR", str(e)[:90])
    print()
