# 把本地网页暴露成一个公网 https 地址（Cloudflare 快速隧道，不用注册账号）
# 用途：演示时让评委/同学在自己电脑上直接打开，不需要和你在同一个 WiFi。
# 用法：.\scripts\start-tunnel.ps1  [-Port 5173]
#
# 注意：快速隧道每次启动域名都会变；你电脑关机或隧道进程被杀，链接立即失效。

param([int]$Port = 5173)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root ".tools\cloudflared.exe"

if (-not (Test-Path $exe)) {
    Write-Host "[!] 找不到 cloudflared：$exe" -ForegroundColor Yellow
    Write-Host "    下载（GitHub 直连不通时可走 gh）：" -ForegroundColor Gray
    Write-Host "    gh release download 2026.10.7 --repo cloudflare/cloudflared --pattern 'cloudflared-windows-amd64.exe' --output '$exe' --clobber" -ForegroundColor Gray
    Write-Host "    或：winget install Cloudflare.cloudflared" -ForegroundColor Gray
    exit 1
}

# 先确认本地服务在跑，否则隧道指过去只会是 502
$up = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $up = $true }
} catch { }
if (-not $up) {
    Write-Host "[!] 127.0.0.1:$Port 没有响应，先启动服务（双击桌面「统一收件箱」）" -ForegroundColor Yellow
    exit 1
}

$errLog = Join-Path $env:TEMP "unified-inbox-tunnel.log"
$outLog = Join-Path $env:TEMP "unified-inbox-tunnel.out.log"
Remove-Item $errLog, $outLog -ErrorAction SilentlyContinue

Write-Host "正在建立隧道 -> http://127.0.0.1:$Port ..." -ForegroundColor Cyan

# cloudflared 的日志走 stderr，重定向到文件最省事（--logfile 这版不落盘）。
# 注意：别把本脚本的输出接进管道（`.\start-tunnel.ps1 | tee out.txt`）——
# cloudflared 会继承那个句柄，管道关不掉，调用方就以为脚本还在跑。
Start-Process -FilePath $exe `
    -ArgumentList "tunnel", "--url", "http://127.0.0.1:$Port", "--no-autoupdate" `
    -RedirectStandardError $errLog -RedirectStandardOutput $outLog `
    -WindowStyle Hidden

# 从日志里抓 trycloudflare 域名（通常 10-20 秒）
$url = $null
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $errLog) {
        $m = Select-String -Path $errLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value; break }
    }
}

if (-not $url) {
    Write-Host "[x] 45 秒内没拿到公网地址，日志：$errLog" -ForegroundColor Red
    Get-Content $errLog -Tail 12 -ErrorAction SilentlyContinue
    exit 1
}

Write-Host ""
Write-Host "  公网地址：$url" -ForegroundColor Green
Write-Host ""
Write-Host "  任何人、任何网络都能打开这个链接（不用连同一个 WiFi）。" -ForegroundColor Gray
Write-Host "  隧道进程：Get-Process cloudflared；关掉它链接即失效。" -ForegroundColor DarkGray
Write-Host "  演示前建议自己先打开一次，确认页面能加载。" -ForegroundColor DarkGray
