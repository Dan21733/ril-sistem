@echo off
setlocal EnableExtensions

:: ==========================================================
::  Start Master
::
::  Menjalankan modul master di http://localhost:8001
::  Berkas ini tidak menyentuh POS kasir dan tidak menjalankan
::  migrasi database sendiri: perubahan database selalu
::  dijalankan dengan sadar, dari baris perintah.
::
::  Berkas ini wajib berakhir baris CRLF.
:: ==========================================================

set "POSDIR=%~dp0"
set "BACKEND=%POSDIR%pos-backend"

if not exist "%BACKEND%\master\settings.py" goto :tidak_lengkap
if not exist "%BACKEND%\venv\Scripts\python.exe" goto :venv_hilang
if not exist "%BACKEND%\.env" goto :env_hilang

cd /d "%BACKEND%"
set "PY=%BACKEND%\venv\Scripts\python.exe"

"%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt -r master\requirements.txt
if errorlevel 1 goto :pip_gagal

"%PY%" manage.py migrate --check --settings=master.settings >nul 2>nul
if errorlevel 1 goto :belum_migrasi

"%PY%" manage.py master_cadangan --jika-diatur --settings=master.settings

echo ==========================================================
echo   Master POS  -  http://localhost:8001
echo   Tutup jendela ini untuk mematikan master.
echo ==========================================================
"%PY%" -m waitress --listen=127.0.0.1:8001 --threads=8 master.wsgi_master:application
pause
exit /b 0

:tidak_lengkap
echo [X] Folder master tidak ditemukan di pos-backend.
pause
exit /b 1

:venv_hilang
echo [X] Virtual environment belum ada. Jalankan sekali:
echo       cd /d "%BACKEND%"
echo       python -m venv venv
pause
exit /b 1

:env_hilang
echo [X] Berkas pos-backend\.env belum ada.
echo     Untuk pengembangan, lihat contoh di pos-backend\master\README.md
echo     Jangan memakai .env produksi untuk mencoba-coba.
pause
exit /b 1

:pip_gagal
echo [X] Pemasangan dependency gagal. Periksa koneksi internet.
pause
exit /b 1

:belum_migrasi
echo [X] Tabel master belum dibuat di database yang ditunjuk .env.
echo     Pastikan .env menunjuk database yang benar, lalu jalankan:
echo       cd /d "%BACKEND%"
echo       venv\Scripts\python.exe manage.py migrate --settings=master.settings
echo       venv\Scripts\python.exe manage.py master_setup --settings=master.settings
pause
exit /b 1
