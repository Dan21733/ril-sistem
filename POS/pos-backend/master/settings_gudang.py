"""Pengaturan server GUDANG: hanya aplikasi operasional (/gudang/), tanpa master.

Dipakai untuk diakses dari HP staf (lewat Tailscale). Proses ini sama sekali tidak
memuat URL master, jadi /master/ di server ini selalu 404 walaupun alamatnya ditebak.
Database, hak akun, dan data sama dengan master.

Jalankan (dari folder pos-backend):
    venv\\Scripts\\python.exe -m waitress --listen=127.0.0.1:8002 master.wsgi_gudang:application
Lalu buka ke tailnet dengan `tailscale serve` (lihat README master).
"""
import os

from master.settings import *

ROOT_URLCONF = "master.urls.root_gudang"

DEBUG = os.getenv("GUDANG_DEBUG", "0") == "1"


def _daftar(nama, bawaan=""):
    return [x.strip() for x in os.getenv(nama, bawaan).split(",") if x.strip()]


# Contoh: GUDANG_ALLOWED_HOSTS=localhost,127.0.0.1,nama-pc.nama-tailnet.ts.net
ALLOWED_HOSTS = _daftar("GUDANG_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Contoh: GUDANG_CSRF_TRUSTED_ORIGINS=https://nama-pc.nama-tailnet.ts.net:8443
CSRF_TRUSTED_ORIGINS = _daftar("GUDANG_CSRF_TRUSTED_ORIGINS")

LOGIN_URL = "gudang:masuk"
LOGIN_REDIRECT_URL = "gudang:beranda"
LOGOUT_REDIRECT_URL = "gudang:masuk"

# Cookie sendiri: masuk di server gudang tidak ikut membuka sesi master.
SESSION_COOKIE_NAME = "gudang_sessionid"
CSRF_COOKIE_NAME = "gudang_csrftoken"
SESSION_COOKIE_HTTPONLY = True
# Diakses lewat HTTPS (tailscale serve): cookie hanya dikirim lewat HTTPS.
if os.getenv("GUDANG_HTTPS", "0") == "1":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
