param(
    [string]$ProjectPath = "E:\tenant_management_system",
    [string]$LiveContainer = "tms_db",
    [string]$DbName = "tenant_management",
    [string]$DbUser = "user",
    [string]$OutputRoot = "E:\TMS_Share_Exports",
    [string]$SharedDjangoPassword = "Share123!"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Format-Elapsed([TimeSpan]$Elapsed) {
    if ($Elapsed.TotalHours -ge 1) {
        return "{0:00}:{1:00}:{2:00}" -f [int]$Elapsed.TotalHours, $Elapsed.Minutes, $Elapsed.Seconds
    }
    return "{0:00}:{1:00}" -f [int]$Elapsed.TotalMinutes, $Elapsed.Seconds
}

function Fail([string]$Message) {
    throw $Message
}

function ConvertTo-PlainText([Security.SecureString]$SecureValue) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
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

    # Build the Linux helper as a real file instead of trying to nest shell
    # quoting inside PowerShell. This is compatible with Windows PowerShell 5.1.
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
        if ($LASTEXITCODE -ne 0) {
            Fail "Could not copy temporary dump helper into '$LiveContainer'."
        }

        # Docker's -d mode runs the command detached. This avoids all of the
        # Windows Start-Process/ExitCode problems from earlier versions.
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

        if ($LASTEXITCODE -ne 0) {
            Fail "Could not start $Label inside '$LiveContainer'."
        }

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

            Write-Progress `
                -Activity $Label `
                -Status ("Elapsed {0} | Generated {1:N1} MB" -f $Elapsed, ($RemoteSize / 1MB))

            if (((Get-Date) - $LastHeartbeat).TotalSeconds -ge 10) {
                Write-Host ("      Working... elapsed {0}, generated {1:N1} MB" -f $Elapsed, ($RemoteSize / 1MB)) -ForegroundColor DarkGray
                $LastHeartbeat = Get-Date
            }

            & docker exec `
                -e "CODE=$RemoteCode" `
                $LiveContainer `
                sh -c 'test -f "$CODE"' *> $null

            if ($LASTEXITCODE -eq 0) {
                break
            }
        }

        Write-Progress -Activity $Label -Completed

        $CodeText = & docker exec `
            -e "CODE=$RemoteCode" `
            $LiveContainer `
            sh -c 'cat "$CODE"' 2>$null

        $ExitCode = -999
        if ($null -ne $CodeText) {
            $LastCodeText = ($CodeText | Select-Object -Last 1)
            if ($null -ne $LastCodeText) {
                [void][int]::TryParse($LastCodeText.ToString().Trim(), [ref]$ExitCode)
            }
        }

        if ($ExitCode -ne 0) {
            $ErrText = & docker exec `
                -e "ERR=$RemoteErr" `
                $LiveContainer `
                sh -c 'if [ -f "$ERR" ]; then cat "$ERR"; fi' 2>$null

            $ErrJoined = ($ErrText -join "`n")
            if ([string]::IsNullOrWhiteSpace($ErrJoined)) {
                $ErrJoined = "mysqldump returned exit code $ExitCode without stderr."
            }

            Fail "$Label failed (exit $ExitCode).`n$ErrJoined"
        }

        & docker cp "${LiveContainer}:$RemoteOut" $OutFile
        if ($LASTEXITCODE -ne 0) {
            Fail "Could not copy $Label output from Docker."
        }

        if (-not (Test-Path $OutFile)) {
            Fail "$Label did not create an output file."
        }

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

$ScriptStartedAt = Get-Date

Write-Host ""
Write-Host "TMS SAFE SHARE EXPORT v16 REPRO-HEALTHCHECK-FIX" -ForegroundColor Green
Write-Host "Sanitizes ONLY tenants_tenant and resets Django passwords." -ForegroundColor Green
Write-Host "No temporary MySQL clone/import is created." -ForegroundColor Yellow
Write-Host "The live database is READ ONLY." -ForegroundColor Yellow
Write-Host ""

Write-Step "[1/7] Validating project and tools"

if (-not (Test-Path $ProjectPath)) {
    Fail "Project path not found: $ProjectPath"
}

$Python = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Fail "Virtual environment Python not found: $Python"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker is not available in PowerShell."
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "Docker Desktop is not running."
}

$LiveRunning = (& docker inspect -f "{{.State.Running}}" $LiveContainer 2>$null)
if ($LASTEXITCODE -ne 0 -or $LiveRunning.Trim() -ne "true") {
    Fail "Live database container '$LiveContainer' is not running."
}

$Image = (& docker inspect -f "{{.Config.Image}}" $LiveContainer).Trim()

Write-Host "Project       : $ProjectPath"
Write-Host "DB container  : $LiveContainer"
Write-Host "Docker image  : $Image"

# Read the exact DB credentials Django is already using.
# The password is captured only in process memory and is never written to the ZIP.
Write-Host "Reading DB credentials from Django settings..." -ForegroundColor DarkGray

# Pass the Python helper through STDIN instead of python -c.
# This avoids PowerShell/native-command quote mangling on Windows.
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

if ($DbProbeExitCode -ne 0 -or -not $DbJson) {
    Fail "Could not read database credentials from Django settings."
}

try {
    $DbCfg = $DbJson | ConvertFrom-Json
}
catch {
    Fail "Django returned invalid database credential data."
}

if ($DbCfg.NAME) { $DbName = [string]$DbCfg.NAME }
if ($DbCfg.USER) { $DbUser = [string]$DbCfg.USER }
$LivePassword = [string]$DbCfg.PASSWORD

if (-not $DbName -or -not $DbUser -or -not $LivePassword) {
    Fail "Django database NAME/USER/PASSWORD is incomplete."
}

Write-Host "DB name       : $DbName"
Write-Host "DB user       : $DbUser"
Write-Host "DB password   : loaded from Django settings (hidden)" -ForegroundColor DarkGray

# Validate credentials BEFORE creating any dump files.
Write-Host "Validating MySQL login..." -ForegroundColor DarkGray
& docker exec -e "MYSQL_PWD=$LivePassword" $LiveContainer `
    mysql -u $DbUser -D $DbName -N -e "SELECT 1;" *> $null

if ($LASTEXITCODE -ne 0) {
    Fail "Django's configured MySQL credentials were rejected by container '$LiveContainer'. Check that Django and tms_db point to the same database."
}

Write-Host "MySQL login OK." -ForegroundColor Green

$Stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$WorkDir = Join-Path $env:TEMP "TMS_FastShare_$Stamp"
$PackageDir = Join-Path $WorkDir "TMS_Sanitized_$Stamp"
$SchemaDump = Join-Path $WorkDir "01_schema.sql"
$DataDump = Join-Path $WorkDir "02_data_without_tenant.sql"
$TenantDump = Join-Path $WorkDir "03_tenant_sanitized.sql"
$FinalDump = Join-Path $PackageDir "tms_sanitized.sql"
$GeneratorPy = Join-Path $WorkDir "generate_sanitized_tenant.py"
$ZipPath = Join-Path $OutputRoot "TMS_SANITIZED_FAST_$Stamp.zip"

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

try {
    Write-Step "[2/7] Capturing Django/Python/Git/MySQL environment"

    & $Python -m pip freeze | Set-Content -Encoding UTF8 (Join-Path $PackageDir "requirements_full.txt")
    & $Python --version 2>&1 | Set-Content -Encoding UTF8 (Join-Path $PackageDir "python_version.txt")
    & $Python -m django --version 2>&1 | Set-Content -Encoding UTF8 (Join-Path $PackageDir "django_version.txt")
    (& docker exec $LiveContainer mysql --version 2>&1) |
        Set-Content -Encoding UTF8 (Join-Path $PackageDir "mysql_version.txt")

    Push-Location $ProjectPath
    try {
        if (Test-Path ".git") {
            (& git rev-parse HEAD 2>&1) | Set-Content -Encoding UTF8 (Join-Path $PackageDir "git_commit.txt")
            (& git status --short 2>&1) | Set-Content -Encoding UTF8 (Join-Path $PackageDir "git_status.txt")
        }
        if (Test-Path "requirements.txt") {
            Copy-Item "requirements.txt" (Join-Path $PackageDir "requirements.txt")
        }
    }
    finally {
        Pop-Location
    }

    Write-Step "[3/7] Dumping schema and all NON-tenant data"

    Invoke-ContainerDump `
        -Mode "schema" `
        -OutFile $SchemaDump `
        -Label "Dumping database schema" `
        -LiveContainer $LiveContainer `
        -DbName $DbName `
        -DbUser $DbUser `
        -LivePassword $LivePassword

    Invoke-ContainerDump `
        -Mode "data" `
        -OutFile $DataDump `
        -Label "Dumping all data except tenants_tenant" `
        -LiveContainer $LiveContainer `
        -DbName $DbName `
        -DbUser $DbUser `
        -LivePassword $LivePassword

    Write-Step "[4/7] Reading + sanitizing tenants_tenant directly"

    $PythonCode = @'
import os
import sys
import hashlib
import hmac
from pathlib import Path

# The sanitizer script itself lives under %TEMP%, so Python would otherwise
# search that directory first and fail to import the project's `tms` package.
PROJECT_PATH = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(PROJECT_PATH))
os.chdir(PROJECT_PATH)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tms.settings")

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import connection
from pymysql.converters import escape_item, conversions

OUT = Path(sys.argv[1])
SHARED_PASSWORD = sys.argv[2]
KEY = b"TMS-TENANT-ONLY-SAFE-SHARE-V7"

NAME_FIELDS = {
    "first_name", "last_name", "father_husband_name",
    "emergency_contact_name", "reference_name_1", "reference_name_2",
}
PHONE_FIELDS = {
    "phone", "phone2", "phone3", "emergency_contact_phone",
}
ADDRESS_FIELDS = {
    "address", "temporary_address", "temporary_address_urdu",
    "permanent_address", "permanent_address_urdu",
    "working_address",
}
FILE_FIELDS = {
    "photo", "photo_crop", "cnic_front", "cnic_back",
    "cnic_front_crop", "cnic_back_crop",
}
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
    if not v:
        return v
    h = dg(v, col)
    if col == "first_name":
        return "First" + h[:6]
    if col == "last_name":
        return "Last" + h[:6]
    return "Person " + h[:8]

def fake_phone(v):
    if not v:
        return v
    return "+92" + fake_digits(str(v), 10, "phone")

def fake_cnic(v, digits_only=False):
    if not v:
        return v
    d = fake_digits(str(v), 13, "cnic")
    if digits_only:
        return d
    return f"{d[:5]}-{d[5:12]}-{d[12]}"

def fake_email(v):
    if not v:
        return v
    return "tenant_" + dg(v, "email")[:12] + "@example.invalid"

def fake_file(v, col):
    if not v:
        return v
    s = str(v)
    ext = ""
    if "." in s.rsplit("/", 1)[-1]:
        ext = "." + s.rsplit(".", 1)[-1][:8].lower()
    return f"sanitized/{dg(s, col)[:18]}{ext}"

def scrub_text(v):
    if not v:
        return v
    s = str(v)
    marker = "[SANITIZED]"
    if len(s) <= len(marker):
        return marker[:len(s)]
    out = marker
    while len(out) < len(s):
        out += " safe-data"
    return out[:len(s)]

def sanitize(col, v):
    if v is None:
        return None
    c = col.lower()

    if c in NAME_FIELDS:
        return fake_name(v, c)
    if c in PHONE_FIELDS:
        return fake_phone(v)
    if c in CNIC_FIELDS:
        return fake_cnic(v, c.endswith("digits"))
    if c in EMAIL_FIELDS:
        return fake_email(v)
    if c in ADDRESS_FIELDS:
        return scrub_text(v)
    if c in FILE_FIELDS:
        return fake_file(v, c)
    if c in TEXT_FIELDS:
        return scrub_text(v)
    return v

def sql_quote(v):
    if v is None:
        return "NULL"
    return escape_item(v, "utf8mb4", conversions)

with connection.cursor() as cur:
    cur.execute("""
        SELECT COLUMN_NAME, EXTRA
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME='tenants_tenant'
        ORDER BY ORDINAL_POSITION
    """, [connection.settings_dict["NAME"]])
    cols = [
        r[0] for r in cur.fetchall()
        if "GENERATED" not in (r[1] or "").upper()
    ]

    if not cols:
        raise RuntimeError("tenants_tenant columns were not found.")

    quoted_cols = ", ".join(f"`{c}`" for c in cols)
    cur.execute(f"SELECT {quoted_cols} FROM `tenants_tenant` ORDER BY `id`")
    rows = cur.fetchall()

password_hash = make_password(SHARED_PASSWORD)

with OUT.open("w", encoding="utf-8", newline="\n") as f:
    f.write("\n-- ------------------------------------------------------\n")
    f.write("-- Sanitized tenants_tenant data generated read-only\n")
    f.write("-- ------------------------------------------------------\n")
    f.write("SET FOREIGN_KEY_CHECKS=0;\n")
    f.write("LOCK TABLES `tenants_tenant` WRITE;\n")

    batch = []
    batch_size = 100

    for i, row in enumerate(rows, start=1):
        sanitized = [sanitize(col, val) for col, val in zip(cols, row)]
        values = "(" + ",".join(sql_quote(v) for v in sanitized) + ")"
        batch.append(values)

        if len(batch) >= batch_size or i == len(rows):
            f.write(
                "INSERT INTO `tenants_tenant` (" + quoted_cols + ") VALUES\n"
                + ",\n".join(batch)
                + ";\n"
            )
            batch.clear()

    f.write("UNLOCK TABLES;\n")

    # Reset passwords in the *actual* configured Django user table.
    # This avoids assuming the project uses accounts_account or auth_user.
    UserModel = get_user_model()
    user_table = UserModel._meta.db_table
    username_field = UserModel.USERNAME_FIELD
    pk_column = UserModel._meta.pk.column

    def qident(name):
        return "`" + str(name).replace("`", "``") + "`"

    f.write("\n-- Reset Django passwords for safe test login\n")
    f.write("SET @safe_password = " + sql_quote(password_hash) + ";\n")
    f.write(
        "UPDATE " + qident(user_table)
        + " SET " + qident(UserModel._meta.get_field("password").column)
        + "=@safe_password;\n"
    )

    # Make one superuser easy to use for local performance testing.
    field_names = {field.name for field in UserModel._meta.get_fields()}
    if "is_superuser" in field_names and username_field:
        username_column = UserModel._meta.get_field(username_field).column
        superuser_column = UserModel._meta.get_field("is_superuser").column
        f.write(
            "SET @admin_id = (SELECT " + qident(pk_column)
            + " FROM " + qident(user_table)
            + " WHERE " + qident(superuser_column) + "=1"
            + " ORDER BY " + qident(pk_column) + " LIMIT 1);\n"
        )
        f.write(
            "UPDATE " + qident(user_table)
            + " SET " + qident(username_column) + "='admin'"
            + " WHERE " + qident(pk_column) + "=@admin_id;\n"
        )

    f.write("SET FOREIGN_KEY_CHECKS=1;\n")

print(f"Sanitized tenant rows: {len(rows)}")
print(f"Tenant columns preserved: {len(cols)}")
print(f"Output: {OUT}")
'@

    Set-Content -Path $GeneratorPy -Value $PythonCode -Encoding UTF8

    Push-Location $ProjectPath
    try {
        $Started = Get-Date
        & $Python $GeneratorPy $TenantDump $SharedDjangoPassword $ProjectPath |
            Tee-Object -FilePath (Join-Path $PackageDir "sanitization_report.txt")
        if ($LASTEXITCODE -ne 0) {
            Fail "Tenant sanitizer failed."
        }
        Write-Host ("    Tenant sanitization completed in {0}" -f (Format-Elapsed ((Get-Date) - $Started))) -ForegroundColor Green
    }
    finally {
        Pop-Location
    }

    Write-Step "[5/7] Building final sanitized SQL"

    $Started = Get-Date

    # ASCII/UTF-8 SQL generated by mysqldump + Python. Binary-safe enough for --hex-blob.
    $outStream = [System.IO.File]::Create($FinalDump)
    try {
        foreach ($part in @($SchemaDump, $DataDump, $TenantDump)) {
            $inStream = [System.IO.File]::OpenRead($part)
            try {
                $inStream.CopyTo($outStream)
                $newline = [System.Text.Encoding]::UTF8.GetBytes("`n")
                $outStream.Write($newline, 0, $newline.Length)
            }
            finally {
                $inStream.Dispose()
            }
        }
    }
    finally {
        $outStream.Dispose()
    }

    $FinalSize = (Get-Item $FinalDump).Length
    Write-Host ("    Final SQL: {0:N2} MB | completed in {1}" -f ($FinalSize / 1MB), (Format-Elapsed ((Get-Date) - $Started))) -ForegroundColor Green

    Write-Step "[6/7] Safety check + README"

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
    for x in found:
        print(" -", x)
    sys.exit(2)

print("Safety scan passed: no obvious API/private-key patterns found.")
'@

    $SafetyPy = Join-Path $WorkDir "safety_scan.py"
    Set-Content -Path $SafetyPy -Value $SafetyCode -Encoding UTF8

    & $Python $SafetyPy $FinalDump |
        Tee-Object -FilePath (Join-Path $PackageDir "safety_scan.txt")
    if ($LASTEXITCODE -ne 0) {
        Fail "Safety scan failed. ZIP was not created."
    }

    $GitCommit = "unknown"
    if (Test-Path (Join-Path $PackageDir "git_commit.txt")) {
        $GitCommit = (Get-Content (Join-Path $PackageDir "git_commit.txt") -Raw).Trim()
    }

    $Readme = @"
TMS SANITIZED PERFORMANCE PACKAGE - V16 REPRO-HEALTHCHECK-FIX
Created: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Source Git commit: $GitCommit

SANITIZATION SCOPE
ONLY the tenants_tenant table is anonymized.

PERFORMANCE EXPORT SCOPE
Stored procedures, scheduled MySQL events, and database triggers are intentionally
not exported. They are not needed for Django page-performance testing and can require
extra MySQL privileges. Django schema/tables/indexes and all application data are kept.

Sanitized tenant fields include:
- names
- CNIC
- phone numbers
- email
- addresses
- emergency contact details
- notes
- tenant photo/CNIC file paths

All other operational data is preserved exactly for performance testing.

Django passwords are reset to:
$SharedDjangoPassword

One custom superuser username is changed to:
admin

IMPORTANT PRIVACY NOTE
Because this ultra-fast mode intentionally sanitizes ONLY tenants_tenant, other tables
may still contain personal information (for example WhatsApp messages, registration
submissions, account profile data, maintenance free text, or other copied identifiers).
Use this mode only when you explicitly accept that tradeoff.

REPRODUCIBLE TEST DATABASE FILES
- docker-compose.yml: ready-to-run MySQL 8 test service
- .env.example: non-production Django DB settings
- README_TEST_SETUP.txt: startup, reset, and connection commands

NO production DB password, production .env, media files, or Python .venv is included.
The live database is never modified; all reads are SELECT/mysqldump only.
"@
    Set-Content -Path (Join-Path $PackageDir "README_SANITIZED.txt") -Value $Readme -Encoding UTF8

    # ------------------------------------------------------------------
    # Reproducible MySQL 8 test environment
    # ------------------------------------------------------------------
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
    command:
      - --default-authentication-plugin=mysql_native_password
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
    Set-Content -Path (Join-Path $PackageDir "docker-compose.yml") -Value $DockerCompose -Encoding UTF8

    $EnvExample = @"
# TEST-ONLY settings for the sanitized performance database.
# These are intentionally non-production credentials.

DB_ENGINE=django.db.backends.mysql
DB_NAME=tenant_management
DB_USER=tms_test
DB_PASSWORD=tms_test_password
DB_HOST=127.0.0.1
DB_PORT=6604

DEBUG=True
SECRET_KEY=tms-sanitized-performance-test-only
"@
    Set-Content -Path (Join-Path $PackageDir ".env.example") -Value $EnvExample -Encoding UTF8

    $TestSetup = @"
TMS SANITIZED DATABASE - REPRODUCIBLE MYSQL 8 TEST SETUP

This package contains a ready-to-run MySQL 8 Docker Compose service.

FILES
- tms_sanitized.sql      Sanitized performance-test database
- docker-compose.yml     MySQL 8 test database service
- .env.example           Test-only Django DB settings
- requirements.txt       Project requirements when present
- requirements_full.txt  Exact pip freeze from the source environment

START MYSQL
From the extracted package directory:

    docker compose up -d

The first startup creates the database and imports tms_sanitized.sql automatically.
Wait until the database is healthy:

    docker compose ps

TEST DATABASE CONNECTION
Using the MySQL client inside the container:

    docker exec tms_test_db mysql -utms_test -ptms_test_password -D tenant_management -e "SELECT COUNT(*) AS tenants FROM tenants_tenant;"

DJANGO TEST DATABASE SETTINGS
Use the values from .env.example:

    DB_NAME=tenant_management
    DB_USER=tms_test
    DB_PASSWORD=tms_test_password
    DB_HOST=127.0.0.1
    DB_PORT=6604

If the Django project uses different environment-variable names, map these values
to the project's existing database settings rather than modifying production settings.

RESET / REIMPORT THE DATABASE
MySQL initialization scripts run only when the Docker volume is empty.
To delete the test DB volume and import the SQL again:

    docker compose down -v
    docker compose up -d

STOP WITHOUT DELETING DATA

    docker compose down

TEST DJANGO LOGIN
Username: admin
Password: $SharedDjangoPassword

IMPORTANT
- All Docker/MySQL credentials in this package are TEST-ONLY.
- No production database password is included.
- The source production database is never modified by the exporter.
- Only tenants_tenant is anonymized by this fast export mode.
"@
    Set-Content -Path (Join-Path $PackageDir "README_TEST_SETUP.txt") -Value $TestSetup -Encoding UTF8

    Write-Step "[7/7] Creating ZIP (Fastest compression)"

    $Started = Get-Date
    if (Test-Path $ZipPath) {
        Remove-Item $ZipPath -Force
    }

    Compress-Archive `
        -Path (Join-Path $PackageDir "*") `
        -DestinationPath $ZipPath `
        -CompressionLevel Fastest

    if (-not (Test-Path $ZipPath)) {
        Fail "ZIP creation failed."
    }

    $ZipSize = (Get-Item $ZipPath).Length
    $TotalElapsed = Format-Elapsed ((Get-Date) - $ScriptStartedAt)

    Write-Host ""
    Write-Host "FAST SHARE EXPORT COMPLETE" -ForegroundColor Green
    Write-Host "ZIP  : $ZipPath" -ForegroundColor Green
    Write-Host ("Size : {0:N2} MB" -f ($ZipSize / 1MB))
    Write-Host "Test password: $SharedDjangoPassword"
    Write-Host "Test username: admin"
    Write-Host "Total export time: $TotalElapsed" -ForegroundColor Cyan
}
finally {
    $LivePassword = $null
    if (Test-Path $WorkDir) {
        Remove-Item $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
