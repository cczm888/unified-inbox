"""探查钉钉客户端的 UIA 控件树，判断能否做 RPA。"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as auto

auto.SetGlobalSearchTimeout(3)

print("=== 顶层窗口 ===")
root = auto.GetRootControl()
try:
    tops_raw = root.GetChildren()
except Exception as e:
    print("  !! GetChildren 抛错:", e)
    tops_raw = []
print(f"  GetRootControl().GetChildren() 返回 {len(tops_raw)} 个")
tops = []
for w in tops_raw:
    try:
        if not w.Name and not w.ClassName:
            continue
        tops.append(w)
        print(f"  - {w.Name!r} | {w.ClassName} | {w.ControlTypeName}")
    except Exception:
        pass

if not tops:
    print("  → 这个进程看不到任何顶层窗口（UIA 在非交互会话里取不到桌面）")
    print("  → 换一条路：直接用 Win32 EnumWindows 拿句柄，再 ControlFromHandle")
    import ctypes
    from ctypes import wintypes

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
        title = buf.value
        if title and ("Ding" in title or "钉" in title or "DingTalk" in cls):
            found.append((hwnd, title, cls.value))
        return True

    EnumWindows(WNDENUMPROC(cb), 0)
    print(f"  Win32 枚举命中 {len(found)} 个钉钉窗口：")
    for hwnd, title, cls in found:
        print(f"    hwnd={hwnd} title={title!r} cls={cls!r}")
    if not found:
        print("  → 仍然找不到，钉钉可能没真正显示窗口")
        sys.exit(1)
    win = auto.ControlFromHandle(found[0][0])

if tops:
    win = None
    for w in tops:
        try:
            if w.ClassName == "DingTalk_WebView_Form" or "DingTalk" in (w.Name or ""):
                win = w
                break
        except Exception:
            pass
    if win is None:
        win = auto.WindowControl(searchDepth=1, Name="DingTalk")

if not win.Exists(2):
    print("!! 没找到钉钉主窗口")
    sys.exit(1)

print()
print("=== 主窗口 ===")
print(f"  Name={win.Name!r} Class={win.ClassName} Rect={win.BoundingRectangle}")

counter = [0]
interesting = []


def walk(ctrl, depth=0, maxdepth=6):
    if depth > maxdepth or counter[0] > 1200:
        return
    try:
        children = ctrl.GetChildren()
    except Exception:
        return
    for c in children:
        counter[0] += 1
        try:
            name = (c.Name or "").replace("\n", " ").strip()
            ctype = c.ControlTypeName
            cls = c.ClassName
            if name and (
                ctype in ("EditControl", "DocumentControl", "TextControl", "ListItemControl")
                or any(k in name for k in ("发送", "输入", "消息", "会话", "搜索", "聊天"))
            ):
                interesting.append((depth, ctype, name[:50], cls))
        except Exception:
            pass
        walk(c, depth + 1, maxdepth)


walk(win)
print()
print(f"=== 遍历完成：节点 {counter[0]} 个，命中 {len(interesting)} 条 ===")
for depth, ctype, name, cls in interesting[:40]:
    print("  " * min(depth, 8) + f"[{ctype}] {name!r} cls={cls}")

print()
print("=== 判定 ===")
has_edit = any(t == "EditControl" for _, t, _, _ in interesting)
has_doc = any(t == "DocumentControl" for _, t, _, _ in interesting)
has_list = any(t == "ListItemControl" for _, t, _, _ in interesting)
print(f"  EditControl(输入框)   : {has_edit}")
print(f"  DocumentControl(网页) : {has_doc}")
print(f"  ListItemControl(会话) : {has_list}")
if counter[0] < 20:
    print("  → 控件树几乎是空的，UIA 不可行")
elif has_edit or has_doc:
    print("  → 有可读控件，UIA 可行")
else:
    print("  → 有节点但无明显输入控件，需要再挖深一层")
