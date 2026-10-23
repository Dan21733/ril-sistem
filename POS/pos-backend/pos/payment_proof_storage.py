"""Satu pintu untuk semua akses berkas bukti transfer.

Kenapa satu pintu: keputusan storage produksi sengaja ditunda (lihat
`PAYMENT_PROOF_DESIGN.md` §3). Hanya modul ini yang tahu bentuk key dan cara
berkas disimpan/dibuka, sehingga pindah dari filesystem lokal ke object storage
cukup mengubah `STORAGES["default"]` di settings — model, serializer, dan view
tidak perlu disentuh.

Aturan yang mengikat modul ini:

- Yang disimpan di database dan yang dikembalikan pemanggil selalu KEY relatif,
  bukan URL, supaya berpindah backend tidak menuntut migrasi data.
- Key selalu dibuat di sini (server). Nama berkas kiriman klien tidak pernah
  menyentuh path storage.
- Tipe berkas ditentukan dari isinya (magic bytes), bukan dari header
  `Content-Type` kiriman klien.
"""

import hashlib
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


# Batas ini juga dipasang sebagai constraint di database
# (`payment_proof_byte_size_check`), jadi klien yang memaksa tetap ditolak di
# dua lapis: HTTP 413 di sini, lalu constraint bila ada jalur lain yang menulis.
MAX_PROOF_BYTES = 5 * 1024 * 1024

# SVG sengaja TIDAK ada di daftar: SVG dapat memuat skrip, dan berkas ini kelak
# ditampilkan kembali di browser.
MAGIC_SIGNATURES = (
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/png", b"\x89PNG\r\n\x1a\n"),
)

EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

# Cukup untuk seluruh magic bytes yang dikenal di atas (WEBP butuh 12 byte).
MAGIC_HEADER_BYTES = 32


def detect_content_type(header):
    """Tentukan tipe dari magic bytes; `None` bila bukan gambar yang diterima."""
    for content_type, signature in MAGIC_SIGNATURES:
        if header.startswith(signature):
            return content_type

    # WEBP: "RIFF" + 4 byte ukuran berkas + "WEBP".
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"

    return None


def sha256_hexdigest(content):
    return hashlib.sha256(content).hexdigest()


def build_object_key(proof_id, content_type, moment):
    """Susun key: `payment_proofs/<tahun>/<bulan>/<proof_id>.<ext>`.

    Bentuknya datar: folder bulan langsung berisi daftar gambar, tanpa subfolder
    per nota.

    Yang dipakai sebagai nama berkas adalah `proof_id`, bukan `sync_id`.
    Alasannya bukan selera: satu nota boleh punya lebih dari satu bukti — jalur
    pemulihan 409 membuat bukti baru dengan `proof_id` baru untuk `sync_id` yang
    sama (design bagian 7.12) — sehingga nama berbasis `sync_id` akan bentrok
    dengan berkas bukti pertama dan ditolak sebagai berkas yatim.

    Relasi berkas ke nota tetap utuh karena dipegang database: `sync_id`
    tersimpan di baris `payment_proof`, jadi pengelompokan per nota cukup
    di-query, tidak perlu di-encode ke dalam path.
    """
    extension = EXTENSIONS.get(content_type, "bin")

    return str(
        PurePosixPath("payment_proofs")
        / f"{moment:%Y}"
        / f"{moment:%m}"
        / f"{proof_id}.{extension}"
    )


def proof_file_exists(object_key):
    return default_storage.exists(object_key)


def save_proof_file(object_key, content):
    """Tulis berkas dan kembalikan key yang benar-benar dipakai.

    Nilai kembalian dipakai pemanggil (bukan `object_key` mentah) karena
    storage dapat mengubah nama bila key sudah terpakai.
    """
    return default_storage.save(object_key, ContentFile(content))


def delete_proof_file(object_key):
    default_storage.delete(object_key)


def open_proof_file(object_key):
    return default_storage.open(object_key, "rb")
