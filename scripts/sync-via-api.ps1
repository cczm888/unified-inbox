# 全量同步：把本地工作区与远端 main 的差异一次性推到远端（github.com:443 的 git 协议不通时用）
#
# 与 push-via-api.ps1 的区别：那个按 `git diff origin/main..HEAD` 判断要推什么，
# 一旦本地 refs/remotes/origin/main 是旧的，就会漏推文件（实际发生过两次）。
# 这个脚本不看 git ref，而是拉远端整棵树、逐个比对文件大小，凡不一致就推。
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sync-via-api.ps1
#                                                            -Message "提交说明"

param(
    [string]$Repo = "cczm888/unified-inbox",
    [string]$Branch = "main",
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$utf8 = New-Object System.Text.UTF8Encoding($false)
$tmp = Join-Path $env:TEMP "gh-sync"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

$baseSha = (& gh api "repos/$Repo/git/refs/heads/$Branch" --jq ".object.sha").Trim()
$baseTree = (& gh api "repos/$Repo/git/commits/$baseSha" --jq ".tree.sha").Trim()
Write-Host "远端 HEAD: $baseSha" -ForegroundColor Cyan

$treeFile = Join-Path $tmp "tree.json"
& gh api "repos/$Repo/git/trees/$baseTree`?recursive=1" > $treeFile
$tree = (Get-Content $treeFile -Raw | ConvertFrom-Json).tree

$skip = '(^\.git/|node_modules|/dist/|\.venv|^\.tmpbuild|local\.db$|\.env$|__pycache__|\.pytest_cache|\.mypy_cache|^docs/~\$)'

$diff = @()
foreach ($e in $tree) {
    if ($e.type -ne "blob") { continue }
    if ($e.path -match $skip) { continue }
    $loc = Join-Path $root ($e.path -replace '/', '\')
    if (-not (Test-Path $loc)) { continue }   # 远端有、本地删了：不处理（要删请手动）
    if ((Get-Item $loc).Length -ne [int]$e.size) { $diff += $e.path }
}

# 本地有、远端整棵树里没有的文件（新增文件）
$remoteSet = @{}
foreach ($e in $tree) { if ($e.type -eq "blob") { $remoteSet[$e.path] = $true } }
$localFiles = & git ls-files
foreach ($f in $localFiles) {
    if ($f -match $skip) { continue }
    if (-not $remoteSet.ContainsKey($f) -and (Test-Path (Join-Path $root ($f -replace '/', '\')))) { $diff += $f }
}

$diff = $diff | Sort-Object -Unique
if ($diff.Count -eq 0) { Write-Host "本地与远端内容一致，无需推送" -ForegroundColor Green; Remove-Item $tmp -Recurse -Force; exit 0 }

Write-Host "待同步 $($diff.Count) 个文件：" -ForegroundColor Cyan
$diff | ForEach-Object { Write-Host "  $_" }

$entries = @()
foreach ($f in $diff) {
    $full = Join-Path $root ($f -replace '/', '\')
    $payload = @{ content = [Convert]::ToBase64String([IO.File]::ReadAllBytes($full)); encoding = "base64" } | ConvertTo-Json -Compress
    $pf = Join-Path $tmp "blob.json"
    [IO.File]::WriteAllText($pf, $payload, $utf8)
    $sha = (& gh api "repos/$Repo/git/blobs" --method POST --input $pf --jq ".sha").Trim()
    Write-Host "  blob $($sha.Substring(0,8))  $f" -ForegroundColor DarkGray
    $entries += @{ path = $f; mode = "100644"; type = "blob"; sha = $sha }
}

$tp = @{ base_tree = $baseTree; tree = $entries } | ConvertTo-Json -Depth 6
$tf = Join-Path $tmp "newtree.json"
[IO.File]::WriteAllText($tf, $tp, $utf8)
$newTree = (& gh api "repos/$Repo/git/trees" --method POST --input $tf --jq ".sha").Trim()
Write-Host "新 tree: $newTree"

$msg = if ($Message) { $Message } else { ((git log -1 --format="%B") -join "`n").TrimEnd() }
$name = "cczm888"; $email = "cczm888@users.noreply.github.com"
$cp = @{ message = $msg; tree = $newTree; parents = @($baseSha); author = @{ name = $name; email = $email } } | ConvertTo-Json -Depth 5
$cf = Join-Path $tmp "commit.json"
[IO.File]::WriteAllText($cf, $cp, $utf8)
$newCommit = (& gh api "repos/$Repo/git/commits" --method POST --input $cf --jq ".sha").Trim()
Write-Host "新 commit: $newCommit"

$rp = @{ sha = $newCommit; force = $false } | ConvertTo-Json -Compress
$rf = Join-Path $tmp "ref.json"
[IO.File]::WriteAllText($rf, $rp, $utf8)
& gh api "repos/$Repo/git/refs/heads/$Branch" --method PATCH --input $rf --jq ".object.sha" | Out-Null
Write-Host "远端 $Branch => $newCommit" -ForegroundColor Green

Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
