param(
    [string]$ProjectPath = "",
    [string]$OutputDir = "",
    [ValidateSet("Ask", "Yes", "No")][string]$DataMode = "Ask",
    [ValidateSet("Ask", "Yes", "No")][string]$VenvMode = "Ask",
    [string]$LiveContainer = "tms_db",
    [string]$DbName = "tenant_management",
    [string]$DbUser = "user",
    [string]$SharedDjangoPassword = "Share123!"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    throw $Message
}

function Format-Elapsed([TimeSpan]$Elapsed) {
    if ($Elapsed.TotalHours -ge 1) {
        return "{0:00}:{1:00}:{2:00}" -f [int]$Elapsed.TotalHours, $Elapsed.Minutes, $Elapsed.Seconds
    }
    return "{0:00}:{1:00}" -f [int]$Elapsed.TotalMinutes, $Elapsed.Seconds
}

function Resolve-Choice {
    param(
        [Parameter(Mandatory=$true)][string]$Mode,
        [Parameter(Mandatory=$true)][string]$Prompt,
        [bool]$Default = $false
    )

    if ($Mode -eq "Yes") { return $true }
    if ($Mode -eq "No") { return $false }

    $Suffix = if ($Default) { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        $Answer = (Read-Host "$Prompt $Suffix").Trim().ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($Answer)) { return $Default }
        if ($Answer -in @("y", "yes")) { return $true }
        if ($Answer -in @("n", "no")) { return $false }
        Write-Host "Please answer Y or N." -ForegroundColor Yellow
    }
}

function Invoke-ContainerDump {
    param(
        [Parameter(Mandatory=$true)][ValidateSet("schema","data")][string]$Mode,
        [Parameter(Mandatory=$true)][string]$OutFile,
        [Parameter(Mandatory=$true)][string]$Label,
        [Parameter(Mandatory=$true)][string]$LiveContainer,
        [Parameter(Mandatory=$true)][string]$DbName,
        [Parameter(Mandatory=$true)][string]$DbUser,
        [Parameter(Mandatory=$true)][string]$LivePassword
    )

    $Started = Get-Date
    $Token = [Guid]::NewGuid().ToString("N")

    $RemoteScript = "/tmp/tms_share_$Token.sh"
    $RemoteOut = "/tmp/tms_share_$Token.sql"
    $RemoteErr = "/tmp/tms_share_$Token.err"
    $RemoteCode = "/tmp/tms_share_$Token.code"
    $LocalScript = Join-Path $env:TEMP "tms_share_$Token.sh"

    $ShellScript = @'
#!/bin/sh
rm -f "$OUT" "$ERR" "$CODE"

if [ "$MODE" = "schema" ]; then
    mysqldump \
      -u "$DB_USER" \
      --default-character-set=utf8mb4 \
      --set-gtid-purged=OFF \
      --no-tablespaces \
      --skip-comments \
      --no-data \
      --skip-triggers \
      "$DB_NAME" > "$OUT" 2> "$ERR"
    RC=$?
else
    mysqldump \
      -u "$DB_USER" \
      --default-character-set=utf8mb4 \
      --set-gtid-purged=OFF \
      --no-tablespaces \
      --skip-comments \
      --no-create-info \
      --single-transaction \
      --quick \
      --skip-lock-tables \
      --extended-insert \
      --hex-blob \
      --ignore-table="${DB_NAME}.tenants_tenant" \
      "$DB_NAME" > "$OUT" 2> "$ERR"
    RC=$?
fi

echo "$RC" > "$CODE"
exit "$RC"
'@

    Set-Content -Path $LocalScript -Value $ShellScript -Encoding ASCII

    try {
        Write-Host "    $Label..." -ForegroundColor DarkGray

        & docker cp $LocalScript "${LiveContainer}:$RemoteScript" *> $null
        if ($LASTEXITCODE -ne 0) { Fail "Could not copy temporary dump helper into '$LiveContainer'." }

        & docker exec -d `
            -e "MYSQL_PWD=$LivePassword" `
            -e "DB_USER=$DbUser" `
            -e "DB_NAME=$DbName" `
            -e "MODE=$Mode" `
            -e "OUT=$RemoteOut" `
            -e "ERR=$RemoteErr" `
            -e "CODE=$RemoteCode" `
            $LiveContainer `
            sh $RemoteScript

        if ($LASTEXITCODE -ne 0) { Fail "Could not start $Label inside '$LiveContainer'." }

        $LastHeartbeat = Get-Date
        while ($true) {
            Start-Sleep -Seconds 2
            $Elapsed = Format-Elapsed ((Get-Date) - $Started)

            $SizeText = & docker exec `
                -e "OUT=$RemoteOut" `
                $LiveContainer `
                sh -c 'if [ -f "$OUT" ]; then wc -c < "$OUT"; else echo 0; fi' 2>$null

            $RemoteSize = 0L
            if ($null -ne $SizeText) {
                $LastSizeText = ($SizeText | Select-Object -Last 1)
                if ($null -ne $LastSizeText) {
                    [void][long]::TryParse($LastSizeText.ToString().Trim(), [ref]$RemoteSize)
                }
            }

            Write-Progress -Activity $Label -Status ("Elapsed {0} | Generated {1:N1} MB" -f $Elapsed, ($RemoteSize / 1MB))

            if (((Get-Date) - $LastHeartbeat).TotalSeconds -ge 10) {
                Write-Host ("      Working... elapsed {0}, generated {1:N1} MB" -f $Elapsed, ($RemoteSize / 1MB)) -ForegroundColor DarkGray
                $LastHeartbeat = Get-Date
            }

            & docker exec -e "CODE=$RemoteCode" $LiveContainer sh -c 'test -f "$CODE"' *> $null
            if ($LASTEXITCODE -eq 0) { break }
        }

        Write-Progress -Activity $Label -Completed
        $CodeText = & docker exec -e "CODE=$RemoteCode" $LiveContainer sh -c 'cat "$CODE"' 2>$null

        $ExitCode = -999
        if ($null -ne $CodeText) {
            $LastCodeText = ($CodeText | Select-Object -Last 1)
            if ($null -ne $LastCodeText) {
                [void][int]::TryParse($LastCodeText.ToString().Trim(), [ref]$ExitCode)
            }
        }

        if ($ExitCode -ne 0) {
            $ErrText = & docker exec -e "ERR=$RemoteErr" $LiveContainer sh -c 'if [ -f "$ERR" ]; then cat "$ERR"; fi' 2>$null
            $ErrJoined = ($ErrText -join "`n")
            if ([string]::IsNullOrWhiteSpace($ErrJoined)) { $ErrJoined = "mysqldump returned exit code $ExitCode without stderr." }
            Fail "$Label failed (exit $ExitCode).`n$ErrJoined"
        }

        & docker cp "${LiveContainer}:$RemoteOut" $OutFile
        if ($LASTEXITCODE -ne 0) { Fail "Could not copy $Label output from Docker." }
        if (-not (Test-Path $OutFile)) { Fail "$Label did not create an output file." }

        $Elapsed = Format-Elapsed ((Get-Date) - $Started)
        $Size = (Get-Item $OutFile).Length
        Write-Host ("    Done in {0} | {1:N2} MB" -f $Elapsed, ($Size / 1MB)) -ForegroundColor Green
    }
    finally {
        Remove-Item $LocalScript -Force -ErrorAction SilentlyContinue
        & docker exec `
            -e "SCRIPT=$RemoteScript" `
            -e "OUT=$RemoteOut" `
            -e "ERR=$RemoteErr" `
            -e "CODE=$RemoteCode" `
            $LiveContainer `
            sh -c 'rm -f "$SCRIPT" "$OUT" "$ERR" "$CODE"' *> $null
    }
}

# -----------------------------------------------------------------------------
# Resolve project root and environment
# -----------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    $GitRoot = (git rev-parse --show-toplevel 2>$null)
    if (-not $GitRoot) {
        Fail "Run this script inside the TMS Git repository or pass -ProjectPath."
    }
    $ProjectPath = $GitRoot.Trim()
}
else {
    $ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
}

if (-not (Test-Path -LiteralPath $ProjectPath)) { Fail "Project path not found: $ProjectPath" }

$Python = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    Fail "Project virtual-environment Python not found: $Python"
}

$ProjectName = Split-Path $ProjectPath -Leaf
$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$Commit = (git -C $ProjectPath rev-parse --short HEAD).Trim()
$Branch = (git -C $ProjectPath branch --show-current).Trim()

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path (Split-Path $ProjectPath -Parent) "TMS_Backups"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$IncludeSanitizedData = Resolve-Choice -Mode $DataMode -Prompt "Include SANITIZED database data in this ZIP?" -Default $false
$IncludeVenv = Resolve-Choice -Mode $VenvMode -Prompt "Include the Windows .venv in this ZIP? (larger/slower)" -Default $false

$DataTag = if ($IncludeSanitizedData) { "DATA" } else { "NODATA" }
$VenvTag = if ($IncludeVenv) { "VENV" } else { "NOVENV" }
$ZipName = "TMS_${Timestamp}_${Commit}_${DataTag}_${VenvTag}.zip"
$ZipPath = Join-Path $OutputDir $ZipName

$TempRoot = Join-Path $env:TEMP "tms_chatgpt_export_$Timestamp"
$ExportRoot = Join-Path $TempRoot $ProjectName
$MetaRoot = Join-Path $ExportRoot "_chatgpt_export"

if (Test-Path -LiteralPath $TempRoot) { Remove-Item -LiteralPath $TempRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ExportRoot | Out-Null
New-Item -ItemType Directory -Force -Path $MetaRoot | Out-Null

$StartedAll = Get-Date
$LivePassword = $null

Write-Host ""
Write-Host "TMS CHATGPT UNIFIED BACKUP" -ForegroundColor Green
Write-Host "--------------------------" -ForegroundColor Green
Write-Host "Project         : $ProjectPath"
Write-Host "Branch          : $Branch"
Write-Host "Commit          : $Commit"
Write-Host "Sanitized data  : $IncludeSanitizedData"
Write-Host "Include .venv   : $IncludeVenv"
Write-Host "Output          : $ZipPath"
Write-Host ""

try {
    Write-Step "[1] Copying current Git-tracked project files"

    $CopyTrackedCode = @'
import os
import shutil
import subprocess
import sys

repo = sys.argv[1]
dest = sys.argv[2]
raw = subprocess.check_output(["git", "-C", repo, "ls-files", "-z"])
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

    $CopyTrackedCode | & $Python - $ProjectPath $ExportRoot
    if ($LASTEXITCODE -ne 0) { Fail "Git file export failed." }

    Write-Step "[2] Capturing environment and Git handoff information"

    & $Python -m pip freeze | Set-Content -Encoding UTF8 (Join-Path $MetaRoot "requirements_full.txt")
    & $Python --version 2>&1 | Set-Content -Encoding UTF8 (Join-Path $MetaRoot "python_version.txt")
    & $Python -m django --version 2>&1 | Set-Content -Encoding UTF8 (Join-Path $MetaRoot "django_version.txt")

    $Status = git -C $ProjectPath status --short
    $RecentCommits = git -C $ProjectPath log -10 --oneline --decorate

    $Handoff = @"
TMS PROJECT HANDOFF
===================

Created: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Branch:  $Branch
Commit:  $Commit
Sanitized data included: $IncludeSanitizedData
Windows .venv included: $IncludeVenv

SOURCE FILE POLICY
- Current working copies of Git-tracked files are included.
- Modified tracked files are included even if not committed yet.
- Untracked project files are excluded unless generated by this export script.
- Production .env, logs, media/private uploads, old ZIPs, and local DB files are not intentionally included.

VIRTUAL ENVIRONMENT NOTE
- If included, .venv is the LOCAL WINDOWS virtual environment.
- It is useful for Windows recovery/reproduction.
- A Windows .venv cannot execute directly on Linux/macOS.
- _chatgpt_export/requirements_full.txt is always included as the portable dependency record.

Git status at export time:
--------------------------
$($Status -join "`r`n")

Recent commits:
---------------
$($RecentCommits -join "`r`n")
"@
    Set-Content -LiteralPath (Join-Path $MetaRoot "TMS_CHATGPT_HANDOFF.txt") -Value $Handoff -Encoding UTF8

    if ($IncludeVenv) {
        Write-Step "[3] Copying Windows .venv (excluding caches only)"
        $SourceVenv = Join-Path $ProjectPath ".venv"
        $DestVenv = Join-Path $ExportRoot ".venv"

        $CopyVenvCode = @'
import os
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

if not src.is_dir():
    raise SystemExit(f"Virtual environment not found: {src}")

def ignore(directory, names):
    ignored = set()
    for name in names:
        low = name.lower()
        if low == "__pycache__" or low in {"pip-cache", ".pytest_cache"}:
            ignored.add(name)
        elif low.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored

shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)
files = 0
size = 0
for root, _, names in os.walk(dst):
    for n in names:
        p = os.path.join(root, n)
        try:
            size += os.path.getsize(p)
            files += 1
        except OSError:
            pass
print(f".venv copied: {files} files, {size / (1024*1024):.2f} MB")
'@
        $CopyVenvCode | & $Python - $SourceVenv $DestVenv
        if ($LASTEXITCODE -ne 0) { Fail "Virtual environment copy failed." }
    }
    else {
        Write-Step "[3] Skipping .venv (requirements snapshot is still included)"
    }

    if ($IncludeSanitizedData) {
        Write-Step "[4] Validating Docker/MySQL and reading Django DB credentials"

        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker is not available in PowerShell." }
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop is not running." }

        $LiveRunning = (& docker inspect -f "{{.State.Running}}" $LiveContainer 2>$null)
        if ($LASTEXITCODE -ne 0 -or $LiveRunning.Trim() -ne "true") {
            Fail "Live database container '$LiveContainer' is not running."
        }

        $DbProbeCode = @'
import json
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tms.settings")
import django
django.setup()
from django.conf import settings
d = settings.DATABASES["default"]
print(json.dumps({
    "NAME": d.get("NAME") or "",
    "USER": d.get("USER") or "",
    "PASSWORD": d.get("PASSWORD") or "",
}))
'@

        $PreviousLocation = Get-Location
        try {
            Set-Location $ProjectPath
            $DbJson = $DbProbeCode | & $Python -
            $DbProbeExitCode = $LASTEXITCODE
        }
        finally {
            Set-Location $PreviousLocation
        }

        if ($DbProbeExitCode -ne 0 -or -not $DbJson) { Fail "Could not read database credentials from Django settings." }
        try { $DbCfg = $DbJson | ConvertFrom-Json } catch { Fail "Django returned invalid database credential data." }

        if ($DbCfg.NAME) { $DbName = [string]$DbCfg.NAME }
        if ($DbCfg.USER) { $DbUser = [string]$DbCfg.USER }
        $LivePassword = [string]$DbCfg.PASSWORD
        if (-not $DbName -or -not $DbUser -or -not $LivePassword) { Fail "Django database NAME/USER/PASSWORD is incomplete." }

        & docker exec -e "MYSQL_PWD=$LivePassword" $LiveContainer mysql -u $DbUser -D $DbName -N -e "SELECT 1;" *> $null
        if ($LASTEXITCODE -ne 0) { Fail "Django's configured MySQL credentials were rejected by '$LiveContainer'." }

        $DataRoot = Join-Path $MetaRoot "sanitized_data"
        New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null

        $SchemaDump = Join-Path $TempRoot "01_schema.sql"
        $DataDump = Join-Path $TempRoot "02_data_without_tenant.sql"
        $TenantDump = Join-Path $TempRoot "03_tenant_sanitized.sql"
        $FinalDump = Join-Path $DataRoot "tms_sanitized.sql"
        $GeneratorPy = Join-Path $TempRoot "generate_sanitized_tenant.py"

        Write-Step "[5] Dumping schema and all NON-tenant data"
        Invoke-ContainerDump -Mode "schema" -OutFile $SchemaDump -Label "Dumping database schema" -LiveContainer $LiveContainer -DbName $DbName -DbUser $DbUser -LivePassword $LivePassword
        Invoke-ContainerDump -Mode "data" -OutFile $DataDump -Label "Dumping all data except tenants_tenant" -LiveContainer $LiveContainer -DbName $DbName -DbUser $DbUser -LivePassword $LivePassword

        Write-Step "[6] Sanitizing tenants_tenant and resetting Django passwords"

        $SanitizerCode = @'
import os
import sys
import hashlib
import hmac
from pathlib import Path

PROJECT_PATH = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(PROJECT_PATH))
os.chdir(PROJECT_PATH)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tms.settings")

import django
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import connection
from pymysql.converters import escape_item, conversions

OUT = Path(sys.argv[1])
SHARED_PASSWORD = sys.argv[2]
KEY = b"TMS-TENANT-ONLY-SAFE-SHARE-V7"

NAME_FIELDS = {"first_name", "last_name", "father_husband_name", "emergency_contact_name", "reference_name_1", "reference_name_2"}
PHONE_FIELDS = {"phone", "phone2", "phone3", "emergency_contact_phone"}
ADDRESS_FIELDS = {"address", "temporary_address", "temporary_address_urdu", "permanent_address", "permanent_address_urdu", "working_address"}
FILE_FIELDS = {"photo", "photo_crop", "cnic_front", "cnic_back", "cnic_front_crop", "cnic_back_crop"}
TEXT_FIELDS = {"notes"}
EMAIL_FIELDS = {"email"}
CNIC_FIELDS = {"cnic", "cnic_digits"}

def dg(v, ns=""):
    raw = f"{ns}|{v}".encode("utf-8", "ignore")
    return hmac.new(KEY, raw, hashlib.sha256).hexdigest()

def fake_digits(v, n, ns):
    num = str(int(dg(v, ns), 16))
    while len(num) < n:
        num += num
    return num[:n]

def fake_name(v, col):
    if not v: return v
    h = dg(v, col)
    if col == "first_name": return "First" + h[:6]
    if col == "last_name": return "Last" + h[:6]
    return "Person " + h[:8]

def fake_phone(v):
    if not v: return v
    return "+92" + fake_digits(str(v), 10, "phone")

def fake_cnic(v, digits_only=False):
    if not v: return v
    d = fake_digits(str(v), 13, "cnic")
    if digits_only: return d
    return f"{d[:5]}-{d[5:12]}-{d[12]}"

def fake_email(v):
    if not v: return v
    return "tenant_" + dg(v, "email")[:12] + "@example.invalid"

def fake_file(v, col):
    if not v: return v
    s = str(v)
    ext = ""
    if "." in s.rsplit("/", 1)[-1]:
        ext = "." + s.rsplit(".", 1)[-1][:8].lower()
    return f"sanitized/{dg(s, col)[:18]}{ext}"

def scrub_text(v):
    if not v: return v
    s = str(v)
    marker = "[SANITIZED]"
    if len(s) <= len(marker): return marker[:len(s)]
    out = marker
    while len(out) < len(s): out += " safe-data"
    return out[:len(s)]

def sanitize(col, v):
    if v is None: return None
    c = col.lower()
    if c in NAME_FIELDS: return fake_name(v, c)
    if c in PHONE_FIELDS: return fake_phone(v)
    if c in CNIC_FIELDS: return fake_cnic(v, c.endswith("digits"))
    if c in EMAIL_FIELDS: return fake_email(v)
    if c in ADDRESS_FIELDS: return scrub_text(v)
    if c in FILE_FIELDS: return fake_file(v, c)
    if c in TEXT_FIELDS: return scrub_text(v)
    return v

def sql_quote(v):
    if v is None: return "NULL"
    return escape_item(v, "utf8mb4", conversions)

def qident(name):
    return "`" + str(name).replace("`", "``") + "`"

with connection.cursor() as cur:
    cur.execute("""
        SELECT COLUMN_NAME, EXTRA
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME='tenants_tenant'
        ORDER BY ORDINAL_POSITION
    """, [connection.settings_dict["NAME"]])
    cols = [r[0] for r in cur.fetchall() if "GENERATED" not in (r[1] or "").upper()]
    if not cols:
        raise RuntimeError("tenants_tenant columns were not found.")
    quoted_cols = ", ".join(f"`{c}`" for c in cols)
    cur.execute(f"SELECT {quoted_cols} FROM `tenants_tenant` ORDER BY `id`")
    rows = cur.fetchall()

password_hash = make_password(SHARED_PASSWORD)

with OUT.open("w", encoding="utf-8", newline="\n") as f:
    f.write("\n-- Sanitized tenants_tenant data generated read-only\n")
    f.write("SET FOREIGN_KEY_CHECKS=0;\n")
    f.write("LOCK TABLES `tenants_tenant` WRITE;\n")
    batch = []
    for i, row in enumerate(rows, start=1):
        sanitized = [sanitize(col, val) for col, val in zip(cols, row)]
        batch.append("(" + ",".join(sql_quote(v) for v in sanitized) + ")")
        if len(batch) >= 100 or i == len(rows):
            f.write("INSERT INTO `tenants_tenant` (" + quoted_cols + ") VALUES\n" + ",\n".join(batch) + ";\n")
            batch.clear()
    f.write("UNLOCK TABLES;\n")

    UserModel = get_user_model()
    user_table = UserModel._meta.db_table
    username_field = UserModel.USERNAME_FIELD
    pk_column = UserModel._meta.pk.column
    f.write("\n-- Reset Django passwords for safe test login\n")
    f.write("SET @safe_password = " + sql_quote(password_hash) + ";\n")
    f.write("UPDATE " + qident(user_table) + " SET " + qident(UserModel._meta.get_field("password").column) + "=@safe_password;\n")

    field_names = {field.name for field in UserModel._meta.get_fields()}
    if "is_superuser" in field_names and username_field:
        username_column = UserModel._meta.get_field(username_field).column
        superuser_column = UserModel._meta.get_field("is_superuser").column
        f.write("SET @admin_id = (SELECT " + qident(pk_column) + " FROM " + qident(user_table) + " WHERE " + qident(superuser_column) + "=1 ORDER BY " + qident(pk_column) + " LIMIT 1);\n")
        f.write("UPDATE " + qident(user_table) + " SET " + qident(username_column) + "='admin' WHERE " + qident(pk_column) + "=@admin_id;\n")
    f.write("SET FOREIGN_KEY_CHECKS=1;\n")

print(f"Sanitized tenant rows: {len(rows)}")
print(f"Tenant columns preserved: {len(cols)}")
print(f"Output: {OUT}")
'@

        Set-Content -Path $GeneratorPy -Value $SanitizerCode -Encoding UTF8
        Push-Location $ProjectPath
        try {
            & $Python $GeneratorPy $TenantDump $SharedDjangoPassword $ProjectPath | Tee-Object -FilePath (Join-Path $DataRoot "sanitization_report.txt")
            if ($LASTEXITCODE -ne 0) { Fail "Tenant sanitizer failed." }
        }
        finally { Pop-Location }

        Write-Step "[7] Building final sanitized SQL and safety-scanning it"
        $OutStream = [System.IO.File]::Create($FinalDump)
        try {
            foreach ($Part in @($SchemaDump, $DataDump, $TenantDump)) {
                $InStream = [System.IO.File]::OpenRead($Part)
                try {
                    $InStream.CopyTo($OutStream)
                    $Newline = [System.Text.Encoding]::UTF8.GetBytes("`n")
                    $OutStream.Write($Newline, 0, $Newline.Length)
                }
                finally { $InStream.Dispose() }
            }
        }
        finally { $OutStream.Dispose() }

        $SafetyCode = @'
import re
import sys
from pathlib import Path
p = Path(sys.argv[1])
text = p.read_text(encoding="utf-8", errors="ignore")
patterns = {
    "OpenAI key": r"\bsk-[A-Za-z0-9_-]{20,}\b",
    "Meta long token": r"\bEAA[A-Za-z0-9]{40,}\b",
    "Private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
}
found = [name for name, pat in patterns.items() if re.search(pat, text)]
if found:
    print("SAFETY SCAN FAILED:")
    for x in found: print(" -", x)
    sys.exit(2)
print("Safety scan passed: no obvious API/private-key patterns found.")
'@
        $SafetyPy = Join-Path $TempRoot "safety_scan.py"
        Set-Content -Path $SafetyPy -Value $SafetyCode -Encoding UTF8
        & $Python $SafetyPy $FinalDump | Tee-Object -FilePath (Join-Path $DataRoot "safety_scan.txt")
        if ($LASTEXITCODE -ne 0) { Fail "Safety scan failed. ZIP was not created." }

        $DockerCompose = @"
services:
  db:
    image: mysql:8.0
    container_name: tms_test_db
    restart: unless-stopped
    environment:
      MYSQL_DATABASE: tenant_management
      MYSQL_USER: tms_test
      MYSQL_PASSWORD: tms_test_password
      MYSQL_ROOT_PASSWORD: tms_test_root_password
    ports:
      - "6604:3306"
    volumes:
      - tms_test_mysql:/var/lib/mysql
      - ./tms_sanitized.sql:/docker-entrypoint-initdb.d/01_tms_sanitized.sql:ro
    healthcheck:
      test: ['CMD-SHELL', 'mysqladmin ping -h 127.0.0.1 -uroot -ptms_test_root_password --silent']
      interval: 5s
      timeout: 5s
      retries: 30
      start_period: 15s
volumes:
  tms_test_mysql:
"@
        Set-Content -Path (Join-Path $DataRoot "docker-compose.yml") -Value $DockerCompose -Encoding UTF8

        $EnvExample = @"
DB_ENGINE=django.db.backends.mysql
DB_NAME=tenant_management
DB_USER=tms_test
DB_PASSWORD=tms_test_password
DB_HOST=127.0.0.1
DB_PORT=6604
DEBUG=True
SECRET_KEY=tms-sanitized-test-only
"@
        Set-Content -Path (Join-Path $DataRoot ".env.example") -Value $EnvExample -Encoding UTF8

        $ReadmeData = @"
TMS SANITIZED TEST DATA
=======================

Created: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Source commit: $Commit

Sanitization policy:
- ONLY tenants_tenant is anonymized.
- Other tables are preserved for realistic performance/testing data.
- Django passwords are reset to: $SharedDjangoPassword
- One existing custom superuser is renamed to: admin

PRIVACY WARNING:
Other tables can still contain personal information. Share this DATA package only when you accept that tradeoff.

The production database is read-only during this export. Production DB credentials are not written into this ZIP.

Quick test DB setup from this directory:
    docker compose up -d

Reset/reimport:
    docker compose down -v
    docker compose up -d
"@
        Set-Content -Path (Join-Path $DataRoot "README_SANITIZED_DATA.txt") -Value $ReadmeData -Encoding UTF8
    }
    else {
        Write-Step "[4-7] Sanitized data not requested; Docker/database access skipped"
    }

    Write-Step "[8] Scanning exported project text for obvious secrets"

    $ProjectSafetyCode = @'
import os
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
patterns = {
    "OpenAI key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "Meta long token": re.compile(r"\bEAA[A-Za-z0-9]{40,}\b"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
skip_parts = {".venv", ".git", "node_modules", "staticfiles"}
text_exts = {".py", ".txt", ".md", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".ps1", ".sh", ".html", ".js", ".css", ".env"}
found = []
for p in root.rglob("*"):
    if not p.is_file():
        continue
    if any(part in skip_parts for part in p.parts):
        continue
    if p.suffix.lower() not in text_exts and p.name != ".env":
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for name, pat in patterns.items():
        if pat.search(text):
            found.append((name, str(p.relative_to(root))))

if found:
    print("WARNING: obvious secret-like patterns found in exported tracked files:")
    for name, rel in found:
        print(f" - {name}: {rel}")
    # Do not automatically delete or mutate tracked source. Fail closed instead.
    sys.exit(2)
print("Project safety scan passed: no obvious API/private-key patterns found outside .venv.")
'@

    $ProjectSafetyPy = Join-Path $TempRoot "project_safety_scan.py"
    Set-Content -Path $ProjectSafetyPy -Value $ProjectSafetyCode -Encoding UTF8
    & $Python $ProjectSafetyPy $ExportRoot | Tee-Object -FilePath (Join-Path $MetaRoot "project_safety_scan.txt")
    if ($LASTEXITCODE -ne 0) {
        Fail "Safety scan found an obvious secret-like pattern in exported tracked files. Review the report; ZIP was not created."
    }

    Write-Step "[9] Creating final ZIP"
    if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }

    Compress-Archive -Path (Join-Path $ExportRoot "*") -DestinationPath $ZipPath -CompressionLevel Fastest
    if (-not (Test-Path -LiteralPath $ZipPath)) { Fail "ZIP creation failed." }

    $SizeMB = [math]::Round((Get-Item -LiteralPath $ZipPath).Length / 1MB, 2)
    $TotalElapsed = Format-Elapsed ((Get-Date) - $StartedAll)

    Write-Host ""
    Write-Host "BACKUP COMPLETE" -ForegroundColor Green
    Write-Host "File : $ZipPath" -ForegroundColor Green
    Write-Host "Size : $SizeMB MB"
    Write-Host "Mode : $DataTag / $VenvTag"
    Write-Host "Time : $TotalElapsed"
    if ($IncludeSanitizedData) {
        Write-Host "Test login: admin / $SharedDjangoPassword" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "Upload this ONE ZIP to ChatGPT for the next TMS handoff."
}
finally {
    $LivePassword = $null
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
