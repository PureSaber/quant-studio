param([string]$Installer, [string]$StartupScript)
$ErrorActionPreference = 'Stop'
$TestId = [guid]::NewGuid().ToString('N')
$TestRoot = "Registry::HKEY_CURRENT_USER\Software\QuantStudioStartupTests\$TestId"
$RunSuffix = 'Software\Microsoft\Windows\CurrentVersion\Run'
# Only the newly generated test hive is created and removed. HKCU in the
# installer is a process-local drive mapped to that hive, not the real HKCU.
New-Item -Path "$TestRoot\$RunSuffix" -Force | Out-Null
try {
    Remove-PSDrive -Name HKCU
    New-PSDrive -Name HKCU -PSProvider Registry -Root $TestRoot.Substring(10) | Out-Null
    $Run = "HKCU:\$RunSuffix"
    Set-ItemProperty -LiteralPath $Run -Name Sentinel -Value 'unrelated startup'
    $Global:Visibility = 'visible'
    function global:Get-CimInstance {
        param([string]$ClassName, [string]$ErrorAction)
        if ($ClassName -ne 'Win32_StartupCommand') { throw 'Unexpected CIM query' }
        if ($Global:Visibility -eq 'error') { throw 'CIM observer unavailable' }
        if ($Global:Visibility -eq 'hidden') { return }
        $Key = Get-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        $Command = $Key.GetValue('QuantStudioWorkbench')
        if ($Command) {
            $Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            [pscustomobject]@{Name='QuantStudioWorkbench'; Command=$Command;
                Location="HKU\$Sid\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"}
        }
    }
    & $Installer -StartupScript $StartupScript
    & $Installer -StartupScript $StartupScript
    $Key = Get-Item -LiteralPath $Run
    if ($Key.GetValue('Sentinel') -ne 'unrelated startup') { throw 'Other entry erased' }
    if (-not $Key.GetValue('QuantStudioWorkbench').Contains($StartupScript)) { throw 'Wrong command' }
    foreach ($Mode in @('hidden', 'error')) {
        Remove-ItemProperty -LiteralPath $Run -Name QuantStudioWorkbench -ErrorAction SilentlyContinue
        $Global:Visibility = $Mode
        $Rejected = $false
        try { & $Installer -StartupScript $StartupScript } catch { $Rejected = $true }
        if (-not $Rejected) { throw "Unverified registration accepted: $Mode" }
        $Key = Get-Item -LiteralPath $Run
        if ($null -ne $Key.GetValue('QuantStudioWorkbench')) { throw 'Failed write was not reverted' }
        if ($Key.GetValue('Sentinel') -ne 'unrelated startup') { throw 'Other entry changed' }
    }
    Set-ItemProperty -LiteralPath $Run -Name QuantStudioWorkbench -Value 'other command'
    $Rejected = $false
    try { & $Installer -StartupScript $StartupScript } catch { $Rejected = $true }
    if (-not $Rejected) { throw 'Different existing registration overwritten' }
    if ((Get-Item -LiteralPath $Run).GetValue('QuantStudioWorkbench') -ne 'other command') { throw 'Conflict changed' }
    Write-Output 'STARTUP_REGISTRY_CHECKS_PASSED'
} finally {
    Remove-PSDrive -Name HKCU -ErrorAction SilentlyContinue
    if ($TestRoot -ne "Registry::HKEY_CURRENT_USER\Software\QuantStudioStartupTests\$TestId" -or $TestId -notmatch '^[a-f0-9]{32}$') { throw 'Unsafe test cleanup target' }
    Remove-Item -LiteralPath $TestRoot -Recurse -Force
}
