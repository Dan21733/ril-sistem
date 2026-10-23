"""Pesan galat API berbahasa Indonesia (permintaan pemilik 2026-10-17).

Sebagian besar penolakan domain sudah berbahasa Indonesia di sumbernya
(`refund_service`, `transaction_service`, `serializers`). Yang tersisa adalah
pesan bawaan DRF — mis. 401 tanpa header device atau validasi kolom wajib —
yang tetap berbahasa Inggris dan bisa tampil apa adanya di UI kasir.

Handler ini hanya menerjemahkan teks `detail`; bentuk respons, status, dan
kode error tidak berubah. Pesan yang tidak dikenal dibiarkan apa adanya supaya
tidak ada informasi yang hilang.
"""

import re

from rest_framework.views import exception_handler

# Pesan bawaan DRF yang realistis sampai ke kasir. Dicocokkan utuh, bukan
# substring, supaya pesan domain tidak ikut tertimpa.
DEFAULT_MESSAGES = {
    "Authentication credentials were not provided.":
        "Perangkat belum terautentikasi.",
    "Incorrect authentication credentials.":
        "Kredensial perangkat tidak cocok.",
    "Invalid token.": "Token tidak valid.",
    "You do not have permission to perform this action.":
        "Anda tidak punya izin untuk aksi ini.",
    "Not found.": "Data tidak ditemukan.",
    "This field is required.": "Kolom ini wajib diisi.",
    "This field may not be null.": "Kolom ini tidak boleh kosong.",
    "Not a valid UUID.": "Format UUID tidak valid.",
    "Not a valid integer.": "Harus berupa bilangan bulat.",
    "Not a valid string.": "Harus berupa teks.",
    "A valid integer is required.": "Harus berupa bilangan bulat.",
    "A valid number is required.": "Harus berupa angka.",
    "Ensure this value is greater than or equal to 0.":
        "Nilai tidak boleh kurang dari 0.",
    "Ensure this value is greater than or equal to 1.":
        "Nilai minimal 1.",
    "Ensure this field has at least 6 characters.":
        "Minimal 6 karakter.",
    "Ensure this field has no more than 6 characters.":
        "Maksimal 6 karakter.",
    "Ensure this field has at least 3 characters.":
        "Minimal 3 karakter.",
    "Must be a valid UUID.": "Format UUID tidak valid.",
    "Must be a valid integer.": "Harus berupa bilangan bulat.",
    "Must be a valid number.": "Harus berupa angka.",
    "Must be a valid string.": "Harus berupa teks.",
    "Must be a valid boolean.": "Harus berupa ya/tidak.",
    "Must be a valid choice.": "Pilihan tidak valid.",
}

METHOD_NOT_ALLOWED = re.compile(r'^Method "(.+)" not allowed\.$')

# Pesan bernomor (mis. "Ensure this value is less than or equal to 200.") tidak
# didaftarkan satu per satu; pola ini menangkap keluarganya.
# "x" / “x” is not a valid choice.
NOT_A_CHOICE = re.compile(r"^[\"“]?.+[\"”]? is not a valid choice\.$")

LIMIT_PATTERNS = (
    (re.compile(r"^Ensure this value is less than or equal to (\d+)\.$"),
     "Nilai maksimal {0}."),
    (re.compile(r"^Ensure this value is greater than or equal to (\d+)\.$"),
     "Nilai minimal {0}."),
    (re.compile(r"^Ensure this value has at most (\d+) characters?\.$"),
     "Maksimal {0} karakter."),
    (re.compile(r"^Ensure this value has at least (\d+) characters?\.$"),
     "Minimal {0} karakter."),
)


def _translate(text):
    if text in DEFAULT_MESSAGES:
        return DEFAULT_MESSAGES[text]

    match = METHOD_NOT_ALLOWED.match(text)
    if match:
        return f"Metode {match.group(1)} tidak diizinkan."

    if NOT_A_CHOICE.match(text):
        return "Pilihan tidak valid."

    for pattern, template in LIMIT_PATTERNS:
        match = pattern.match(text)
        if match:
            return template.format(match.group(1))

    return text


def _translate_detail(detail):
    if isinstance(detail, dict):
        return {key: _translate_detail(value) for key, value in detail.items()}

    if isinstance(detail, list):
        return [_translate_detail(item) for item in detail]

    if isinstance(detail, str):
        return _translate(detail)

    return detail


def indonesian_exception_handler(exc, context):
    """Terjemahkan `detail` galat DRF tanpa mengubah bentuk/kode respons."""
    response = exception_handler(exc, context)

    if response is None:
        return None

    response.data = _translate_detail(response.data)

    return response
