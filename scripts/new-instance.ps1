# 在一台机器上再开一个「完全独立」的收件箱实例
#
# 用途：队友或评委要在自己设备上有一份自己的收件箱，而不是看你那份。
# 每个实例 = 独立数据库 + 独立配置文件 + 独立访问口令 + 独立端口，
# 平台凭证互不共享（新实例是空的，自己去「账号」里接）。
#
# 用法：
#   .\scripts\new-instance.ps1 -Name bob                      # 默认 8001/5174
#   .\scripts\new-instance.ps1 -Name bob -ApiPort 8001 -WebPort 5174
#
# 建好之后别人打开 http://<你的IP>:5174 就能用，输的是这个新实例自己的口令。

param(
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$ApiPort = 8001,
    [int]$WebPort = 5174
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env.$Name"
$dbFile = Join-Path $root "backend\$Name.db"

if ($Name -notmatch '^[a-zA-Z][a-zA-Z0-9_]{1,23}$') {
    Write-Host "实例名只能用字母开头、字母数字下划线，2-24 位。收到：$Name" -ForegroundColor Red
    exit 1
}

function New-Password([int]$len = 12) {
    $chars = (48..57) + (97..122)
    return -join ($chars | Get-Random -Count $len | ForEach-Object { [char]$_ })
}

function New-SecretKey {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join '')
}

# 已经有就复用，别每次重跑都把口令换掉（否则对方下次进来要重新问你要）
$password = ""
$secret = ""
if (Test-Path $envFile) {
    $existing = Get-Content $envFile -Raw
    if ($existing -match '(?m)^ACCESS_PASSWORD=(.+)$') { $password = $Matches[1].Trim() }
    if ($existing -match '(?m)^SECRET_KEY=(.+)$') { $secret = $Matches[1].Trim() }
    Write-Host "沿用已有的 $envFile" -ForegroundColor DarkGray
}
if (-not $password) { $password = New-Password }
if (-not $secret) { $secret = New-SecretKey }

$lines = @(
    "# 实例 $Name —— 独立数据库 / 独立凭证 / 独立口令",
    "# 这份文件不会被提交（.gitignore 里有 .env.*）",
    "",
    "# 本实例自己的库，跟主实例的 local.db 完全分开",
    "DATABASE_URL=sqlite+aiosqlite:///./$Name.db",
    "REDIS_URL=",
    "",
    "# 访问口令：别人打开 http://<你的IP>:$WebPort 要先输这个",
    "ACCESS_PASSWORD=$password",
    "ACCESS_COOKIE_DAYS=7",
    "",
    "# token 加密密钥：每个实例独立，密文不能跨实例解密",
    "SECRET_KEY=$secret",
    "",
    "# 平台凭证全部留空 —— 新实例不继承任何人的账号，自己去「账号 → 接入新平台」里填",
    "FEISHU_APP_ID=",
    "FEISHU_APP_SECRET=",
    "DINGTALK_CLIENT_ID=",
    "DINGTALK_CLIENT_SECRET=",
    "DINGTALK_ROBOT_CODE=",
    "TELEGRAM_BOT_TOKEN=",
    "EMAIL_ADDRESS=",
    "EMAIL_PASSWORD=",
    "EMAIL_IMAP_HOST=",
    "EMAIL_SMTP_HOST=",
    "",
    "# AI：留空则用规则兜底（摘要/优先级判断），不调外部接口",
    "LLM_BASE_URL=https://api.deepseek.com/v1",
    "LLM_API_KEY=",
    "LLM_MODEL=deepseek-chat",
    "",
    "OUTBOX_WORKERS=2",
    "OUTBOX_MAX_ATTEMPTS=5",
    "SLA_MINUTES=15",
    "LOG_LEVEL=info"
)
[System.IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))

# 端口冲突就先说清楚，别让 uvicorn/vite 报一堆看不懂的错
foreach ($p in @($ApiPort, $WebPort)) {
    $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        Write-Host "端口 $p 已被占用，换一个：-ApiPort / -WebPort" -ForegroundColor Red
        exit 1
    }
}

$python = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "找不到 $python —— 先把主实例跑起来（依赖装在同一个 venv 里）" -ForegroundColor Red
    exit 1
}

Write-Host "[1/2] 启动后端 :$ApiPort（库 $Name.db）…" -ForegroundColor Cyan
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$backendArgs = @{
    FilePath               = $python
    ArgumentList           = @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$ApiPort", "--log-level", "warning")
    WorkingDirectory       = (Join-Path $root "backend")
    RedirectStandardOutput = (Join-Path $logDir "$Name.api.log")
    RedirectStandardError  = (Join-Path $logDir "$Name.api.err.log")
    WindowStyle            = "Hidden"
    PassThru               = $true
}
# 关键：用 UTI_ENV_FILE 让这个进程只读 .env.$Name，不读主 .env
$env:UTI_ENV_FILE = $envFile
$proc = Start-Process @backendArgs

$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 800
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host "后端没起来，看日志：logs\$Name.api.err.log" -ForegroundColor Red
    exit 1
}
Remove-Item Env:UTI_ENV_FILE -ErrorAction SilentlyContinue

Write-Host "[2/2] 启动前端 :$WebPort（代理到 :$ApiPort）…" -ForegroundColor Cyan
$nodeArgs = @{
    FilePath               = "cmd.exe"
    ArgumentList           = @("/c", "set WEB_PORT=$WebPort&& set API_TARGET=http://127.0.0.1:$ApiPort&& npm run dev -- --host 0.0.0.0")
    WorkingDirectory       = (Join-Path $root "web")
    RedirectStandardOutput = (Join-Path $logDir "$Name.web.log")
    RedirectStandardError  = (Join-Path $logDir "$Name.web.err.log")
    WindowStyle            = "Hidden"
    PassThru               = $true
}
$web = Start-Process @nodeArgs

$webOk = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 800
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$WebPort/" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $webOk = $true; break }
    } catch { }
}

$lan = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1).IPAddress

Write-Host ""
if ($webOk) {
    Write-Host "实例 $Name 已就绪" -ForegroundColor Green
} else {
    Write-Host "前端可能还没监听完，稍等几秒再刷新（日志 logs\$Name.web.err.log）" -ForegroundColor Yellow
}
Write-Host "  本机     http://127.0.0.1:$WebPort"
if ($lan) { Write-Host "  局域网   http://${lan}:$WebPort   <- 别人的设备用这个" }
Write-Host "  访问口令 $password" -ForegroundColor Yellow
Write-Host ""
Write-Host "这个实例是空的：库里没有消息，也没接任何平台。" -ForegroundColor DarkGray
Write-Host "进去之后点右上角「账号 → 接入新平台」，填自己的凭证，跟主实例互不影响。" -ForegroundColor DarkGray
Write-Host ""
Write-Host "后端 PID $($proc.Id)   前端 PID $($web.Id)   （要关掉：Stop-Process -Id $($proc.Id),$($web.Id)）" -ForegroundColor DarkGray
