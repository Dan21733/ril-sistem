@echo off
setlocal EnableExtensions

:: ==========================================================
::  Buat "Penghubung Cetak.exe"
::
::  Dijalankan SEKALI di PC yang ada Python-nya (bukan di laptop toko).
::  Hasilnya satu berkas: dist\Penghubung Cetak.exe
::  Salin berkas itu ke laptop toko; laptop toko TIDAK perlu Python.
::
::  bridge.py diambil apa adanya dari ..\POS\print-bridge. Kalau bridge.py
::  diperbarui, jalankan berkas ini lagi lalu salin ulang exe-nya.
::
::  Berkas ini wajib berakhir baris CRLF.
:: ==========================================================

cd /d "%~dp0"

if not exist "..\POS\print-bridge\bridge.py" (
  echo [X] ..\POS\print-bridge\bridge.py tidak ditemukan.
  goto gagal
)

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [X] Python tidak ditemukan di PC ini.
  goto gagal
)

echo [1/4] Menyiapkan venv khusus build...
if not exist ".venv-build\Scripts\python.exe" %PY% -m venv .venv-build
if errorlevel 1 goto gagal

echo [2/4] Memasang PyInstaller...
".venv-build\Scripts\python.exe" -m pip install --disable-pip-version-check -q pyinstaller
if errorlevel 1 goto gagal

echo [3/4] Menjalankan uji...
".venv-build\Scripts\python.exe" uji_inti.py
if errorlevel 1 goto gagal

echo [4/4] Membuat exe...
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "Penghubung Cetak" --icon ikon.ico --add-data "ikon.ico;." ^
  --paths "..\POS\print-bridge" --hidden-import bridge penghubung_cetak.py
if errorlevel 1 goto gagal

echo(
echo Selesai: %CD%\dist\Penghubung Cetak.exe
echo Salin berkas itu ke laptop toko, lalu klik dua kali.
echo(
explorer dist
pause
exit /b 0

:gagal
echo(
echo [X] Gagal. Baca pesan di atas.
pause
exit /b 1
