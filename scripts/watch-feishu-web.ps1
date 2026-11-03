<#
  定时轮询采集飞书网页版 + 推进统一收件箱。

  为什么用轮询而不是抓包：
    - 飞书网页版把 WebSocket 放在 Web Worker 里，主线程 hook window.WebSocket
      抓不到任何帧（实测 15 秒 0 帧）；
    - 走网络层抓包（mitmproxy / CDP）帧是 protobuf，解析要 .proto 定义，
      成本远高于「每 30 秒重读一次 DOM」。

  所以实时性就用轮询换：30 秒一次，演示场景足够。

  用法：
    .\scripts\watch-feishu-web.ps1                 # 默认 30 秒
    .\scripts\watch-feishu-web.ps1 -Interval 10    # 10 秒
#>
param([int]$Interval = 30)

$root = Split-Path -Parent $PSScriptRoot
$collector = "file:///" + ($root.Replace('\', '/')) + "/scripts/collect-feishu-web.mjs"
$collector = $collector.Replace(' ', '%20')

if (-not (Get-Command ego-browser -ErrorAction SilentlyContinue)) {
    Write-Host "找不到 ego-browser，无法采集" -ForegroundColor Red
    exit 1
}

Write-Host "开始轮询（每 $Interval 秒一次），Ctrl+C 停止" -ForegroundColor Cyan
$round = 0
while ($true) {
    $round++
    $stamp = Get-Date -Format "HH:mm:ss"
    Write-Host "[$stamp] 第 $round 轮 采集…" -ForegroundColor DarkGray
    try {
        $out = ego-browser nodejs -e "await import('$collector')" 2>&1
        $res = ($out | Select-String -Pattern "ingest ->").Line
        if ($res) { Write-Host "  $res" -ForegroundColor Green }
        else { Write-Host "  本轮无输出" -ForegroundColor DarkYellow }
    } catch {
        Write-Host ("  失败：" + $_.Exception.Message) -ForegroundColor Red
    }
    Start-Sleep -Seconds $Interval
}