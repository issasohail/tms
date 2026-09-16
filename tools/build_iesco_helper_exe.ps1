param([string]$TmsBaseUrl = 'https://kirayas.com/tms')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { throw 'Run this from a local TMS checkout with .venv.' }
$uri = [Uri]$TmsBaseUrl
if ($uri.Scheme -ne 'https' -or !$uri.Host -or $uri.Query -or $uri.Fragment -or $uri.UserInfo) { throw 'TmsBaseUrl must be a plain HTTPS TMS base URL.' }
$configPath = Join-Path $PSScriptRoot 'iesco_helper_build.json'
@{ base_url = $TmsBaseUrl.TrimEnd('/') } | ConvertTo-Json -Compress | Set-Content -LiteralPath $configPath -Encoding utf8
& $python -m PyInstaller --noconfirm --onefile --windowed --name 'TMS IESCO Fetch Helper Setup' --paths $root --add-data "$configPath;." --distpath (Join-Path $root 'tools\dist') --workpath (Join-Path $root 'tools\build') --specpath (Join-Path $root 'tools') (Join-Path $root 'tools\iesco_pairing_helper.py')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Write-Host "Built: $root\tools\dist\TMS IESCO Fetch Helper Setup.exe"
