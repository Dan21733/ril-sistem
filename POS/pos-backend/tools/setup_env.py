"""Penyiapan berkas rahasia POS: ``pos-backend/.env`` dan ``pos-frontend/.env.local``.

Kenapa ini ada
==============

``.env`` sengaja tidak masuk git, jadi mesin yang baru meng-clone repo tidak
punya satu pun rahasia dan Django langsung mati saat start:

    RuntimeError: SECRET_KEY environment variable is required.

Skrip ini menutup celah itu: ``SECRET_KEY``, ``DEVICE_AUTH_SIGNING_KEY``, dan
``DEVICE_REGISTRATION_TOKEN`` dibuat di mesin itu sendiri, jadi tidak perlu
dikirim lewat chat. Yang tetap harus datang dari pemilik database hanya
``DATABASE_URL``.

``DEVICE_REGISTRATION_TOKEN`` selalu disalin ke ``pos-frontend/.env.local``,
karena dev server Vite menyuntikkan token itu sebagai header
``X-Device-Registration-Token`` pada ``POST /api/devices/register/``. Kalau dua
berkas itu berbeda, kasir melihat 401 yang membingungkan ("Invalid device
registration token.").

Pemakaian, dari folder ``pos-backend``
=====================================

    venv\\Scripts\\python.exe tools\\setup_env.py
    venv\\Scripts\\python.exe tools\\setup_env.py --check
    venv\\Scripts\\python.exe tools\\setup_env.py --force
    venv\\Scripts\\python.exe tools\\setup_env.py --database-url "postgresql://..."

Tanpa ``--force`` nilai yang sudah ada dipertahankan dan hanya yang kosong yang
diisi, jadi skrip ini juga berfungsi sebagai perbaikan: setelah dijalankan,
token backend dan frontend dijamin sama.

``--check`` tidak mengubah apa pun; ``Start POS.bat`` memakainya untuk memastikan
konfigurasi lengkap sebelum dua jendela server dibuka.

Skrip ini sengaja hanya memakai pustaka standar (tanpa Django, tanpa
python-dotenv) supaya tetap bisa dijalankan memakai Python mana pun, termasuk
ketika venv justru sedang bermasalah.
"""

import argparse
import os
import platform
import secrets
import string
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "pos-frontend"

BACKEND_ENV_PATH = BACKEND_DIR / ".env"
FRONTEND_ENV_LOCAL_PATH = FRONTEND_DIR / ".env.local"
FRONTEND_ENV_PATH = FRONTEND_DIR / ".env"

# Keempat variabel ini wajib ada; settings.py menolak start kalau salah satu
# kosong.
REQUIRED_KEYS = (
    "DATABASE_URL",
    "SECRET_KEY",
    "DEVICE_AUTH_SIGNING_KEY",
    "DEVICE_REGISTRATION_TOKEN",
)

SECRET_KEYS = ("SECRET_KEY", "DEVICE_AUTH_SIGNING_KEY", "DEVICE_REGISTRATION_TOKEN")

# Huruf yang aman untuk berkas .env dan shell. Pola ini sama dengan yang dipakai
# Django untuk SECRET_KEY: tanpa kutip, tanpa karakter yang perlu di-escape.
SECRET_KEY_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*(-_=+)"

# Python yang dipakai membuat venv saat proyek ini diuji.
TESTED_PYTHON_SERIES = ("3.11", "3.12", "3.13")


def parse_env_file(path):
    """Baca ``KEY=VALUE`` sederhana dari berkas .env tanpa dependensi apa pun.

    Hanya dipakai untuk memeriksa; python-dotenv tetap yang membaca berkas ini
    saat Django start.
    """
    values = {}

    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        if line.startswith("export "):
            line = line[len("export "):].strip()

        name, _, value = line.partition("=")
        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        values[name.strip()] = value

    return values


def frontend_registration_token():
    """Token registrasi yang dibaca dev server Vite, beserta nama berkasnya.

    Vite memuat ``.env.local`` lebih dulu daripada ``.env``, dan nama tanpa
    prefix ``VITE_`` adalah bentuk yang didukung; nama lama tetap dikenali.
    """
    for path in (FRONTEND_ENV_LOCAL_PATH, FRONTEND_ENV_PATH):
        values = parse_env_file(path)

        for key in ("DEVICE_REGISTRATION_TOKEN", "VITE_DEVICE_REGISTRATION_TOKEN"):
            token = values.get(key, "").strip()

            if token:
                return token, path

    return "", None


# Identitas toko yang tercetak di kepala nota dan di surat jalan. Semuanya
# hidup di berkas env frontend (bukan di kode) karena berbeda per outlet.
FRONTEND_STORE_KEYS = (
    "VITE_STORE_NAME",
    "VITE_STORE_ADDRESS",
    "VITE_STORE_PHONE",
    "VITE_STORE_CODE",
)


def frontend_store_values():
    """Identitas toko dari berkas frontend; ``.env.local`` menang atas ``.env``."""
    values = {}

    for path in (FRONTEND_ENV_LOCAL_PATH, FRONTEND_ENV_PATH):
        found = parse_env_file(path)

        for key in FRONTEND_STORE_KEYS:
            if key not in values and found.get(key, "").strip():
                values[key] = found[key].strip()

    return values


def frontend_bridge_target():
    """Nilai ``PRINT_BRIDGE_TARGET`` dari berkas frontend, kalau diisi."""
    for path in (FRONTEND_ENV_LOCAL_PATH, FRONTEND_ENV_PATH):
        value = parse_env_file(path).get("PRINT_BRIDGE_TARGET", "").strip()

        if value:
            return value

    return ""


def bridge_target_port(value):
    """Nomor port dari ``PRINT_BRIDGE_TARGET``; "" kalau tidak ada/tidak terbaca."""
    text = (value or "").strip().split("://")[-1].split("/")[0]

    if ":" not in text:
        return ""

    return text.rsplit(":", 1)[1].strip()


def generate_secret_key():
    return "".join(secrets.choice(SECRET_KEY_ALPHABET) for _ in range(50))


def generate_token():
    return secrets.token_urlsafe(48)


def build_backend_env_text(values):
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    lines = [
        "# Rahasia POS 21733, dibuat oleh pos-backend/tools/setup_env.py.",
        f"# Terakhir diperbarui: {stamp}.",
        "#",
        "# Berkas ini ter-ignore git; jangan menempelkan isinya ke chat, commit,",
        "# atau issue. Jangan diubah dengan tangan kalau tidak perlu: jalankan",
        "# ulang tools/setup_env.py supaya token backend dan frontend tetap sama.",
        "",
    ]

    for key in REQUIRED_KEYS:
        lines.append(f"{key}={values[key]}")

    return "\n".join(lines) + "\n"


def build_frontend_env_text(existing_text, token):
    """Ganti/sisipkan ``DEVICE_REGISTRATION_TOKEN`` tanpa mengganggu baris lain."""
    lines = existing_text.splitlines() if existing_text else []
    result = []
    written = False

    for line in lines:
        stripped = line.strip()
        name = stripped.partition("=")[0].strip() if "=" in stripped else ""

        if name in ("DEVICE_REGISTRATION_TOKEN", "VITE_DEVICE_REGISTRATION_TOKEN"):
            if not written:
                result.append(f"DEVICE_REGISTRATION_TOKEN={token}")
                written = True

            # Baris duplikat dibuang: satu token saja yang boleh menang.
            continue

        result.append(line)

    if not written:
        if result and result[-1].strip():
            result.append("")

        result.extend(
            [
                "# Dibuat oleh pos-backend/tools/setup_env.py. Nilainya harus sama",
                "# dengan DEVICE_REGISTRATION_TOKEN di pos-backend/.env.",
                "# Dibaca dev server Vite, bukan browser, jadi jangan beri prefix VITE_.",
                f"DEVICE_REGISTRATION_TOKEN={token}",
                "",
                "# Alamat komputer yang menjalankan penghubung cetak (bridge.py, port",
                "# 9101), bukan alamat printer: printer memakai port 9100 dan bukan",
                "# HTTP. Kalau penghubung cetak jalan di komputer yang sama dengan dev",
                "# server, biarkan baris di bawah sebagai komentar - nilai bawaannya",
                "# sudah http://127.0.0.1:9101.",
                "# PRINT_BRIDGE_TARGET=http://127.0.0.1:9101",
                "",
                "# Identitas toko yang tercetak di kepala nota dan di surat jalan.",
                "# Isinya memang tampil di nota pelanggan, jadi prefix VITE_ di sini",
                "# benar (beda dengan token di atas yang tidak boleh sampai ke browser).",
                "# Kosong berarti barisnya tidak dicetak: nota tanpa alamat lebih baik",
                "# daripada nota bertuliskan alamat karangan. Setelah diubah, jalankan",
                "# ulang dev server (npm run dev) supaya nilainya terbaca.",
                "# VITE_STORE_NAME=Nama Toko",
                "# VITE_STORE_ADDRESS=Jl. Contoh No.19, Kota",
                "# VITE_STORE_PHONE=0812xxxxxxx",
                "#",
                "# Awalan nomor dokumen surat jalan: formulir toko memakai kode + cap",
                "# waktu. Kalau kosong, yang tercetak hanya cap waktunya.",
                "# VITE_STORE_CODE=164135",
            ]
        )

    return "\n".join(result).rstrip("\n") + "\n"


VALID_SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


def describe_database_url_problem(database_url):
    """Cari kesalahan tulis yang paling sering terjadi pada DATABASE_URL."""
    problems = []

    if database_url.startswith("#"):
        problems.append(
            "nilai diawali '#' sehingga python-dotenv membacanya sebagai komentar"
        )

    for character in ("#", "'", '"'):
        if character in database_url:
            problems.append(
                f"mengandung karakter {character!r} yang harus di-encode "
                "(%23, %27, %22) di dalam kata sandi"
            )

    if " " in database_url:
        problems.append(
            "mengandung spasi; spasi di kata sandi harus di-encode (%20) atau "
            "diapit tanda kutip"
        )

    if not database_url.startswith(("postgresql://", "postgres://")):
        problems.append(
            "tidak diawali 'postgresql://', jadi dj-database-url tidak mengenalinya"
        )

    # Insiden 2026-10-02 di mesin client: sslmode=require3 (salah ketik)
    # lolos --check lalu meledak sebagai traceback psycopg saat runserver.
    # Nilai yang sah: disable/allow/prefer/require/verify-ca/verify-full.
    query = database_url.partition("?")[2].partition("#")[0]
    for pair in query.split("&"):
        key, separator, value = pair.partition("=")
        if key.strip().lower() == "sslmode" and separator:
            mode = value.strip()
            if mode.lower() not in VALID_SSLMODES:
                problems.append(
                    f"sslmode={mode!r} tidak dikenal (yang sah: "
                    + "/".join(VALID_SSLMODES)
                    + "); periksa salah ketik, mis. 'require3' seharusnya 'require'"
                )

    return problems


def run_check():
    """Periksa konfigurasi tanpa mengubah berkas apa pun. 0 = siap start."""
    problems = []
    notes = []

    backend_values = parse_env_file(BACKEND_ENV_PATH)

    if not BACKEND_ENV_PATH.is_file():
        problems.append(f"{BACKEND_ENV_PATH} tidak ada")
    else:
        missing = [
            key
            for key in REQUIRED_KEYS
            if not backend_values.get(key, "").strip()
        ]

        if missing:
            problems.append(
                f"{BACKEND_ENV_PATH} ada, tapi variabel ini kosong: "
                + ", ".join(missing)
            )

    database_url = backend_values.get("DATABASE_URL", "").strip()

    if database_url:
        for problem in describe_database_url_problem(database_url):
            problems.append(f"DATABASE_URL {problem}")

    token, token_path = frontend_registration_token()
    backend_token = backend_values.get("DEVICE_REGISTRATION_TOKEN", "").strip()

    if not token:
        problems.append(
            f"{FRONTEND_ENV_LOCAL_PATH} belum memuat DEVICE_REGISTRATION_TOKEN"
        )
    elif backend_token and token != backend_token:
        problems.append(
            f"DEVICE_REGISTRATION_TOKEN di {token_path} berbeda dengan yang ada di "
            f"{BACKEND_ENV_PATH}; device kasir akan ditolak 401"
        )

    store = frontend_store_values()
    store_name = store.get("VITE_STORE_NAME", "")

    if not store_name:
        notes.append(
            f"VITE_STORE_NAME belum diisi di {FRONTEND_ENV_LOCAL_PATH}; kepala nota "
            'akan menulis "TOKO" tanpa alamat dan telepon. Isi identitas toko di '
            "berkas itu (bukan di kode), lalu jalankan ulang dev server"
        )

    bridge_target = frontend_bridge_target()

    if bridge_target:
        bridge_port = bridge_target_port(bridge_target)

        if bridge_port == "9100":
            notes.append(
                f"PRINT_BRIDGE_TARGET memakai port 9100 ({bridge_target}); itu port "
                "printer, bukan penghubung cetak, sehingga layar kasir hanya akan "
                "menampilkan 'penghubung cetak tidak menjawab'. Isi alamat komputer "
                "yang menjalankan bridge.py, umumnya port 9101"
            )
        elif bridge_port != "9101":
            notes.append(
                f"PRINT_BRIDGE_TARGET memakai port {bridge_port or '(bawaan 80)'} "
                f"({bridge_target}); penghubung cetak bawaan memakai port 9101"
            )

    python_series = ".".join(platform.python_version_tuple()[:2])

    if python_series not in TESTED_PYTHON_SERIES:
        notes.append(
            f"Python {platform.python_version()} belum diuji dengan requirements.txt "
            f"proyek ini (venv yang diuji memakai Python "
            f"{'/'.join(TESTED_PYTHON_SERIES)}); kalau ada error aneh saat "
            "menghubungi database, itu tersangka pertama"
        )

    print(f"Python          : {platform.python_version()}")
    print(f"Berkas backend  : {BACKEND_ENV_PATH}")
    print(f"Berkas frontend : {FRONTEND_ENV_LOCAL_PATH}")
    print(f"Penghubung cetak: {bridge_target or '(bawaan http://127.0.0.1:9101)'}")
    print(f"Nama toko       : {store_name or '(belum diisi, nota memakai TOKO)'}")
    print(f"Alamat toko     : {store.get('VITE_STORE_ADDRESS') or '(belum diisi)'}")
    print(f"Telepon toko    : {store.get('VITE_STORE_PHONE') or '(belum diisi)'}")
    print(
        "Kode dokumen    : "
        f"{store.get('VITE_STORE_CODE') or '(belum diisi, nomor dokumen hanya cap waktu)'}"
    )

    for note in notes:
        print(f"[!] {note}")

    if problems:
        print()

        for problem in problems:
            print(f"[X] {problem}")

        print()
        print("Perbaiki dengan menjalankan, dari folder pos-backend:")
        print("    venv\\Scripts\\python.exe tools\\setup_env.py")
        return 1

    print("[OK] Konfigurasi lengkap: backend dan frontend siap start.")
    return 0


def run_setup(database_url_argument, force):
    existing = parse_env_file(BACKEND_ENV_PATH)
    values = {}
    generated = []
    kept = []

    database_url = (database_url_argument or "").strip()

    if not database_url:
        database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        database_url = existing.get("DATABASE_URL", "").strip()

    if not database_url:
        print("DATABASE_URL belum ada. Ambil dari pemilik database (format")
        print("postgresql://user:sandi@host:port/nama_database).")
        print()

        try:
            database_url = input("DATABASE_URL: ").strip()
        except EOFError:
            database_url = ""

    if not database_url:
        print()
        print("[X] DATABASE_URL kosong, jadi .env belum bisa dibuat.")
        print("    Minta DATABASE_URL ke pemilik database, lalu jalankan ulang")
        print('    dengan: --database-url "postgresql://..."')
        return 2

    values["DATABASE_URL"] = database_url

    for key in SECRET_KEYS:
        if not force and existing.get(key, "").strip():
            values[key] = existing[key].strip()
            kept.append(key)
        else:
            values[key] = generate_secret_key() if key == "SECRET_KEY" else generate_token()
            generated.append(key)

    BACKEND_ENV_PATH.write_text(
        build_backend_env_text(values), encoding="utf-8", newline="\n"
    )

    print(f"[OK] {BACKEND_ENV_PATH} ditulis.")

    for key in generated:
        print(f"     dibuat baru : {key}")

    for key in kept:
        print(f"     dipertahankan: {key}")

    if FRONTEND_DIR.is_dir():
        existing_text = ""

        if FRONTEND_ENV_LOCAL_PATH.is_file():
            existing_text = FRONTEND_ENV_LOCAL_PATH.read_text(
                encoding="utf-8", errors="replace"
            )

        FRONTEND_ENV_LOCAL_PATH.write_text(
            build_frontend_env_text(existing_text, values["DEVICE_REGISTRATION_TOKEN"]),
            encoding="utf-8",
            newline="\n",
        )

        print(f"[OK] {FRONTEND_ENV_LOCAL_PATH} memakai token yang sama.")
    else:
        print(
            f"[!] {FRONTEND_DIR} tidak ditemukan, jadi token frontend tidak "
            "disalin. Salin DEVICE_REGISTRATION_TOKEN dari berkas .env di atas ke "
            "DEVICE_REGISTRATION_TOKEN di pos-frontend/.env.local."
        )

    for problem in describe_database_url_problem(database_url):
        print(f"[!] DATABASE_URL {problem}")

    print()
    print("Jalankan ulang periksaannya dengan:")
    print("    venv\\Scripts\\python.exe tools\\setup_env.py --check")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Periksa atau buat berkas rahasia POS (pos-backend/.env dan "
            "pos-frontend/.env.local)."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="hanya periksa konfigurasi, tanpa mengubah berkas",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="buat ulang SECRET_KEY, DEVICE_AUTH_SIGNING_KEY, dan token registrasi",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="isi DATABASE_URL tanpa ditanya interaktif",
    )

    arguments = parser.parse_args(argv)

    if arguments.check:
        return run_check()

    return run_setup(arguments.database_url, arguments.force)


if __name__ == "__main__":
    sys.exit(main())
