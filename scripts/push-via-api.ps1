# 用 GitHub REST API 提交本地这笔 commit（github.com:443 的 git 协议被掐时用）
#
# 背景：本机访问 github.com 的 HTTPS 会超时（20 秒 000），但 api.github.com 正常。
# git push 走的是 github.com，所以永远失败；API 走的是 api.github.com，能通。
# 于是用 Git Data API 手工复刻一次 commit：
#   blobs -> trees -> commits -> refs/heads/main
# 推上去之后本地 reset --hard origin/main，让 hash 对齐（内容完全一致）。

param(
    [string]$Repo = "cczm888/unified-inbox",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$files = @(git diff --name-only "origin/$Branch..HEAD")
if (-not $files -or $files.Count -eq 0) {
    Write-Host "没有待推送的改动（origin/$Branch 已是最新）" -ForegroundColor Green
    exit 0
}
Write-Host "待推送文件 $($files.Count) 个：" -ForegroundColor Cyan
$files | ForEach-Object { Write-Host "  $_" }

$baseSha = (gh api "repos/$Repo/git/refs/heads/$Branch" --jq ".object.sha").Trim()
Write-Host "远端当前 HEAD: $baseSha"

$baseTree = (gh api "repos/$Repo/git/commits/$baseSha" --jq ".tree.sha").Trim()

$tmp = Join-Path $env:TEMP "gh-api-push"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)

# 1) 每个改动文件上传成 blob
$entries = @()
foreach ($f in $files) {
    $full = Join-Path $root $f
    if (-not (Test-Path $full)) {
        Write-Host "跳过（已删除）: $f" -ForegroundColor Yellow
        continue
    }
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($full))
    $payload = @{ content = $b64; encoding = "base64" } | ConvertTo-Json -Compress
    $pf = Join-Path $tmp "blob.json"
    [IO.File]::WriteAllText($pf, $payload, $utf8)
    $blobSha = (gh api "repos/$Repo/git/blobs" --method POST --input $pf --jq ".sha").Trim()
    Write-Host "  blob $($blobSha.Substring(0,8))  $f" -ForegroundColor DarkGray
    $entries += @{ path = $f; mode = "100644"; type = "blob"; sha = $blobSha }
}

# 2) 基于远端 tree 建一棵新 tree
$treePayload = @{ base_tree = $baseTree; tree = $entries } | ConvertTo-Json -Depth 6
$tf = Join-Path $tmp "tree.json"
[IO.File]::WriteAllText($tf, $treePayload, $utf8)
$newTree = (gh api "repos/$Repo/git/trees" --method POST --input $tf --jq ".sha").Trim()
Write-Host "新 tree: $newTree"

# 3) 建 commit（作者信息与本地一致，message 原样搬）
$msg = ((git log -1 --format="%B") -join "`n").TrimEnd()
$authorName = ((git log -1 --format="%an") | Select-Object -First 1).Trim()
$authorEmail = ((git log -1 --format="%ae") | Select-Object -First 1).Trim()
$commitPayload = @{
    message = $msg
    tree    = $newTree
    parents = @($baseSha)
    author  = @{ name = $authorName; email = $authorEmail }
} | ConvertTo-Json -Depth 5
$cf = Join-Path $tmp "commit.json"
[IO.File]::WriteAllText($cf, $commitPayload, $utf8)
$newCommit = (gh api "repos/$Repo/git/commits" --method POST --input $cf --jq ".sha").Trim()
Write-Host "新 commit: $newCommit"

# 4) 移动分支指针
$refPayload = @{ sha = $newCommit; force = $false } | ConvertTo-Json -Compress
$rf = Join-Path $tmp "ref.json"
[IO.File]::WriteAllText($rf, $refPayload, $utf8)
gh api "repos/$Repo/git/refs/heads/$Branch" --method PATCH --input $rf --jq ".object.sha" | Out-Null
Write-Host "分支 $Branch 已指向 $newCommit" -ForegroundColor Green

# 5) 收尾：只提示，不动本地
#
# 这里绝对不能 git reset --hard origin/main：
# 远端的这个 commit 是 API 重建的，hash 与本地不同，而本地的 refs/remotes/origin/main
# 还指着旧值 —— 此时 reset 会把刚提交的文件退回上一个版本（真的丢过文件）。
# 正确做法是等 github.com 恢复后手动 fetch 再决定，或者直接 force push 本地这版。
Write-Host ""
Write-Host "远端已更新：$newCommit" -ForegroundColor Green
Write-Host "注意：本地 commit 与远端内容相同但 hash 不同（API 重建）。" -ForegroundColor Yellow
Write-Host "等 github.com:443 恢复后，二选一对齐：" -ForegroundColor Yellow
Write-Host "  git fetch origin; git reset --hard origin/$Branch      # 用远端这份" -ForegroundColor DarkGray
Write-Host "  git push --force origin $Branch                        # 或用本地这份覆盖" -ForegroundColor DarkGray

Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
