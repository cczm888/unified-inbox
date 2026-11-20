# 钉钉 RPA 可行性探查记录

> 目的：判断能否像飞书那样，用浏览器/客户端自动化给钉钉发消息。
> 结论：**不能走 DOM/UIA 路线，只剩图像识别 + 键鼠模拟，不建议做。**
> 日期：2026-11-20　探查人：开发（AI 协作）

## 一、先看网页版：已经关了

| 地址 | 结果 |
|---|---|
| `https://im.dingtalk.com/` | 302 到 `page.dingtalk.com/wow/...`，页面标题「系统维护页面」，正文「系统维护中 / 下载对应版本客户端获得完整体验」 |
| `https://web.dingtalk.com/` | `page.goto` 15 秒超时，导航未完成 |
| `https://www.dingtalk.com/` | 官网，只有客户端下载入口 |

飞书有 `feishu.cn/messenger/` 这样可用的网页版，钉钉没有。所以飞书那套「开标签页 → 点会话 → 打字 → 回车」在钉钉上没有落地页。

## 二、再试客户端 UIA：读不到控件树

### 2.1 环境对照实验（排除"环境不行"的误判）

不能一上来就说"钉钉不支持 UIA"，得先证明这个进程读得到别人的窗口。

用 Win32 `EnumWindows` 枚举可见窗口，逐个 `ControlFromHandle` + `GetChildren()`：

| 窗口类名 | 子节点数 |
|---|---|
| `Chrome_WidgetWin_1`（ego 浏览器） | 1、2 |
| `CASCADIA_HOSTING_WINDOW_CLASS`（cmd） | 1 |
| **`StandardFrame_DingTalk`（钉钉）** | **0** |
| `kugou_ui`（酷狗） | 0 |
| `SDL_app`（Steam） | 0 |
| `Windows.UI.Core.CoreWindow`（设置） | 0 |

有窗口能读到子节点，说明 **UIA 客户端本身是好的**，钉钉的 0 不是环境问题。

### 2.2 挖到子窗口层

钉钉顶层窗口 `GetChildren()` 为 0，但 Win32 `EnumChildWindows` 能枚举出 13 个子窗口：

```
Qt5152QWindowIcon            '束元洲'                     ← Qt 原生窗口
DTWebTabWin                  'qt_image_web_tab_...'
CefBrowserWindow
Chrome_WidgetWin_0
Chrome_RenderWidgetHostHWND  'Chrome Legacy Window'      ← CEF 渲染窗口
Navigator
DingChatWnd                                               ← 聊天窗口
CefBrowserWindow × 2
Chrome_RenderWidgetHostHWND × 2
```

对 `Chrome_RenderWidgetHostHWND`、`DingChatWnd`、`Navigator` 分别做 UIA：

```
=== DTWebTabWin                     UIA children = 0
=== Chrome_RenderWidgetHostHWND      UIA children = 0
=== Navigator                        UIA children = 0
=== DingChatWnd                      UIA children = 0
```

**全部为 0。**

### 2.3 根因

从安装目录可以看出技术栈：

```
plugins\dtwebview\libcef.dll        132,772,656 B   ← CEF 内核
data\icudtl.dat                      10,284,336 B
eapp_runtime\icudtl.dat              10,197,152 B   ← 另一套运行时
DingTalk.exe                          1,046,320 B   ← 外壳只有 1 MB
```

主界面是 **Qt 原生绘制**（`StandardFrame_DingTalk`、`Qt5152QWindowIcon`），CEF 只用来承载 webview 内容（小程序、卡片、内置浏览器）。Qt 默认不向 Windows UIA 注册 provider——除非用支持可访问性的构建版本，而钉钉显然没有。

## 三、最后试 CDP：能连，但是空壳

用 `--remote-debugging-port=9222` 重启钉钉：

```powershell
& "C:\Program Files (x86)\DingDing\main\current\DingTalk.exe" --remote-debugging-port=9222
```

结果：

```
9222 listening = True
Browser  = Chrome/91.0.4472.164
targets  = 2
  - page | about:blank
  - page | chatbox | app://desktop.dingtalk.com/web_content/chatbox.html?isFourColumnMode=f
```

连上 `chatbox` 跑 `Runtime.evaluate`：

```json
{"title":"chatbox","bodyLen":0,"editable":0,"textarea":0}
```

`Page.getFrameTree` 只有一个 frame，没有 iframe；`Target.getTargets` 也只有这两个。

**`chatbox.html` 是个空壳，聊天主界面不在 CEF 里。**

## 四、三条路都堵死后的剩余选项

| 路径 | 可行性 | 致命弱点 |
|---|---|---|
| 网页版 DOM | ✗ 网页版已关停 | — |
| 客户端 UIA | ✗ Qt 不注册 provider | — |
| 客户端 CDP | ✗ 主界面不是网页 | — |
| 图像识别 + 键鼠模拟 | ✓ 技术上可做 | 见下 |

如果非要做图像识别方案，代价是：

1. **抢焦点**：必须把钉钉窗口拉到前台并保持可见，用户没法同时干别的
2. **脆**：DPI 缩放、深色/浅色主题、客户端版本升级，任何一个变了模板匹配就失效
3. **不可验证**：飞书方案能回读"右侧标题是不是目标会话"，图像方案只能看"像素变没变"，发错人无从发现——这正是我们踩过的坑

## 五、建议

**钉钉侧不做 RPA 发送。** 理由：

- 官方 API 路线（Stream 模式）代码已就绪，只差一个组织管理员身份
- 用户只需在钉钉客户端里「创建团队 → 成为管理员」，就能开通开发者权限，走正规 Stream 通道
- 图像识别的脆弱性在演示场合是负资产——现场卡一次比不做更难看

**要凑第三个平台，用 Telegram**：找 `@BotFather` 发 `/newbot`，零审核、零权限、纯 HTTP API，五分钟的事。

## 六、附：本次探查用到的脚本

| 脚本 | 作用 |
|---|---|
| `scripts/probe-dingtalk-uia.py` | 主窗口 UIA 遍历 + Win32 兜底 |
| `scripts/probe-uia-env.py` | 环境对照实验（判断是环境问题还是目标问题） |
| `scripts/probe-dingtalk-deep.py` | 截图 + 三种读取方式对比 + 子窗口枚举 |
| `scripts/probe-dingtalk-cef.py` | 对每个 CEF/Qt 子窗口分别试 UIA |
| `scripts/probe-dingtalk-cdp.mjs` | CDP 连 chatbox，读 DOM 与 frame 树 |
| `scripts/probe-dingtalk-targets.mjs` | 通过 browser ws 翻全部 target |

这些都留在仓库里，评审若问"为什么钉钉没做成"，跑一遍就能复现结论。
## 七、微信（补充探查）

同样实测了，比钉钉更彻底——三条路都不通。

### 7.1 客户端技术栈

安装目录 `C:\Program Files\Tencent\Weixin\4.1.15.13\`：

```
RadiumWMPF.bin      432,405,783 B   ← 微信自研跨平台 UI 框架
Weixin.dll          201,552,944 B
XPlayer.bin          66,471,101 B
WeChatOcr.bin        51,896,993 B   ← 自带 OCR（它自己就在做图像识别）
XEditor.bin          23,388,262 B
libEGL.dll / libGLESv2.dll          ← OpenGL 直接绘制
```

没有 `libcef.dll`、没有 `app.asar`、没有 `icudtl.dat`、没有 `resources.pak`。

### 7.2 实测 UIA

```
=== 微信窗口 ===
  hwnd=2884470 title='微信' cls='Qt51514QWindowIcon'

=== 环境对照（cmd 窗口）===
  CASCADIA_HOSTING_WINDOW_CLASS children=1        ← 环境正常

=== 微信主窗口 ===
  UIA children = 0

=== 子窗口 ===
  Qt51514QWindowIcon        'Weixin'                 UIA children=0
  MMUIRenderSubWindowHW     'MMUIRenderSubWindowHW'  UIA children=0
```

主窗口和所有子窗口都是 0。`MMUIRenderSubWindowHW` 是微信自研的渲染层（MMUI = 微信 UI），Qt 5.15.14 外壳 + 自绘内容。

### 7.3 实测 MSAA（比 UIA 更老的接口）

UIA 是 2005 年之后的接口，有些应用只实现了更早的 MSAA（`IAccessible`）。
Chromium 系一律支持 MSAA，所以这一项必须单独测——不然结论站不住。

探测方式（`oleacc.dll` 的 `AccessibleObjectFromWindow`，绕过 UIA 直接拿 COM 接口）：

```python
import comtypes, comtypes.client
comtypes.client.GetModule("oleacc.dll")
from comtypes.gen.Accessibility import IAccessible

p = ctypes.POINTER(IAccessible)()
oleacc.AccessibleObjectFromWindow(
    hwnd, OBJID_CLIENT,            # 0xFFFFFFFC
    ctypes.byref(IAccessible._iid_),
    ctypes.byref(p))
```

结果：

```
hwnd=659002  title='微信'  cls=Qt51514QWindowIcon
  MSAA childCount=2   name='微信'

  遍历 accChild(1) / accChild(2)：
    → 两个都是简单元素（accChild 返回 None）
    → accName(i) 读不出内容，accRole(i) 也读不出
```

**childCount=2 比钉钉的 0 好看，但实际等价于 0。** 这两个是窗口级的空壳节点，
往下再没有任何可读内容。Qt 默认只向辅助技术暴露窗口边界，不暴露控件树——
Qt 的 MSAA provider 需要应用显式加载 `qwindows.dll` 的无障碍插件才会填充内容。

所以：**UIA 是空，MSAA 也是空**。两条腿都断。

区别在于钉钉的 0 是"CEF renderer 没开 accessibility"，微信的 2 是"Qt 没加载
无障碍插件"——成因不同，结果一样：任何走系统辅助接口的工具（Power Automate /
UiPath / 影刀 / uiautomation / pywinauto）看到的都是空窗户。

### 7.4 调试端口

```
=== 监听端口 ===
  无监听
=== 命令行 ===
  pid=32440 "C:\Program Files\Tencent\Weixin\Weixin.exe"     ← 无 --remote-debugging-port
```

不是 Chromium 系，没有 CDP 可用。

### 7.5 官方接口

| 接口 | 能否读个人消息 | 门槛 |
|---|---|---|
| 微信公众号 | ✗ 只能被动回复关注者（48 小时窗口） | 企业主体认证 |
| 企业微信 | ✗ 只能读企业内成员 | 企业认证 |
| 微信个人号 | 无官方 API | — |
| 微信网页版 | 2017 年起限制，现基本不可用 | — |

微信是三家平台里唯一**连"合规但受限"的接口都不给个人**的。

### 7.6 结论

微信不做。不是技术不行，是**没有任何一条合规路径**：自绘界面读不到（UIA 空、MSAA 只有两个空壳节点）、无调试端口、无个人可用 API。第三方协议逆向会封号且违约。

探查脚本：`scripts/probe-weixin-uia.py`、`scripts/probe-weixin-msaa.py`

---

## 八、三家平台横向对比

把三次探查的结论并排放，才看得出"为什么只有飞书做成了"。

| 维度 | 飞书 | 钉钉 | 微信 |
|---|---|---|---|
| 网页版能用吗 | ✅ `icnbvv0z1c24.user.feishu.cn` | ❌ 已关停（跳「系统维护中」） | ❌ 2017 年起限制 |
| 网页版有输入框吗 | ⚠️ 机器人通知类会话没有，真人会话有 | — | — |
| 客户端 UIA | ✅ Electron，可读 | ❌ 0 节点 | ❌ 0 节点 |
| 客户端 MSAA | ✅ | ❌ `Chrome_WidgetWin_0` childCount=0 | ⚠️ childCount=2，但都是空壳 |
| 客户端 CDP | — | ⚠️ 能连，只有 `about:blank` 和 `chatbox` | ❌ 非 Chromium |
| 官方 API（个人能拿） | ✅ 自建应用，免审权限即时生效 | ⚠️ 需自建组织成为管理员 | ❌ 全部需企业资质 |
| 官方 API 能读历史消息 | ✅ `GET /im/v1/messages` 200 | ❌ 无公开接口（要付费「会话内容存档」） | ❌ |
| 我们最终用的方式 | 官方 API + 网页 RPA 双通道 | 代码就绪，等用户建组织 | 不做 |

三条结论：

**第一，"读不到控件"是应用的单方面选择，不是工具的能力问题。**
钉钉是 CEF renderer 没启用 accessibility，微信是 Qt 没加载无障碍插件。两者成因不同，结果一样：任何走系统辅助接口的工具看到的都是空窗口。换 Power Automate、UiPath、影刀都改不了——它们用的是同一套接口。

**第二，飞书能做成的关键不是它技术更好，是它给了 RPA 一条退路。**
官方 API 拿不到全量时，网页版还能用、真人会话还有输入框、DOM 还能读出标题来核对。钉钉网页版直接关停，微信连网页版都没有——**没有退路的平台，RPA 就是赌博**。

**第三，能不能验证"发给了谁"，是 RPA 方案的分水岭。**
飞书靠回读标题比对修好了发错人（见 `05-初步验证.docx` 3.6 节）。钉钉和微信的标题是像素，比对只能靠 OCR，认错一个字就发给错误的真人。赛题要求"平台内个体用户之间"，这个风险不能接。

