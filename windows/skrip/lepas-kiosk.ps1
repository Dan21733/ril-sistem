<#
Kembalikan akun karyawan ke desktop Windows biasa (kebalikan pasang-kiosk.ps1).
Jalankan dari akun PEMILIK, PowerShell "Run as administrator", saat akun karyawan sudah sign out:

    powershell -ExecutionPolicy Bypass -File .\lepas-kiosk.ps1 -Akun karyawan
#>
param([Parameter(Mandatory = $true)][string]$Akun)
$ErrorActionPreference = "Stop"
$user = Get-LocalUser -Name $Akun -ErrorAction SilentlyContinue
if (-not $user) { throw "Akun '$Akun' tidak ditemukan." }
$sid = $user.SID.Value
$profil = (Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $sid }).LocalPath
$sudahMuat = Test-Path "Registry::HKEY_USERS\$sid"
$akar = "Registry::HKEY_USERS\$sid"
if (-not $sudahMuat) {
    & reg.exe load "HKU\Ril_$Akun" (Join-Path $profil "NTUSER.DAT") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Registry akun '$Akun' tidak bisa dibuka. Pastikan akun itu sudah sign out." }
    $akar = "Registry::HKEY_USERS\Ril_$Akun"
}
try {
    Remove-ItemProperty -Path "$akar\Software\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name "Shell" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path "$akar\Software\Microsoft\Windows\CurrentVersion\Policies\System" -Name "DisableTaskMgr" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path "$akar\Software\Policies\Microsoft\Windows\RemovableStorageDevices" -Name "Deny_All" -ErrorAction SilentlyContinue
    Write-Host "Akun '$Akun' kembali ke desktop Windows biasa."
}
finally {
    if (-not $sudahMuat) { [gc]::Collect(); Start-Sleep -Milliseconds 500; & reg.exe unload "HKU\Ril_$Akun" | Out-Null }
}
