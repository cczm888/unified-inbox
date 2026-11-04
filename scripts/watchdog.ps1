<#
  守护进程：每 5 分钟检查一次 8000 / 5173，谁挂了就把谁拉起来。

  为什么需要它：服务是独立进程，机器休眠、注销、内存压力都可能把它们回收；
  启动文件夹里的快捷方式只在「登录那一刻」生效一次，中途挂掉就再也不会恢复。

  两个坑（都踩过）：
  1) 仓库路径带空格（DEEP SEEK），Start-Process 的 -ArgumentList 数组拼接会
     把路径截断成 C:\Users\75090\Desktop\DEEP。所以内部一律用 -EncodedCommand
     传 base64，彻底避开引号与空格问题。
  2) 日志用 [IO.File]::AppendAllText，比 Add-Content 更不容易受环境影响。

  日志：logs\watchdog.log（只在状态变化时写，不刷屏）
  用法：.\scripts\watchdog.ps1 [-IntervalSeconds 300]
#>
param([int]$IntervalSeconds = 300)

$root = Split-Path -Parent $PSScriptRoot
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$logDir = Join-Path $root "logs"
$log = Join-Path $logDir "watchdog.log"
$tmp = Join-Path $root "web\.tmp"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log([string]$msg) {
    $line = "{0}  {1}`r`n" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try { [System.IO.File]::AppendAllText($log, $line, (New-Object System.Text.UTF8Encoding($false))) } catch { }
}

function Test-Port([int]$p) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}

function Start-Encoded([string]$script) {
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($script))
    Start-Process -FilePath $ps -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $b64 -WindowStyle Hidden
}

function Start-Backend {
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $s = "`$env:DATABASE_URL='sqlite+aiosqlite:///./local.db'; `$env:TEMP='$tmp'; `$env:TMP=`$env:TEMP; Set-Location -LiteralPath '$root\backend'; & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning"
    Start-Encoded $s
}

function Start-Frontend {
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $s = "Remove-Item Env:npm_config_allow_scripts -ErrorAction SilentlyContinue; `$env:TEMP='$tmp'; `$env:TMP=`$env:TEMP; Set-Location -LiteralPath '$root\web'; npm run dev -- --host 0.0.0.0 --port 5173"
    Start-Encoded $s
}

$last = ""
Write-Log "watchdog started (interval = ${IntervalSeconds}s, root = $root)"

while ($true) {
    try {
        $b = Test-Port 8000
        $f = Test-Port 5173
        $state = "backend={0} frontend={1}" -f $(if ($b) { "up" } else { "down" }), $(if ($f) { "up" } else { "down" })

        if ($state -ne $last) { Write-Log $state; $last = $state }

        if (-not $b) {
            Start-Backend
            Write-Log "  -> backend restarting"
            Start-Sleep -Seconds 12
        }
        if (-not (Test-Port 5173)) {
            Start-Frontend
            Write-Log "  -> frontend restarting"
            Start-Sleep -Seconds 10
        }

        if ((Test-Port 8000) -and (Test-Port 5173) -and ($last -match "down")) {
            $last = "backend=up frontend=up"
            Write-Log "recovered: both ports listening"
        }
    } catch {
        Write-Log ("error: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $IntervalSeconds
}