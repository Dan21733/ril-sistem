"""Pengaturan untuk menjalankan modul Master.

Berkas ini MENUMPANG pengaturan POS (config/settings.py) lalu menambah modul
master di atasnya. Tidak ada berkas POS yang diubah: kalau POS di-update,
cukup tarik update-nya dan master tetap jalan.

Cara pakai:  python manage.py runserver 8001 --settings=master.settings
"""
import copy
import os
from pathlib import Path

from dotenv import load_dotenv

# master/.env (opsional) dibaca LEBIH DULU daripada pos-backend/.env, jadi nilainya
# menang. Gunanya: master bisa memakai database pengembangan sendiri walau
# pos-backend/.env menunjuk database produksi untuk kasir.
load_dotenv(Path(__file__).resolve().parent / ".env")

from config.settings import *  # noqa: F401,F403,E402
from config.settings import INSTALLED_APPS, TEMPLATES  # noqa: E402

INSTALLED_APPS = [*INSTALLED_APPS, "master"]

ROOT_URLCONF = "master.urls.root"

# Master punya login, jadi tidak boleh ikut DEBUG=True milik POS.
# Nyalakan hanya saat mengembangkan: set MASTER_DEBUG=1.
DEBUG = os.getenv("MASTER_DEBUG", "0") == "1"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("MASTER_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("MASTER_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

LOGIN_URL = "master:login"
LOGIN_REDIRECT_URL = "master:dashboard"
LOGOUT_REDIRECT_URL = "master:login"

# Nama cookie sendiri supaya tidak bertabrakan kalau POS kelak memakai sesi.
SESSION_COOKIE_NAME = "master_sessionid"
CSRF_COOKIE_NAME = "master_csrftoken"
SESSION_COOKIE_AGE = 60 * 60 * 12

LANGUAGE_CODE = "id"

# Aplikasi operasional (/gudang/) hanya terbuka di perangkat yang disetujui pemilik
# (master: Kasir > Perangkat operasional). Jangan dimatikan di server sungguhan.
MIDDLEWARE = [*MIDDLEWARE, "master.akun.gerbang.GerbangPerangkat"]  # noqa: F405
OPS_WAJIB_PERANGKAT = True

TEMPLATES = copy.deepcopy(TEMPLATES)
TEMPLATES[0]["OPTIONS"]["context_processors"].append("master.akun.akses.konteks")

# Server rumah (Docker, master/server/): foto faktur dan bukti transfer disimpan di
# HDD data, dan cache batas percobaan masuk di volume sendiri. Tanpa variabel ini
# (PC Windows), lokasinya tetap seperti pengaturan POS: pos-backend/media dan
# pos-backend/.pin_throttle_cache.
if os.getenv("MASTER_MEDIA_ROOT", "").strip():
    MEDIA_ROOT = Path(os.environ["MASTER_MEDIA_ROOT"].strip())
if os.getenv("MASTER_CACHE_DIR", "").strip():
    CACHES = copy.deepcopy(CACHES)  # noqa: F405
    CACHES["default"]["LOCATION"] = os.environ["MASTER_CACHE_DIR"].strip()

# Batas unggah Excel (10 MB).
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000
