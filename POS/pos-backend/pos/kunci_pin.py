"""Kunci untuk PIN penyetuju yang menunggu di tablet (QA 18 Okt 2026).

Nota offline yang disetujui di bawah floor membawa PIN penyetuju sampai nota
terkirim. Supaya PIN itu tidak tersimpan sebagai teks biasa di tablet, tablet
menguncinya dengan kunci publik server (RSA-OAEP SHA-256, WebCrypto) dan hanya
server yang bisa membukanya.

- Pasangan kunci dibuat sekali, disimpan di `pos_setting` (database server).
- Kunci publik ikut dikirim di daftar kasir offline (wajib token tablet).
- Bentuk teks terkunci: "rsa1:<base64>".
"""
import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.db import IntegrityError, transaction

from .models import PosSetting

KUNCI = "kunci_pin_privat"
AWALAN = "rsa1:"
_cache = {}


def _privat():
    if "privat" in _cache:
        return _cache["privat"]
    baris = PosSetting.objects.filter(kunci=KUNCI).first()
    if baris is None:
        baru = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = baru.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()).decode()
        try:
            with transaction.atomic():
                PosSetting.objects.create(kunci=KUNCI, nilai=pem)
        except IntegrityError:
            pass  # proses lain membuatnya lebih dulu; pakai miliknya
        baris = PosSetting.objects.get(kunci=KUNCI)
    _cache["privat"] = serialization.load_pem_private_key(baris.nilai.encode(), password=None)
    return _cache["privat"]


def lupakan_cache():
    _cache.clear()


def kunci_publik():
    """Kunci publik (SPKI DER, base64) untuk `crypto.subtle.importKey('spki')`."""
    der = _privat().public_key().public_bytes(serialization.Encoding.DER,
                                              serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


def buka(teks):
    """PIN dari teks terkunci, atau ValueError."""
    if not isinstance(teks, str) or not teks.startswith(AWALAN):
        raise ValueError("Bentuk PIN terkunci tidak dikenal.")
    try:
        isi = _privat().decrypt(
            base64.b64decode(teks[len(AWALAN):], validate=True),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
    except Exception as exc:  # base64 rusak, kunci lain, isi diubah
        raise ValueError("PIN terkunci tidak bisa dibuka.") from exc
    return isi.decode()


def ambil_pin_penyetuju(data):
    """Ambil (dan buang) PIN penyetuju dari body nota: `approver_pin_sandi`
    (terkunci) atau `approver_pin` (teks biasa, tablet lama / online).
    Terkunci yang gagal dibuka menjadi PIN yang pasti salah, supaya alurnya
    sama dengan PIN salah (ditolak, atau ditahan kalau nota final)."""
    sandi = data.pop("approver_pin_sandi", None)
    biasa = data.get("approver_pin")
    if sandi:
        try:
            return buka(sandi)
        except ValueError:
            return "terkunci-rusak"
    return biasa
