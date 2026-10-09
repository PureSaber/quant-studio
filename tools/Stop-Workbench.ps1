param([Parameter(Mandatory)][string]$State)
$ErrorActionPreference = 'Stop'
$Private = Join-Path (Resolve-Path -LiteralPath $State).Path 'private'
if (-not (Test-Path -LiteralPath (Join-Path $Private 'access.json'))) { throw 'Unknown workbench state directory' }
Set-Content -LiteralPath (Join-Path $Private 'stop.request') -Value 'Local operator requested graceful stop' -Encoding utf8
Write-Host 'Stop requested. Wait for the server process to exit before backup or restart.'
