param([Parameter(Mandatory)][string]$Config)
$ErrorActionPreference = 'Stop'
$State = Split-Path -Parent (Resolve-Path -LiteralPath $Config).Path
[IO.File]::WriteAllText((Join-Path $State 'stop-supervisor.request'), 'stop')
Write-Output 'Requested graceful shutdown. Wait for operations-status.json to report stopped before maintenance.'
