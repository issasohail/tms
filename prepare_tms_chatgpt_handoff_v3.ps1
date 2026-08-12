param(
    [string]$Distro = "Ubuntu",
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
$Project = (Get-Location).Path

if (-not (Test-Path (Join-Path $Project "requirements.txt"))) {
    throw "Run this script from the TMS project root (folder containing requirements.txt)."
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL is not installed or wsl.exe is unavailable."
}

function Convert-ToWslPath([string]$WindowsPath) {
    if ($WindowsPath -match '^([A-Za-z]):\\(.*)$') {
        $drive = $Matches[1].ToLower()
        $rest = $Matches[2] -replace '\\','/'
        return "/mnt/$drive/$rest"
    }
    throw "Expected a Windows drive path such as E:\tenant_management_system. Got: $WindowsPath"
}

$WslProject = Convert-ToWslPath $Project
$WheelZipWindows = Join-Path $Project "tms_linux_wheelhouse.zip"
$WheelZipWsl = "$WslProject/tms_linux_wheelhouse.zip"

Write-Host ""
Write-Host "=== TMS ChatGPT Handoff Builder v3 ==="
Write-Host "Project        : $Project"
Write-Host "WSL distro     : $Distro"
Write-Host "Python target  : $PythonVersion"
Write-Host ""

# 1) Create normal TMS code backup.
$BackupScript = Join-Path $Project "backup_tms_for_chatgpt.ps1"
if (Test-Path $BackupScript) {
    Write-Host "[1/4] Creating TMS source-code backup..."
    & $BackupScript
} else {
    Write-Warning "backup_tms_for_chatgpt.ps1 not found. Source-code backup skipped."
}

Write-Host ""
Write-Host "[2/4] Checking/installing WSL build prerequisites..."
Write-Host "You may be asked for your LOCAL WSL password once."

# Run prerequisite setup separately so sudo has a normal interactive terminal.
$prereq = @'
set -e
need=0
for cmd in gcc pkg-config; do
    if ! command -v "$cmd" >/dev/null 2>&1; then need=1; fi
done

if ! pkg-config --exists cairo 2>/dev/null; then
    need=1
fi

if [ "$need" -eq 1 ]; then
    echo "Installing Linux build/runtime prerequisites required by TMS dependencies..."
    sudo apt-get update
    sudo apt-get install -y \
        build-essential \
        pkg-config \
        libcairo2-dev \
        libffi-dev \
        libjpeg-dev \
        zlib1g-dev \
        libpango1.0-dev \
        libgdk-pixbuf-2.0-dev \
        shared-mime-info
else
    echo "WSL build prerequisites already installed."
fi
'@

$TempPrereqWindows = Join-Path $env:TEMP "tms_chatgpt_prereqs.sh"
[System.IO.File]::WriteAllText(
    $TempPrereqWindows,
    ($prereq -replace "`r`n", "`n"),
    [System.Text.UTF8Encoding]::new($false)
)
$TempPrereqWsl = Convert-ToWslPath $TempPrereqWindows

try {
    & wsl.exe -d $Distro -- bash $TempPrereqWsl
    if ($LASTEXITCODE -ne 0) {
        throw "WSL prerequisite installation failed with exit code $LASTEXITCODE."
    }
}
finally {
    Remove-Item $TempPrereqWindows -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "[3/4] Building Linux dependency bundle..."

$bashTemplate = @'
set -euo pipefail

PROJECT="__PROJECT__"
PYVER="__PYVER__"
DEST_ZIP="__DEST_ZIP__"
OUT="$HOME/tms_linux_wheelhouse"
VENV="$HOME/tms_wheel_builder_312"

export PATH="$HOME/.local/bin:$PATH"

echo "Project: $PROJECT"
echo "Target Python: $PYVER"

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl is not installed even after prerequisite setup."
    exit 20
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "Ensuring Python $PYVER is available..."
uv python install "$PYVER"

rm -rf "$OUT" "$VENV" "$HOME/tms_linux_wheelhouse.zip"
mkdir -p "$OUT"

echo "Creating isolated Python $PYVER environment..."
uv venv "$VENV" --python "$PYVER" --seed
source "$VENV/bin/activate"

python --version
python -m pip --version

echo "Downloading exact TMS dependencies for Linux..."
python -m pip download \
    -r "$PROJECT/requirements.txt" \
    -d "$OUT" \
    --disable-pip-version-check

python - <<'PYCOUNT'
from pathlib import Path
out = Path.home() / "tms_linux_wheelhouse"
files = [p for p in out.iterdir() if p.is_file()]
print(f"Dependency files downloaded: {len(files)}")
if not files:
    raise SystemExit("ERROR: Dependency wheelhouse is empty.")
PYCOUNT

echo "Creating dependency ZIP..."
python - <<'PYZIP'
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

home = Path.home()
src = home / "tms_linux_wheelhouse"
dst = home / "tms_linux_wheelhouse.zip"
files = sorted(p for p in src.iterdir() if p.is_file())

if not files:
    raise SystemExit("ERROR: Refusing to create an empty wheelhouse ZIP.")

if dst.exists():
    dst.unlink()

with ZipFile(dst, "w", ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, f"tms_linux_wheelhouse/{p.name}")

print(f"Created {dst} with {len(files)} package files")
PYZIP

cp "$HOME/tms_linux_wheelhouse.zip" "$DEST_ZIP"
echo "Copied dependency ZIP to: $DEST_ZIP"
'@

$bash = $bashTemplate.
    Replace("__PROJECT__", $WslProject).
    Replace("__PYVER__", $PythonVersion).
    Replace("__DEST_ZIP__", $WheelZipWsl)

$TempBashWindows = Join-Path $env:TEMP "tms_chatgpt_handoff_build.sh"
[System.IO.File]::WriteAllText(
    $TempBashWindows,
    ($bash -replace "`r`n", "`n"),
    [System.Text.UTF8Encoding]::new($false)
)
$TempBashWsl = Convert-ToWslPath $TempBashWindows

try {
    & wsl.exe -d $Distro -- bash $TempBashWsl
    if ($LASTEXITCODE -ne 0) {
        throw "WSL dependency build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Remove-Item $TempBashWindows -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $WheelZipWindows)) {
    throw "WSL completed but $WheelZipWindows was not created."
}

Write-Host ""
Write-Host "[4/4] Validating dependency ZIP..."

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($WheelZipWindows)
try {
    $files = @($zip.Entries | Where-Object {
        $_.Length -gt 0 -and -not $_.FullName.EndsWith("/")
    })

    if ($files.Count -lt 1) {
        throw "Dependency ZIP exists but contains no package files."
    }

    $packageFiles = @($files | Where-Object {
        $_.Name -match '\.(whl|zip|tar\.gz|tgz|tar\.bz2)$'
    })

    $projectCode = @($files | Where-Object {
        $_.Name -match '\.(py|html|js|css)$'
    })

    Write-Host "Total dependency files : $($files.Count)"
    Write-Host "Package archives       : $($packageFiles.Count)"
    Write-Host "TMS code files         : $($projectCode.Count)"
    Write-Host "Wheelhouse ZIP         : $WheelZipWindows"

    if ($packageFiles.Count -lt 1) {
        throw "ZIP does not contain recognizable Python package archives."
    }

    if ($projectCode.Count -gt 0) {
        Write-Warning "Unexpected source-code-like files found. Inspect before uploading."
    } else {
        Write-Host "Privacy check          : PASS - no TMS project source code in dependency ZIP."
    }
}
finally {
    $zip.Dispose()
}

Write-Host ""
Write-Host "HANDOFF BUILD COMPLETE"
Write-Host ""
Write-Host "For future ChatGPT handoffs upload TWO files:"
Write-Host "  1. Latest TMS source backup from E:\TMS_Backups"
Write-Host "  2. $WheelZipWindows"
Write-Host ""
Write-Host "On later runs the Linux prerequisites and Python 3.12 are reused,"
Write-Host "so the process should require little or no setup."
