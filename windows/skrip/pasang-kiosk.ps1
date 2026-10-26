<#
Pasang Ril sebagai pengganti desktop untuk satu akun karyawan (Windows Home/Pro).
Jalankan dari akun PEMILIK (administrator), PowerShell "Run as administrator":

    powershell -ExecutionPolicy Bypass -File .\pasang-kiosk.ps1 -Akun karyawan

Syarat: akun karyawan sudah dibuat sebagai akun Standard, sudah pernah login sekali,
lalu sudah keluar (sign out). Ril.exe ada di folder yang sama dengan skrip ini.

Yang dilakukan:
  1. Menyalin Ril.exe ke C:\Program Files\Ril\
  2. Di akun karyawan saja:
     - shell = Ril.exe (login langsung masuk Ril, tanpa desktop/taskbar/Start)
     - Task Manager dimatikan
     - semua penyimpanan lepas-pasang (flashdisk, HDD eksternal, HP mode file) ditolak
#>
param(
    [Parameter(Mandatory = $true)][string]$Akun
)
$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Jalankan PowerShell sebagai administrator (klik kanan > Run as administrator)." }

$user = Get-LocalUser -Name $Akun -ErrorAction SilentlyContinue
if (-not $user) { throw "Akun '$Akun' tidak ditemukan." }
if ((Get-LocalGroupMember -SID "S-1-5-32-544" | Where-Object { $_.Name -like "*\$Akun" })) {
    throw "Akun '$Akun' masih administrator. Jadikan Standard dulu (Settings > Accounts > Other users)."
}
$sid = $user.SID.Value
$profil = (Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $sid }).LocalPath
if (-not $profil) { throw "Akun '$Akun' belum pernah login. Login sekali ke akun itu, lalu sign out, lalu jalankan skrip ini lagi." }

# 1. salin Ril.exe
$sumber = Join-Path $PSScriptRoot "Ril.exe"
if (-not (Test-Path $sumber)) { throw "Ril.exe tidak ada di folder skrip ($PSScriptRoot)." }
$tujuan = Join-Path $env:ProgramFiles "Ril"
New-Item -ItemType Directory -Force -Path $tujuan | Out-Null
Copy-Item $sumber (Join-Path $tujuan "Ril.exe") -Force
$exe = Join-Path $tujuan "Ril.exe"
Write-Host "Ril.exe disalin ke $exe"

# 2. registry akun karyawan
$sudahMuat = Test-Path "Registry::HKEY_USERS\$sid"
$akar = "Registry::HKEY_USERS\$sid"
if (-not $sudahMuat) {
    & reg.exe load "HKU\Ril_$Akun" (Join-Path $profil "NTUSER.DAT") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Registry akun '$Akun' tidak bisa dibuka. Pastikan akun itu sudah sign out (bukan cuma lock)." }
    $akar = "Registry::HKEY_USERS\Ril_$Akun"
}
try {
    $wl = "$akar\Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
    New-Item -Force -Path $wl | Out-Null
    Set-ItemProperty -Path $wl -Name "Shell" -Value "`"$exe`""

    $sys = "$akar\Software\Microsoft\Windows\CurrentVersion\Policies\System"
    New-Item -Force -Path $sys | Out-Null
    Set-ItemProperty -Path $sys -Name "DisableTaskMgr" -Value 1 -Type DWord

    $usb = "$akar\Software\Policies\Microsoft\Windows\RemovableStorageDevices"
    New-Item -Force -Path $usb | Out-Null
    Set-ItemProperty -Path $usb -Name "Deny_All" -Value 1 -Type DWord
    Write-Host "Akun '$Akun': shell = Ril, Task Manager mati, penyimpanan USB ditolak."
}
finally {
    if (-not $sudahMuat) {
        [gc]::Collect(); Start-Sleep -Milliseconds 500
        & reg.exe unload "HKU\Ril_$Akun" | Out-Null
    }
}
Write-Host ""
Write-Host "Selesai. Login ke akun '$Akun': Ril langsung terbuka dan minta PIN pemilik baru."
Write-Host "Untuk membatalkan: .\lepas-kiosk.ps1 -Akun $Akun"
