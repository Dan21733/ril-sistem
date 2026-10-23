# Image untuk master dan aplikasi gudang di server rumah (Linux + Docker).
# Dibangun dari folder pos-backend (lihat compose.yaml); POS kasir tidak dijalankan di sini.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Jakarta

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY master/requirements.txt ./master/requirements.txt
RUN pip install -r requirements.txt -r master/requirements.txt

COPY . .

# Jalan sebagai pengguna biasa, bukan root. UID 1000 = akun pertama di Ubuntu,
# jadi berkas di /srv/data yang ditulis kontainer tetap bisa dibuka akun itu.
RUN useradd --uid 1000 --create-home master \
    && mkdir -p /data/media /data/cache /data/cadangan /data/pindah \
    && chown -R master:master /data
USER master

EXPOSE 8001
CMD ["python", "-m", "waitress", "--listen=0.0.0.0:8001", "--threads=8", "master.wsgi_master:application"]
