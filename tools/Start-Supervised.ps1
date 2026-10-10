param([Parameter(Mandatory)][string]$Config)
$ErrorActionPreference = 'Stop'
$ResolvedConfig = (Resolve-Path -LiteralPath $Config).Path
$Service = Get-Content -LiteralPath $ResolvedConfig -Raw | ConvertFrom-Json
$State = Split-Path -Parent $ResolvedConfig
$StatusFile = Join-Path $State 'operations-status.json'
if (Test-Path -LiteralPath $StatusFile) {
    $Status = Get-Content -LiteralPath $StatusFile -Raw | ConvertFrom-Json
    $Existing = Get-CimInstance Win32_Process -Filter "ProcessId=$($Status.supervisor_pid)" -ErrorAction SilentlyContinue
    if ($Existing -and $Existing.CommandLine -like '*quant_studio.operations*' -and $Existing.CommandLine.Contains($ResolvedConfig)) {
        Write-Output 'The supervisor is already running. No second instance was started.'
        return
    }
}
if (Get-NetTCPConnection -LocalAddress $Service.host -LocalPort $Service.port -State Listen -ErrorAction SilentlyContinue) {
    throw 'The selected address/port is occupied. No process was stopped.'
}
$Stop = Join-Path $State 'stop-supervisor.request'
if (Test-Path -LiteralPath $Stop) { Remove-Item -LiteralPath $Stop }
$Logs = Join-Path $State 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$Arguments = '-X utf8 -m quant_studio.operations supervise --config "' + $ResolvedConfig + '"'
$Started = Start-Process -FilePath $Service.python -ArgumentList $Arguments -WorkingDirectory $Service.cwd -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $Logs "supervisor-$Stamp.out.log") `
    -RedirectStandardError (Join-Path $Logs "supervisor-$Stamp.err.log")
Write-Output "Started supervisor launcher PID $($Started.Id). Check /operations after startup."
