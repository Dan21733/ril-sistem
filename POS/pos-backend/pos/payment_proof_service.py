"""Penyimpanan bukti transfer: validasi, idempotency, dan penulisan.

Dipisahkan dari view supaya aturan validasinya bisa diuji tanpa HTTP, mengikuti
pola `transaction_service.py` dan `device_registration.py`.

Konteks keputusan: nota `bank_transfer` yang tersinkron tanpa bukti tetap
diterima dan ditandai "bukti tertunda" (PAYMENT_PROOF_DESIGN.md §4), jadi
endpoint ini bersifat menyusul — bukan syarat sahnya nota. Urutan yang benar di
klien: nota dulu, bukti menyusul.
"""

from django.db import IntegrityError
from django.utils import timezone

from .models import PaymentProof, Transactions
from .payment_proof_storage import (
    MAGIC_HEADER_BYTES,
    MAX_PROOF_BYTES,
    build_object_key,
    delete_proof_file,
    detect_content_type,
    proof_file_exists,
    save_proof_file,
    sha256_hexdigest,
)
from .transaction_id import build_transaction_id


class PaymentProofError(Exception):
    """Base error bukti transfer."""


class PaymentProofNotFound(PaymentProofError):
    """Nota tidak ada, atau bukan milik device pemanggil."""


class InvalidPaymentProofFile(PaymentProofError):
    """Berkas kosong atau tipenya tidak diterima."""


class PaymentProofTooLarge(PaymentProofError):
    """Berkas melebihi batas ukuran."""


class PaymentProofConflict(PaymentProofError):
    """`proof_id` sudah dipakai untuk isi yang berbeda."""


def _validate_upload(uploaded_file):
    """Validasi isi berkas, bukan header kiriman klien."""
    header = uploaded_file.read(MAGIC_HEADER_BYTES)
    uploaded_file.seek(0)

    content_type = detect_content_type(header)

    if content_type is None:
        raise InvalidPaymentProofFile(
            "Hanya gambar JPEG, PNG, atau WEBP yang diterima."
        )

    byte_size = uploaded_file.size or 0

    if byte_size <= 0:
        raise InvalidPaymentProofFile("Berkas bukti transfer kosong.")

    if byte_size > MAX_PROOF_BYTES:
        raise PaymentProofTooLarge(
            "Ukuran bukti transfer maksimal "
            f"{MAX_PROOF_BYTES // (1024 * 1024)} MB."
        )

    return content_type, byte_size


def _assert_belongs_to_device(transaction, device_code):
    """Nota device lain dibalas 404 supaya keberadaannya tidak bocor."""
    transaction_id = transaction.transaction_id

    if not transaction_id or not device_code:
        # Nota tanpa nomor (offline kehabisan jatah nomor) belum bisa
        # diatribusikan ke device, karena `transactions` belum punya kolom
        # `device_id` (KEPUTUSAN-4 di OFFLINE_SYNC_DESIGN.md). Kelonggaran ini
        # sama persis dengan _validate_transaction_id_device saat nota ditulis,
        # jadi tidak membuka celah baru.
        return

    expected_prefix = build_transaction_id(device_code, 1)[:3]

    if transaction_id[:3] != expected_prefix:
        raise PaymentProofNotFound("Nota tidak ditemukan.")


def store_payment_proof(
    *,
    proof_id,
    sync_id,
    uploaded_file,
    device_code,
    captured_at=None,
):
    """Simpan satu bukti transfer untuk satu nota.

    Returns:
        tuple[PaymentProof, bool]: bukti dan apakah barisnya baru dibuat.
        `False` berarti pengiriman ulang isi yang sama — idempotent, bukan
        kegagalan, sehingga retry klien selalu aman.
    """
    transaction = Transactions.objects.filter(sync_id=sync_id).first()

    if transaction is None:
        raise PaymentProofNotFound("Nota tidak ditemukan.")

    _assert_belongs_to_device(transaction, device_code)

    content_type, byte_size = _validate_upload(uploaded_file)

    content = uploaded_file.read()
    digest = sha256_hexdigest(content)

    existing = PaymentProof.objects.filter(proof_id=proof_id).first()

    if existing is not None:
        if existing.sha256 == digest:
            return existing, False

        raise PaymentProofConflict(
            "Nomor bukti ini sudah ada dengan isi berbeda."
        )

    object_key = build_object_key(
        proof_id,
        content_type,
        timezone.now(),
    )

    if proof_file_exists(object_key):
        raise PaymentProofConflict(
            "Berkas untuk nomor bukti ini sudah ada."
        )

    saved_key = save_proof_file(object_key, content)

    try:
        proof = PaymentProof.objects.create(
            proof_id=proof_id,
            sync_id=sync_id,
            object_key=saved_key,
            content_type=content_type,
            byte_size=byte_size,
            sha256=digest,
            captured_at=captured_at,
            # Diisi eksplisit: kolomnya NOT NULL, dan mengandalkan default DB
            # saja adalah pola yang dulu membuat registrasi device gagal 500.
            uploaded_at=timezone.now(),
            device_code=device_code,
        )
    except IntegrityError:
        # Baris gagal ditulis: jangan tinggalkan berkas yatim di storage.
        delete_proof_file(saved_key)
        raise

    return proof, True
