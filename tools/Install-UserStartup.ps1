param(
    [Parameter(Mandatory=$true, ParameterSetName='Python')][string]$Python,
    [Parameter(Mandatory=$true, ParameterSetName='Python')][string]$Config,
    [Parameter(Mandatory=$true, ParameterSetName='Script')][string]$StartupScript,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
# Packaged desktop apps can see a private HKCU hive that Windows logon cannot.
# Refuse that context before either installing or removing the entry.
if (-not ('QuantStudio.StartupPackageContext' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace QuantStudio {
    public static class StartupPackageContext {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        public static extern int GetCurrentPackageFullName(ref uint length, IntPtr name);
    }
}
'@
}
[uint32]$PackageNameLength = 0
$PackageStatus = [QuantStudio.StartupPackageContext]::GetCurrentPackageFullName([ref]$PackageNameLength, [IntPtr]::Zero)
if ($PackageStatus -ne 15700) { # APPMODEL_ERROR_NO_PACKAGE
    throw 'Run this installer in a normal Windows PowerShell window opened from Start, outside a packaged app. Its private registry may not be visible to Windows logon. No startup entry was changed.'
}
$Entry = 'QuantStudioWorkbench'
$Registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($Remove) {
    Remove-ItemProperty -LiteralPath $Registry -Name $Entry -ErrorAction SilentlyContinue
    $Remaining = @(Get-CimInstance Win32_StartupCommand -ErrorAction Stop | Where-Object {
        $_.Name -eq $Entry -and $_.Location -like "HKU\$Sid\*"
    })
    if ($Remaining.Count -gt 0) { throw 'Windows still sees the startup entry. Removal is not verified; use a normal Windows PowerShell window.' }
    Write-Output 'Removed Quant Studio user-login startup; running service was not stopped.'
    exit
}
if ($PSCmdlet.ParameterSetName -eq 'Script') {
    $ResolvedScript = (Resolve-Path -LiteralPath $StartupScript).Path
    if ([IO.Path]::GetExtension($ResolvedScript) -ne '.ps1' -or -not (Test-Path -LiteralPath $ResolvedScript -PathType Leaf)) { throw 'StartupScript must be an existing PowerShell script.' }
    $Shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $Command = '"' + $Shell + '" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ResolvedScript + '"'
} else {
    $ResolvedPython = (Resolve-Path -LiteralPath $Python).Path
    $ResolvedConfig = (Resolve-Path -LiteralPath $Config).Path
    $Windowless = Join-Path (Split-Path -Parent $ResolvedPython) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $Windowless)) { throw 'pythonw.exe is required for hidden user-login startup.' }
    $Command = '"' + $Windowless + '" -X utf8 -m quant_studio.operations supervise --config "' + $ResolvedConfig + '"'
}
$Previous = $null
if (Test-Path -LiteralPath $Registry) {
    $Properties = Get-ItemProperty -LiteralPath $Registry
    $Property = $Properties.PSObject.Properties[$Entry]
    if ($Property) { $Previous = $Property.Value }
}
if ($Previous -and $Previous -ne $Command) { throw 'A different Quant Studio startup is already registered. Inspect and remove it explicitly before replacing.' }
if (-not (Test-Path -LiteralPath $Registry)) {
    New-Item -Path $Registry | Out-Null
}
try {
    Set-ItemProperty -LiteralPath $Registry -Name $Entry -Value $Command
    $NativeEntry = @(Get-CimInstance Win32_StartupCommand -ErrorAction Stop | Where-Object {
        $_.Name -eq $Entry -and $_.Location -like "HKU\$Sid\*" -and $_.Command -eq $Command
    })
    if ($NativeEntry.Count -ne 1) {
        throw 'Registry write completed, but Windows did not confirm the expected startup command. Automatic startup is not verified.'
    }
} catch {
    if ($null -eq $Previous) {
        Remove-ItemProperty -LiteralPath $Registry -Name $Entry -ErrorAction SilentlyContinue
    } else {
        Set-ItemProperty -LiteralPath $Registry -Name $Entry -Value $Previous
    }
    throw
}
Write-Output 'Registered for current-user Windows login. This is not a pre-login system service.'
