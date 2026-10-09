param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$State,
    [Parameter(Mandatory)][string]$BindAddress,
    [int]$Port = 8782,
    [string]$Templates = ''
)
$ErrorActionPreference = 'Stop'
$State = (Resolve-Path -LiteralPath $State).Path
$Private = Join-Path $State 'private'
$StopFile = Join-Path $Private 'stop.request'
if (Test-Path -LiteralPath $StopFile) {
    throw 'A stop marker exists. Verify the previous server has stopped, then remove private/stop.request before starting.'
}
$Required = @('access.json', 'server.crt', 'server.key')
foreach ($Name in $Required) {
    if (-not (Test-Path -LiteralPath (Join-Path $Private $Name))) { throw "Missing $Name; provision access first" }
}
$Arguments = @('-m', 'quant_studio', 'serve', '--host', $BindAddress, '--port', "$Port",
    '--runs-root', (Join-Path $State 'runs'), '--settings', (Join-Path $State 'studio-settings.json'),
    '--access', (Join-Path $Private 'access.json'), '--tls-cert', (Join-Path $Private 'server.crt'),
    '--tls-key', (Join-Path $Private 'server.key'), '--stop-file', $StopFile)
if ($Templates) { $Arguments += @('--templates', $Templates) }
Write-Host "Workbench: https://${BindAddress}:$Port/research"
Write-Host "To stop gracefully: create $StopFile, or press Ctrl+C here."
& $Python @Arguments
exit $LASTEXITCODE
