# 用 Cloudflare Workers 给 Telegram Bot API 搭一个私有反代
#
# 适用场景：境内直连 api.telegram.org 时 TLS 握手被 RST，本地没有任何绕法。
#
# 需要：一个 Cloudflare 账号（免费版够用）+ 一枚 API Token。
#   登录 https://dash.cloudflare.com/profile/api-tokens
#   → Create Token → 模板选 "Edit Cloudflare Workers" → 复制生成的 token
#   账号 ID 在 https://dash.cloudflare.com 右侧栏 "Account ID"
#
# 用法：
#   $env:CF_API_TOKEN = "你的 token"
#   $env:CF_ACCOUNT_ID = "你的 account id"
#   .\scripts\deploy-telegram-relay.ps1
#
# 成功后打印一个 https://tg-relay.<子域>.workers.dev 地址，
# 把它填进 .env 的 TELEGRAM_API_BASE，同一行的 RELAY_KEY 填进 TELEGRAM_RELAY_KEY。

param(
  [string]$Name = "tg-relay",
  [string]$RelayKey = ""
)

$ErrorActionPreference = "Stop"

if (-not $env:CF_API_TOKEN) { throw "缺 CF_API_TOKEN：先设置环境变量" }
if (-not $env:CF_ACCOUNT_ID) { throw "缺 CF_ACCOUNT_ID：先设置环境变量" }

$repo = Split-Path $PSScriptRoot -Parent
$worker = Join-Path $repo "deploy\telegram-relay.js"
if (-not (Test-Path $worker)) { throw "找不到 $worker" }

if (-not $RelayKey) {
  # 生成一个 32 位十六进制密钥；不用 GUID 是为了少两个横线、URL 里更干净
  $RelayKey = -join ((1..32) | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
}

$api = "https://api.cloudflare.com/client/v4/accounts/$($env:CF_ACCOUNT_ID)/workers"
$headers = @{ Authorization = "Bearer $($env:CF_API_TOKEN)" }

Write-Host "1/3 上传 Worker：$Name"
$body = [System.IO.File]::ReadAllBytes($worker)
$r = Invoke-RestMethod -Method Put -Uri "$api/scripts/$Name" -Headers $headers `
  -ContentType "application/javascript" -Body $body
if (-not $r.success) { $r.errors | ConvertTo-Json -Depth 5 | Write-Host; throw "上传失败" }

Write-Host "2/3 写入 RELAY_KEY 环境变量"
$kv = @{ bindings = @(@{ type = "plain_text"; name = "RELAY_KEY"; text = $RelayKey }) } | ConvertTo-Json -Depth 6
$r2 = Invoke-RestMethod -Method Put -Uri "$api/scripts/$Name/bindings" -Headers $headers `
  -ContentType "application/json" -Body ([System.Text.Encoding]::UTF8.GetBytes($kv))
if (-not $r2.success) { $r2.errors | ConvertTo-Json -Depth 5 | Write-Host; throw "写入变量失败" }

Write-Host "3/3 查询访问地址"
$sub = Invoke-RestMethod -Uri "$api/subdomain" -Headers $headers
$host_ = "$Name.$($sub.result.subdomain).workers.dev"

Write-Host ""
Write-Host "部署完成" -ForegroundColor Green
Write-Host "  地址：https://$host_"
Write-Host "  密钥：$RelayKey"
Write-Host ""
Write-Host "把这两行写进 .env（把 KEY 换成上面那个密钥）："
Write-Host "  TELEGRAM_API_BASE=https://$host_"
Write-Host "  TELEGRAM_RELAY_KEY=$RelayKey"
Write-Host ""
Write-Host "然后自检：python scripts/check-platform.py"
