param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Config,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$Entry = 'QuantStudioWorkbench'
$Registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if ($Remove) {
    Remove-ItemProperty -LiteralPath $Registry -Name $Entry -ErrorAction SilentlyContinue
    Write-Output 'Removed Quant Studio user-login startup; running service was not stopped.'
    exit
}
$ResolvedPython = (Resolve-Path -LiteralPath $Python).Path
$ResolvedConfig = (Resolve-Path -LiteralPath $Config).Path
$Windowless = Join-Path (Split-Path -Parent $ResolvedPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $Windowless)) { throw 'pythonw.exe is required for hidden user-login startup.' }
$Command = '"' + $Windowless + '" -X utf8 -m quant_studio.operations supervise --config "' + $ResolvedConfig + '"'
$Previous = $null
if (Test-Path -LiteralPath $Registry) {
    $Properties = Get-ItemProperty -LiteralPath $Registry
    $Property = $Properties.PSObject.Properties[$Entry]
    if ($Property) { $Previous = $Property.Value }
}
if ($Previous -and $Previous -ne $Command) { throw 'A different Quant Studio startup is already registered. Inspect and remove it explicitly before replacing.' }
New-Item -Path $Registry -Force | Out-Null
Set-ItemProperty -LiteralPath $Registry -Name $Entry -Value $Command
Write-Output 'Registered for current-user Windows login. This is not a pre-login system service.'
