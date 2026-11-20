# -*- coding: utf-8 -*-
r"""探测微信客户端是否向 MSAA（IAccessible）暴露控件树。

UIA 是 2005 年之后的接口，有些应用只实现更早的 MSAA。
Chromium 系一律支持 MSAA，所以这一项必须单独测——不然结论站不住。

用法：
    backend\.venv\Scripts\python.exe scripts\probe-weixin-msaa.py

需要：comtypes（已随 requirements 装）。
"""
import ctypes
import sys
from ctypes import wintypes

import comtypes
import comtypes.client

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

comtypes.client.GetModule("oleacc.dll")
from comtypes.gen.Accessibility import IAccessible  # noqa: E402

user32 = ctypes.windll.user32
oleacc = ctypes.windll.oleacc
OBJID_CLIENT = 0xFFFFFFFC


def find_windows():
    """按窗口类名找微信窗口（不依赖进程名，改名也能跑）。"""
    hits = []
    P = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        ln = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(ln + 1)
        user32.GetWindowTextW(hwnd, title, ln + 1)
        name, text = cls.value, title.value
        if "Qt" in name or "微信" in text or "Weixin" in text or "MMUI" in name:
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 100 and h > 100:
                hits.append((hwnd, text, name, w, h))
        return True

    user32.EnumWindows(P(cb), 0)
    return hits


def probe(hwnd, label):
    p = ctypes.POINTER(IAccessible)()
    try:
        hr = oleacc.AccessibleObjectFromWindow(
            wintypes.HWND(hwnd),
            wintypes.DWORD(OBJID_CLIENT),
            ctypes.byref(IAccessible._iid_),
            ctypes.byref(p),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: ERR {exc}")
        return
    if hr != 0 or not p:
        print(f"  {label}: hr={hr}")
        return

    cnt = p.accChildCount or 0
    name = p.accName(0)
    print(f"  {label}: MSAA childCount={cnt} name={name!r}")

    readable = 0
    for i in range(1, min(cnt, 8) + 1):
        try:
            child = p.accChild(i)
        except Exception:  # noqa: BLE001
            continue
        if child is None:
            try:
                n, ro = p.accName(i), p.accRole(i)
                if n:
                    readable += 1
                print(f"    [{i}] simple name={n!r} role={ro}")
            except Exception:  # noqa: BLE001
                pass
        else:
            readable += 1
            print(f"    [{i}] child cnt={child.accChildCount} name={child.accName(0)!r}")
    print(f"    → 可读节点数：{readable}（0 表示这条路也堵死）")


def main():
    hits = find_windows()
    if not hits:
        print("没找到微信窗口。先启动微信客户端再跑这个脚本。")
        return 1
    for hwnd, title, cls, w, h in hits:
        print(f"hwnd={hwnd} title={title!r} cls={cls} size={w}x{h}")
        probe(hwnd, cls)
    return 0


if __name__ == "__main__":
    sys.exit(main())