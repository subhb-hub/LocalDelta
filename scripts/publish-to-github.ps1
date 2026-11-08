# Publish the open-source code ONLY after creating an EMPTY PUBLIC GitHub repo.
# No private database, screenshot, statement, or local HMAC key should be in this folder.
param(
  [string]$Owner = 'subhb-hub',
  [string]$Repo = 'LocalDelta'
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Please install Git first.' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python 3.10+ is required.' }
if (-not (Test-Path '.git')) { git init -b main | Out-Null }
git add -A
python scripts/check_public_tree.py
if ($LASTEXITCODE -ne 0) { throw 'Private-file audit failed; nothing was published.' }
Write-Host 'The following files are staged for public publication:'
git diff --cached --name-only
$answer = Read-Host 'Publish THESE staged files to PUBLIC GitHub? Type PUBLISH'
if ($answer -cne 'PUBLISH') { throw 'Cancelled; no remote push performed.' }
$branch = git branch --show-current
if ($branch -ne 'main') { throw "Expected main branch, found $branch" }
$remoteUrl = "https://github.com/$Owner/$Repo.git"
$existing = git remote get-url origin 2>$null
if ($existing -and $existing -ne $remoteUrl) { throw "origin points to $existing, not $remoteUrl" }
if (-not $existing) { git remote add origin $remoteUrl }
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { git commit -m 'feat: initial local-first AI incremental update engine' }
git push -u origin main
if ($LASTEXITCODE -ne 0) { throw 'Push failed: check authentication and that the empty public repository exists.' }
Write-Host "Published: https://github.com/$Owner/$Repo"
