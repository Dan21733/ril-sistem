@echo off
rem Server aplikasi gudang untuk HP staf. Hanya mendengar di PC ini sendiri (127.0.0.1);
rem dibuka ke Tailscale lewat "tailscale serve". Jalankan dari mana saja.
cd /d "%~dp0.."
venv\Scripts\python.exe -m waitress --listen=127.0.0.1:8002 --threads=8 master.wsgi_gudang:application
