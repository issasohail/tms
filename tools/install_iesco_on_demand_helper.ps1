param(
  [Parameter(Mandatory=$true)][string]$HelperExePath,
  [Parameter(Mandatory=$true)][string]$IngestUrl,
  [Parameter(Mandatory=$true)][string]$ReferencesUrl,
  [Parameter(Mandatory=$true)][string]$ApiKey
)
$ErrorActionPreference = 'Stop'
if (!(Test-Path $HelperExePath)) { throw 'HelperExePath is not valid.' }
if (!$IngestUrl.StartsWith('https://') -or !$ReferencesUrl.StartsWith('https://')) { throw 'Both URLs must use HTTPS.' }
$configDir = Join-Path $env:APPDATA 'TMS'; New-Item -ItemType Directory -Force $configDir | Out-Null
$installedExe = Join-Path $configDir 'TMSIESCOFetchHelper.exe'; Copy-Item $HelperExePath $installedExe -Force
@{ ingest_url=$IngestUrl; references_url=$ReferencesUrl; api_key=$ApiKey } | ConvertTo-Json | Set-Content (Join-Path $configDir 'iesco_helper.json') -Encoding UTF8
$command = '"' + $installedExe + '" "%1"'
New-Item -Path 'HKCU:\Software\Classes\tms-iesco' -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\Software\Classes\tms-iesco' -Name '(Default)' -Value 'URL:TMS IESCO Fetch'
New-ItemProperty -Path 'HKCU:\Software\Classes\tms-iesco' -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
New-Item -Path 'HKCU:\Software\Classes\tms-iesco\shell\open\command' -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\Software\Classes\tms-iesco\shell\open\command' -Name '(Default)' -Value $command
Write-Host 'Installed. The helper now starts only when a TMS Fetch button is clicked, then exits.'
