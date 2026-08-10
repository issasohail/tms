param(
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

# Find repository root.
$ProjectRoot = (git rev-parse --show-toplevel 2>$null)
if (-not $ProjectRoot) {
    Write-Error "This script must be run inside the TMS Git repository."
    exit 1
}
$ProjectRoot = $ProjectRoot.Trim()

$ProjectName = Split-Path $ProjectRoot -Leaf
$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$Commit = (git -C $ProjectRoot rev-parse --short HEAD).Trim()
$Branch = (git -C $ProjectRoot branch --show-current).Trim()

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path (Split-Path $ProjectRoot -Parent) "TMS_Backups"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$ZipName = "TMS_${Timestamp}_${Commit}.zip"
$ZipPath = Join-Path $OutputDir $ZipName

$TempRoot = Join-Path $env:TEMP "tms_git_export_$Timestamp"
$ExportRoot = Join-Path $TempRoot $ProjectName

if (Test-Path -LiteralPath $TempRoot) {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $ExportRoot | Out-Null

Write-Host ""
Write-Host "TMS Git Backup"
Write-Host "--------------"
Write-Host "Project : $ProjectRoot"
Write-Host "Branch  : $Branch"
Write-Host "Commit  : $Commit"
Write-Host "Output  : $ZipPath"
Write-Host ""

# Use Python to read Git's NUL-separated tracked-file list.
# This avoids PowerShell/Git quoting problems with unusual filenames.
$PythonCode = @'
import os
import shutil
import subprocess
import sys

repo = sys.argv[1]
dest = sys.argv[2]

raw = subprocess.check_output(
    ["git", "-C", repo, "ls-files", "-z"],
)
paths = [p for p in raw.decode("utf-8", errors="surrogateescape").split("\0") if p]

copied = 0
missing = []

for rel in paths:
    src = os.path.join(repo, rel)
    if not os.path.isfile(src):
        missing.append(rel)
        continue

    dst = os.path.join(dest, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    copied += 1

print(f"Tracked files copied: {copied}")
if missing:
    print(f"Tracked files absent from working tree: {len(missing)}")
'@

$PythonCode | & (Join-Path $ProjectRoot ".venv\Scripts\python.exe") - $ProjectRoot $ExportRoot

if ($LASTEXITCODE -ne 0) {
    throw "Git file export failed."
}

# Capture handoff information.
$Status = git -C $ProjectRoot status --short
$RecentCommits = git -C $ProjectRoot log -10 --oneline --decorate

$Handoff = @"
TMS PROJECT HANDOFF
===================

Created: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Branch:  $Branch
Commit:  $Commit

This ZIP contains the CURRENT WORKING COPIES of files tracked by Git.

That means:
- committed tracked files are included;
- modified tracked files that are not committed yet are also included;
- untracked files are intentionally excluded.

Normally excluded because they are untracked/ignored:
- .venv
- logs and caches
- local exports
- database dumps
- private uploads
- temporary patch files
- previous handoff ZIPs

Git status at export time:
--------------------------
$($Status -join "`r`n")

Recent commits:
---------------
$($RecentCommits -join "`r`n")
"@

$Handoff | Set-Content -LiteralPath (Join-Path $ExportRoot "TMS_CHATGPT_HANDOFF.txt") -Encoding UTF8

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

Compress-Archive `
    -Path (Join-Path $ExportRoot "*") `
    -DestinationPath $ZipPath `
    -CompressionLevel Optimal

Remove-Item -LiteralPath $TempRoot -Recurse -Force

$SizeMB = [math]::Round((Get-Item -LiteralPath $ZipPath).Length / 1MB, 2)

Write-Host ""
Write-Host "BACKUP COMPLETE"
Write-Host "File : $ZipPath"
Write-Host "Size : $SizeMB MB"
Write-Host ""
Write-Host "Upload this ZIP to ChatGPT for the next TMS handoff."
