param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { throw 'Run this from a local TMS checkout with .venv.' }
& $python -m pip install --upgrade pyinstaller
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name TMSIESCOFetchHelper --paths $root --distpath (Join-Path $root 'tools\dist') --workpath (Join-Path $root 'tools\build') --specpath (Join-Path $root 'tools') (Join-Path $root 'tools\iesco_on_demand_helper.py')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Write-Host "Built: $root\tools\dist\TMSIESCOFetchHelper.exe"
