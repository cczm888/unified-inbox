# 本地一键启动（Windows，不需要 Docker）
# 用法：右键 -> 使用 PowerShell 运行，或在 PowerShell 里执行 .\scripts\dev-up.ps1
# 前提：backend\.venv 已建好并装完依赖，web\node_modules 已装好

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "backend\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "[x] 找不到 Python 虚拟环境：$py" -ForegroundColor Red
    Write-Host "    先执行：cd backend; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path (Join-Path $root "web\node_modules"))) {
    Write-Host "[x] 找不到前端依赖 web\node_modules" -ForegroundColor Red
    Write-Host "    先执行：cd web; npm install" -ForegroundColor Yellow
    exit 1
}

# ---------- 后端：本地用 SQLite，不依赖 Postgres/Redis ----------
Write-Host "[1/3] 启动后端 http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$backendArgs = @(
    "-NoExit", "-Command",
    "`$env:DATABASE_URL='sqlite+aiosqlite:///./local.db'; Set-Location '$root\backend'; & '$py' -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
)
Start-Process pwsh -ArgumentList $backendArgs -WindowStyle Minimized

# 等后端起来（最多 40 秒）
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/healthz" -TimeoutSec 2
        if ($h.ok) { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Host "[!] 后端 40 秒内没起来，看那个最小化的窗口里的报错" -ForegroundColor Yellow
} else {
    $names = ($h.platforms | ForEach-Object { "$($_.platform)=$(if ($_.ready) {'在线'} else {'未就绪'})" }) -join "  "
    Write-Host "    后端就绪，平台状态：$names" -ForegroundColor Green
}

# ---------- 前端 ----------
Write-Host "[2/3] 启动前端 http://127.0.0.1:5173 ..." -ForegroundColor Cyan
Remove-Item Env:npm_config_allow_scripts -ErrorAction SilentlyContinue
$frontArgs = @(
    "-NoExit", "-Command",
    "Remove-Item Env:npm_config_allow_scripts -ErrorAction SilentlyContinue; Set-Location '$root\web'; `$env:TEMP='$root\web\.tmp'; `$env:TMP=`$env:TEMP; npm run dev -- --host 127.0.0.1 --port 5173"
)
New-Item -ItemType Directory -Force -Path (Join-Path $root "web\.tmp") | Out-Null
Start-Process pwsh -ArgumentList $frontArgs -WindowStyle Minimized

$feOk = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $feOk = $true; break }
    } catch { }
}
if ($feOk) { Write-Host "    前端就绪" -ForegroundColor Green }
else { Write-Host "[!] 前端 40 秒内没起来，看那个最小化的窗口" -ForegroundColor Yellow }

# ---------- 打开页面 ----------
Write-Host "[3/3] 打开页面" -ForegroundColor Cyan
Start-Process "http://127.0.0.1:5173"

Write-Host ""
Write-Host "都起来了。关闭：关掉两个最小化的 PowerShell 窗口即可。" -ForegroundColor Cyan
Write-Host "日志：后端口志在那个最小化窗口里；也可以用 http://127.0.0.1:8000/healthz 自检。" -ForegroundColor DarkGray
