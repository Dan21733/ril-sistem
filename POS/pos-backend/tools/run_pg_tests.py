"""Menjalankan suite backend di database PostgreSQL disposable (item #2).

Kenapa ini ada
==============

``manage.py test`` memakai ``DATABASE_URL`` dari ``.env``, yang di mesin
client bisa menunjuk database bersama/produksi. Test runner Django membuat
database ``test_*`` di server itu: tidak menghapus data produksi, tetapi
menulis database uji di server produksi dan membebaninya. Skrip ini menutup
celah itu: pengujian PostgreSQL hanya jalan bila ``POS_TEST_DATABASE_URL``
menunjuk database disposable terpisah yang disetujui pemilik, dan
menolak bila URL uji sama dengan ``DATABASE_URL`` operasional.

Pemakaian, dari folder ``pos-backend`` (Git Bash)::

    POS_TEST_DATABASE_URL='postgresql://user:sandi@localhost:5432/pos_test' \\
    venv/Scripts/python.exe tools/run_pg_tests.py

    venv/Scripts/python.exe tools/run_pg_tests.py pos.tests.ItemListEndpointTests

Tanpa argumen, seluruh suite ``pos`` berjalan. Kredensial
(``SECRET_KEY``/``DEVICE_AUTH_SIGNING_KEY``/``DEVICE_REGISTRATION_TOKEN``)
diambil dari environment dulu, lalu ``.env`` seperti biasa; di CI isi dengan
nilai dummy. Skrip ini sengaja hanya memakai pustaka standar dan tidak
pernah mencetak nilai rahasia apa pun, hanya host/nama database uji.

Di dalam CI (``.github/workflows/pos-checks.yml``), URL disposable berasal
dari service ``postgres`` yang hidup-mati per job, jadi tidak ada database
produksi yang tersentuh.
"""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parent.parent

REQUIRED_SECRETS = (
    "SECRET_KEY",
    "DEVICE_AUTH_SIGNING_KEY",
    "DEVICE_REGISTRATION_TOKEN",
)


def _describe(url):
    """Tampilkan host/nama database saja; user/sandi tidak pernah dicetak."""
    try:
        parts = urlparse(url)
        return f"{parts.hostname or '?'}:{parts.port or '?'} / {parts.path.lstrip('/') or '?'}"
    except Exception:
        return "?"


def _normalized(url):
    parts = urlparse(url)
    return (
        (parts.scheme or "").lower(),
        (parts.hostname or "").lower(),
        parts.port,
        (parts.path or "").lstrip("/"),
    )


def _read_dotenv_database_url():
    """Ambil DATABASE_URL dari .env tanpa dependensi apa pun (tanpa nilainya)."""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL"):
            _, _, value = line.partition("=")
            return value.strip().strip("'").strip('"')
    return ""


def main(argv):
    test_url = (os.getenv("POS_TEST_DATABASE_URL") or "").strip()

    if not test_url:
        print(
            "POS_TEST_DATABASE_URL belum diisi. Minta database PostgreSQL "
            "disposable ke pemilik database (terpisah dari produksi), lalu "
            "set variabel itu. Lihat README bagian Verifikasi aman.",
            file=sys.stderr,
        )
        return 2

    if not test_url.split("://", 1)[0].lower().startswith("postgres"):
        print(
            "POS_TEST_DATABASE_URL harus PostgreSQL "
            f"(dapat: {test_url.split('://', 1)[0]}). "
            "Untuk smoke tanpa database bisnis pakai "
            "DATABASE_URL='sqlite://:memory:' langsung dengan manage.py test.",
            file=sys.stderr,
        )
        return 2

    operational_url = (os.getenv("DATABASE_URL") or "").strip() or _read_dotenv_database_url()

    if operational_url and _normalized(test_url) == _normalized(operational_url):
        print(
            "POS_TEST_DATABASE_URL sama dengan DATABASE_URL operasional: "
            "itu bukan database terpisah. Minta database uji disposable "
            "ke pemilik database.",
            file=sys.stderr,
        )
        return 2

    missing = [key for key in REQUIRED_SECRETS if not (os.getenv(key) or "").strip()]
    if missing and not (BACKEND_DIR / ".env").exists():
        print(
            "Kredensial belum tersedia: "
            + ", ".join(missing)
            + ". Isi lewat environment (CI: nilai dummy) atau jalankan "
            "tools/setup_env.py dulu.",
            file=sys.stderr,
        )
        return 2

    labels = argv[1:] or ["pos"]

    print(f"[pg-tests] database uji : {_describe(test_url)}")
    print(f"[pg-tests] label        : {' '.join(labels)}")
    print("[pg-tests] tabel eksternal dipakai sebagai TEMP TABLE sekali pakai;")
    print("[pg-tests] tabel operasional tidak disentuh (managed=False).")

    env = dict(os.environ)
    env["DATABASE_URL"] = test_url
    env["POS_TEST_RUN"] = "1"

    completed = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "manage.py"), "test", *labels],
        cwd=str(BACKEND_DIR),
        env=env,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
