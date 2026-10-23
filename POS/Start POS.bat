@echo off
setlocal EnableExtensions

:: ==========================================================
::  Start POS 21733
::
::  Menjalankan backend Django (waitress) dan frontend hasil build
::  (vite preview) sekaligus — mode operasional toko, bukan dev.
::  Keduanya mendengarkan seluruh antarmuka jaringan supaya
::  HP/tablet di Wi-Fi yang sama bisa membuka POS ini.
::
::  Dependency dipasang lebih dulu, setiap kali berkas ini
::  dijalankan, di jendela masing-masing:
::    pos-backend  : venv\Scripts\python.exe -m pip install -r requirements.txt
::    pos-frontend : npm install
::  Server (waitress / build+preview) baru dinyalakan kalau
::  pemasangan itu berhasil, karena semuanya dirangkai dengan "&&".
::  Kalau dependency sudah lengkap, langkah pip/npm hanya beberapa detik
::  dan tidak mengunduh apa pun; yang agak lama hanya `npm run build`
::  (sekali per perubahan kode frontend).
::
::  Tidak ada path yang dipatok ke satu mesin: lokasi proyek
::  dihitung dari posisi berkas ini, jadi folder boleh dipindah
::  atau di-clone ke mana saja.
::
::  Prasyarat sekali saja di mesin baru:
::    pos-backend  : python -m venv venv
::    pos-backend\.env  berisi DATABASE_URL, SECRET_KEY,
::                      DEVICE_AUTH_SIGNING_KEY, DEVICE_REGISTRATION_TOKEN
::    pos-frontend\.env.local  berisi DEVICE_REGISTRATION_TOKEN yang sama
::    pos-frontend\.env.local  sebaiknya juga berisi VITE_STORE_NAME,
::                      VITE_STORE_ADDRESS, VITE_STORE_PHONE, dan
::                      VITE_STORE_CODE (identitas yang tercetak di nota)
::
::  Kalau .env belum ada, buat sekali di mesin itu dengan menjalankan
::  "venv\Scripts\python.exe tools\setup_env.py" dari folder pos-backend;
::  lihat pesan :env_belum_lengkap di bawah.
::
::  Berkas ini wajib berakhir baris CRLF. cmd salah membaca label dan blok
::  "if (" kalau berkasnya LF, gejalanya error aneh seperti
::  ". was unexpected at this time.".
:: ==========================================================

set "POSDIR=%~dp0"
set "BACKEND=%POSDIR%pos-backend"
set "FRONTEND=%POSDIR%pos-frontend"

echo ==========================================================
echo   POS 21733
echo ==========================================================
echo(

if not exist "%BACKEND%\manage.py" goto :proyek_tidak_lengkap
if not exist "%FRONTEND%\package.json" goto :proyek_tidak_lengkap
if not exist "%BACKEND%\venv\Scripts\python.exe" goto :venv_hilang

where npm >nul 2>nul
if errorlevel 1 goto :npm_tidak_ada

:: Konfigurasi rahasia diperiksa oleh tools\setup_env.py --check supaya yang
:: dilaporkan adalah variabel yang benar-benar kosong, bukan sekadar "berkas
:: .env tidak ada". Tanpa pemeriksaan ini Django tetap diluncurkan lalu mati
:: sendiri dengan: RuntimeError: SECRET_KEY environment variable is required.
"%BACKEND%\venv\Scripts\python.exe" "%BACKEND%\tools\setup_env.py" --check
if errorlevel 1 goto :env_belum_lengkap

call :deteksi_ip

echo  URL di komputer ini    : http://localhost:5173
echo  URL di HP/tablet (LAN) : http://%LANIP%:5173
echo(
echo  Hanya port 5173 yang perlu dijangkau perangkat lain; backend
echo  di 8000 tetap dipakai server ini lewat proxy yang sama.
echo(
echo  Kalau HP tidak bisa terhubung:
echo    - pastikan HP di Wi-Fi yang sama;
echo    - izinkan Node.js pada firewall Windows, di profil jaringan yang
echo      sedang dipakai. Cek dengan: Get-NetFirewallProfile
echo    - matikan AP/client isolation di router.
echo(
echo  Memasang dependency lalu menyalakan dua jendela baru:
echo    - POS Backend  : pip install -r requirements.txt, lalu waitress
echo    - POS Frontend : npm install, lalu build, lalu preview
echo  Tunggu baris "built in" di jendela Frontend; server hanya menyala
echo  setelah langkah itu selesai.
echo(

:: Dependency dipasang di jendela yang sama dan sebelum servernya: "&&"
:: memastikan waitress/build/preview tidak pernah menyala dengan dependency
:: gagal terpasang. Opsi pip/npm di bawah hanya mengurangi kebisingan dan
:: memakai cache lokal lebih dulu (--prefer-offline), bukan mengubah
:: hasil pemasangan. Frontend sengaja di-build dulu (`npm run build`)
:: supaya yang jalan di toko adalah bundle produksi, bukan dev server:
:: dev server tanpa optimasi, memakai autoreload, dan bukan untuk operasi.
:: Backend operasional toko: WSGI waitress (multi-thread, tanpa autoreload)
:: + POS_DEBUG=0. runserver hanya untuk development: single-thread,
:: DEBUG hardcoded, dan autoreload me-restart proses seenaknya.
start "POS Backend" /D "%BACKEND%" cmd /k "call .\venv\Scripts\activate.bat && python -m pip install --disable-pip-version-check -r requirements.txt && set POS_DEBUG=0 && waitress-serve --listen=0.0.0.0:8000 config.wsgi:application"
start "POS Frontend" /D "%FRONTEND%" cmd /k "npm install --no-audit --no-fund --prefer-offline && npm run build && npm run preview -- --host 0.0.0.0 --port 5173"

echo Selesai. Tunggu sampai jendela "POS Frontend" menulis baris
echo "Network: http://%LANIP%:5173/", lalu buka URL itu di HP.
echo Kalau port 5173 sudah dipakai, preview gagal start (tidak pindah
echo port sendiri seperti dev): bebaskan dulu port 5173-nya.
echo(
echo Tutup kedua jendela tersebut untuk mematikan POS.
echo(
pause
exit /b 0

:: ---------- subrutin ----------

:deteksi_ip
:: IPv4 antarmuka yang punya default gateway, yaitu Wi-Fi/Ethernet yang
:: benar-benar tersambung ke router (bukan adapter VirtualBox/VMware).
::
:: Hasil PowerShell ditulis ke berkas sementara lalu dibaca dengan "set /p".
:: Cara ini dipilih supaya tidak ada pipe maupun tanda kurung yang perlu
:: di-escape di dalam for /f - bentuk itu rapuh dan sudah terbukti gagal:
:: PowerShell menerima caret mentah sebagai argumen.
set "LANIP="
set "IPTEMP=%TEMP%\pos-21733-lanip.txt"
powershell -NoProfile -Command "foreach ($c in Get-NetIPConfiguration) { if ($c.IPv4DefaultGateway) { $c.IPv4Address.IPAddress } }" > "%IPTEMP%" 2>nul
if exist "%IPTEMP%" set /p LANIP=<"%IPTEMP%"
if exist "%IPTEMP%" del "%IPTEMP%" >nul 2>nul
if not defined LANIP set "LANIP=IP-TIDAK-TERDETEKSI"
exit /b 0

:env_belum_lengkap
echo [X] Berkas .env belum lengkap, jadi Django pasti gagal start.
echo(
echo     Kalau pemilik sudah mengirim berkas .env, letakkan di:
echo       %BACKEND%\.env
echo     lalu salin nilai DEVICE_REGISTRATION_TOKEN-nya ke:
echo       %FRONTEND%\.env.local
echo(
echo     Kalau belum ada, buat sendiri di mesin ini dengan:
echo(
echo       cd /d "%BACKEND%"
echo       venv\Scripts\python.exe tools\setup_env.py
echo(
echo     Skrip itu mengisi SECRET_KEY, DEVICE_AUTH_SIGNING_KEY, dan
echo     DEVICE_REGISTRATION_TOKEN secara otomatis, lalu menulis
echo     pos-frontend\.env.local dengan token yang sama. Dari pemilik
echo     database hanya DATABASE_URL yang dibutuhkan.
echo(
echo     Setelah itu periksa ulang dengan:
echo       venv\Scripts\python.exe tools\setup_env.py --check
echo(
pause
exit /b 1

:proyek_tidak_lengkap
echo [X] Folder POS tidak lengkap: pos-backend\manage.py atau
echo     pos-frontend\package.json tidak ditemukan.
echo(
echo     Letakkan berkas "Start POS.bat" ini di dalam folder POS,
echo     bersebelahan dengan folder pos-backend dan pos-frontend.
echo(
pause
exit /b 1

:venv_hilang
echo [X] Virtual environment backend belum ada. Jalankan sekali:
echo(
echo     cd /d "%BACKEND%"
echo     python -m venv venv
echo(
echo     Dependency Python-nya tidak perlu dipasang manual: "Start POS.bat"
echo     menjalankan pip install -r requirements.txt setiap kali dijalankan.
echo(
pause
exit /b 1

:npm_tidak_ada
echo [X] Perintah "npm" tidak ditemukan di PATH.
echo     Pasang Node.js dulu (butuh Node 22.18 atau lebih baru):
echo     https://nodejs.org
echo(
pause
exit /b 1
