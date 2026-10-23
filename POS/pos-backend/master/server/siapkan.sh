#!/usr/bin/env bash
# Persiapan sekali di server rumah: berkas .env berisi rahasia acak, dan folder data di HDD.
# Aman dijalankan ulang: .env yang sudah ada tidak ditimpa.
#
#   cd pos-backend/master/server && bash siapkan.sh
set -euo pipefail
cd "$(dirname "$0")"

DATA_DIR="${DATA_DIR:-/srv/data/master}"

if [ -f .env ]; then
  echo "[=] .env sudah ada, tidak diubah."
else
  acak() { openssl rand -hex 32; }
  umask 077
  cat > .env <<EOF
# Rahasia master di server rumah. Dibuat siapkan.sh $(date '+%Y-%m-%d %H:%M').
# Jangan di-commit, jangan ditempel ke chat. Menyimpan salinannya di pengelola sandi boleh.
POSTGRES_PASSWORD=$(acak)
SECRET_KEY=$(acak)
# Dua nilai ini wajib ada karena pengaturan POS memintanya; master tidak memakainya.
DEVICE_AUTH_SIGNING_KEY=$(acak)
DEVICE_REGISTRATION_TOKEN=$(acak)

DATA_DIR=${DATA_DIR}

# Alamat yang boleh membuka master dan gudang. Tambahkan nama tailnet server
# (mis. server-rumah.nama-tailnet.ts.net) saat membuka lewat tailscale serve.
MASTER_ALLOWED_HOSTS=localhost,127.0.0.1
MASTER_CSRF_TRUSTED_ORIGINS=
GUDANG_ALLOWED_HOSTS=localhost,127.0.0.1
GUDANG_CSRF_TRUSTED_ORIGINS=
GUDANG_HTTPS=0
EOF
  echo "[+] .env dibuat dengan rahasia acak."
fi

# Isian yang ditambahkan setelah .env pertama dibuat (kasir di server, cadangan Neon).
# Hanya ditambahkan kalau belum ada; nilai yang sudah diisi tidak pernah diubah.
tambah() {
  if ! grep -q "^$1=" .env; then
    printf '%s=%s
' "$1" "$2" >> .env
    echo "[+] .env: $1 ditambahkan."
  fi
}
umask 077
if ! grep -q "^CADANGAN_KUNCI=" .env; then
  printf '
# Kunci enkripsi cadangan Neon. SALIN KE TEMPAT AMAN DI LUAR SERVER (kertas/flashdisk).
# Tanpa kunci ini cadangan di Neon tidak bisa dibuka siapa pun.
' >> .env
fi
tambah CADANGAN_KUNCI "$(openssl rand -hex 32)"
tambah CADANGAN_NEON_URL ""
tambah CADANGAN_JAM "02:30"
if ! grep -q "^PRINT_BRIDGE_TARGET=" .env; then
  printf '
# Kasir: alamat penghubung cetak (aplikasi laptop toko, menu Pengaturan) dan identitas di nota.
' >> .env
fi
tambah PRINT_BRIDGE_TARGET "http://127.0.0.1:9101"
tambah VITE_STORE_NAME ""
tambah VITE_STORE_ADDRESS ""
tambah VITE_STORE_PHONE ""
tambah VITE_STORE_CODE ""

# Folder di HDD dibuat oleh akun ini (bukan root) supaya kontainer (UID 1000) bisa menulis.
mkdir -p "$DATA_DIR/media" "$DATA_DIR/cadangan/db" "$DATA_DIR/pindah"
if [ "$(id -u)" != "1000" ]; then
  echo "[!] UID akun ini $(id -u), bukan 1000. Kontainer menulis sebagai UID 1000:"
  echo "    sudo chown -R 1000:1000 $DATA_DIR"
fi
echo "[+] Folder data: $DATA_DIR/{media,cadangan,pindah}"
echo
echo "PENTING: salin nilai CADANGAN_KUNCI di .env ke tempat aman di luar server."
echo "Berikutnya: docker compose build"
