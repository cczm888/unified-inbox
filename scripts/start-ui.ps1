# 双击启动/打开统一收件箱
# 逻辑：服务已经在跑就直接开浏览器（秒开）；没跑就先拉起再开。
# 快捷方式指向本文件，见 scripts\make-shortcut.ps1

$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
$webUrl = "http://127.0.0.1:5173"
$apiUrl = "http://127.0.0.1:8000/healthz"

function Test-Port([int]$p) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $p)
        $ok = $c.Connected
        $c.Close()
        return $ok
    } catch {
        return $false
    }
}

$apiUp = Test-Port 8000
$webUp = Test-Port 5173

if ($apiUp -and $webUp) {
    # 两边都活着，直接开页面
    Start-Process $webUrl
    exit 0
}

# 有缺的，走完整启动（dev-up.ps1 自己会在最后打开浏览器）
$devUp = Join-Path $PSScriptRoot "dev-up.ps1"
if (-not (Test-Path $devUp)) {
    Write-Host "[x] 找不到 dev-up.ps1：$devUp" -ForegroundColor Red
    Read-Host "按回车关闭"
    exit 1
}

Write-Host "服务没起全（后端=$apiUp 前端=$webUp），正在启动..." -ForegroundColor Cyan
& $devUp
Write-Host ""
Write-Host "启动完成。这个窗口可以关掉——服务跑在那两个最小化的窗口里。" -ForegroundColor Green
Start-Sleep -Seconds 3
