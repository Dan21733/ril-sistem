"""Passkey (WebAuthn) untuk aplikasi HP: masuk dan buka kunci dengan Face ID / sidik jari.

Tanpa pustaka tambahan: CBOR dan kunci COSE yang dibutuhkan WebAuthn dibaca di sini.
- Daftar (navigator.credentials.create): attestation "none". Yang diperiksa: jenis, challenge,
  origin, hash rpId, bendera hadir (UP) dan terverifikasi (UV), lalu kunci publik disimpan.
- Masuk/buka kunci (navigator.credentials.get): tanda tangan atas authData + hash clientData
  diperiksa dengan kunci publik tersimpan. Algoritma: ES256 (-7) dan RS256 (-257).
Data wajah/sidik jari tidak pernah sampai ke server; yang disimpan hanya kunci publik perangkat.
"""
import base64
import hashlib
import json
import os
import struct
import time
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from master.layanan.inti import Tolak

UMUR_CHALLENGE = 5 * 60
BENDERA_UP, BENDERA_UV, BENDERA_AT = 0x01, 0x04, 0x40


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(teks):
    teks = (teks or "").strip()
    return base64.urlsafe_b64decode(teks + "=" * (-len(teks) % 4))


# ---------------------------------------------------------------------------
# CBOR (RFC 8949), cukup untuk attestationObject dan kunci COSE
# ---------------------------------------------------------------------------


def cbor_baca(data, i=0):
    """(nilai, posisi berikutnya). Hanya panjang pasti (yang dipakai WebAuthn)."""
    awal = data[i]
    jenis, info = awal >> 5, awal & 0x1F
    i += 1
    if info < 24:
        n = info
    elif info in (24, 25, 26, 27):
        lebar = {24: 1, 25: 2, 26: 4, 27: 8}[info]
        n = int.from_bytes(data[i:i + lebar], "big")
        i += lebar
    else:
        raise ValueError("CBOR panjang tak pasti tidak didukung")
    if jenis == 0:
        return n, i
    if jenis == 1:
        return -1 - n, i
    if jenis == 2:
        return bytes(data[i:i + n]), i + n
    if jenis == 3:
        return bytes(data[i:i + n]).decode("utf-8"), i + n
    if jenis == 4:
        isi = []
        for _ in range(n):
            v, i = cbor_baca(data, i)
            isi.append(v)
        return isi, i
    if jenis == 5:
        isi = {}
        for _ in range(n):
            k, i = cbor_baca(data, i)
            v, i = cbor_baca(data, i)
            isi[k] = v
        return isi, i
    if jenis == 7:
        return {20: False, 21: True, 22: None}.get(info), i
    raise ValueError("Jenis CBOR tidak didukung")


def kunci_cose(cose):
    """Kunci publik cryptography dari kunci COSE (bytes). Hasil: (kunci, alg)."""
    k, _ = cbor_baca(cose)
    kty, alg = k.get(1), k.get(3)
    if kty == 2 and alg == -7 and k.get(-1) == 1:
        titik = b"\x04" + k[-2] + k[-3]
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), titik), alg
    if kty == 3 and alg == -257:
        return rsa.RSAPublicNumbers(int.from_bytes(k[-2], "big"), int.from_bytes(k[-1], "big")).public_key(), alg
    raise Tolak("Jenis kunci perangkat ini belum didukung.")


def baca_auth_data(ad):
    if len(ad) < 37:
        raise Tolak("Data perangkat tidak lengkap.")
    hasil = {"rp_hash": ad[:32], "bendera": ad[32], "hitungan": struct.unpack(">I", ad[33:37])[0]}
    if hasil["bendera"] & BENDERA_AT:
        panjang = struct.unpack(">H", ad[53:55])[0]
        hasil["id"] = ad[55:55 + panjang]
        _, akhir = cbor_baca(ad, 55 + panjang)
        hasil["cose"] = bytes(ad[55 + panjang:akhir])
    return hasil


# ---------------------------------------------------------------------------
# Challenge di sesi
# ---------------------------------------------------------------------------


def buat_challenge(request, tujuan):
    c = os.urandom(32)
    request.session[f"passkey_{tujuan}"] = {"c": b64u(c), "t": int(time.time())}
    return b64u(c)


def _ambil_challenge(request, tujuan):
    simpan = request.session.pop(f"passkey_{tujuan}", None)    # sekali pakai
    if not simpan or time.time() - simpan.get("t", 0) > UMUR_CHALLENGE:
        raise Tolak("Permintaan kedaluwarsa. Coba lagi.")
    return simpan["c"]


def rp_id(request):
    return request.get_host().split(":")[0]


def _origin_boleh(request, origin):
    from django.conf import settings
    u = urlsplit(origin or "")
    if u.hostname != rp_id(request):
        return False
    sendiri = f"{request.scheme}://{request.get_host()}"
    return origin == sendiri or origin in getattr(settings, "CSRF_TRUSTED_ORIGINS", [])


def _periksa_client(request, client_json, jenis, tujuan):
    try:
        cd = json.loads(client_json.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Tolak("Data perangkat tidak terbaca.") from None
    if cd.get("type") != jenis:
        raise Tolak("Jenis permintaan tidak cocok.")
    if cd.get("challenge") != _ambil_challenge(request, tujuan):
        raise Tolak("Permintaan kedaluwarsa. Coba lagi.")
    if not _origin_boleh(request, cd.get("origin")):
        raise Tolak("Alamat aplikasi tidak cocok.")


def _periksa_auth(request, ad):
    if ad["rp_hash"] != hashlib.sha256(rp_id(request).encode()).digest():
        raise Tolak("Passkey ini bukan untuk alamat ini.")
    if not ad["bendera"] & BENDERA_UP or not ad["bendera"] & BENDERA_UV:
        raise Tolak("Face ID / sidik jari belum diverifikasi.")


# ---------------------------------------------------------------------------
# Daftar dan verifikasi
# ---------------------------------------------------------------------------


def opsi_daftar(request, user):
    from master.models import PasskeyHp
    return {
        "challenge": buat_challenge(request, "daftar"),
        "rp": {"id": rp_id(request), "name": "DB Toko"},
        "user": {"id": b64u(f"u{user.pk}".encode()), "name": user.username,
                 "displayName": user.first_name or user.username},
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}, {"type": "public-key", "alg": -257}],
        "authenticatorSelection": {"authenticatorAttachment": "platform", "residentKey": "required",
                                   "requireResidentKey": True, "userVerification": "required"},
        "excludeCredentials": [{"type": "public-key", "id": p.credential_id}
                               for p in PasskeyHp.objects.filter(user=user)],
        "attestation": "none", "timeout": 120000,
    }


def daftar(request, user, data, nama=""):
    from master.models import PasskeyHp
    try:
        resp = data["response"]
        client = unb64u(resp["clientDataJSON"])
        att, _ = cbor_baca(unb64u(resp["attestationObject"]))
    except (KeyError, TypeError, ValueError, IndexError):
        raise Tolak("Data perangkat tidak terbaca.") from None
    _periksa_client(request, client, "webauthn.create", "daftar")
    if not isinstance(att, dict) or "authData" not in att:
        raise Tolak("Data perangkat tidak lengkap.")
    ad = baca_auth_data(att["authData"])
    _periksa_auth(request, ad)
    if "cose" not in ad:
        raise Tolak("Perangkat tidak mengirim kunci.")
    kunci_cose(ad["cose"])          # pastikan algoritmanya didukung
    cid = b64u(ad["id"])
    if PasskeyHp.objects.filter(credential_id=cid).exists():
        raise Tolak("Perangkat ini sudah terdaftar.")
    return PasskeyHp.objects.create(user=user, credential_id=cid, kunci_publik=b64u(ad["cose"]),
                                    hitungan=ad["hitungan"], nama=(nama or "Perangkat")[:100])


def opsi_masuk(request, user=None):
    from master.models import PasskeyHp
    opsi = {"challenge": buat_challenge(request, "masuk"), "rpId": rp_id(request),
            "userVerification": "required", "timeout": 120000}
    if user is not None:
        opsi["allowCredentials"] = [{"type": "public-key", "id": p.credential_id}
                                    for p in PasskeyHp.objects.filter(user=user)]
    return opsi


def verifikasi(request, data, user=None):
    """Periksa jawaban navigator.credentials.get. Hasil: PasskeyHp yang dipakai."""
    from django.utils import timezone
    from master.models import PasskeyHp
    try:
        resp = data["response"]
        cid = b64u(unb64u(data["rawId"] or data["id"]))
        client = unb64u(resp["clientDataJSON"])
        ad_mentah = unb64u(resp["authenticatorData"])
        tanda = unb64u(resp["signature"])
    except (KeyError, TypeError, ValueError):
        raise Tolak("Data perangkat tidak terbaca.") from None
    p = PasskeyHp.objects.select_related("user").filter(credential_id=cid).first()
    if p is None or not p.user.is_active or (user is not None and p.user_id != user.pk):
        raise Tolak("Passkey ini tidak terdaftar. Masuk dengan kata sandi, lalu daftarkan lagi di Setelan.")
    _periksa_client(request, client, "webauthn.get", "masuk")
    ad = baca_auth_data(ad_mentah)
    _periksa_auth(request, ad)
    kunci, alg = kunci_cose(unb64u(p.kunci_publik))
    isi = ad_mentah + hashlib.sha256(client).digest()
    try:
        if alg == -7:
            kunci.verify(tanda, isi, ec.ECDSA(hashes.SHA256()))
        else:
            kunci.verify(tanda, isi, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise Tolak("Verifikasi gagal.") from None
    if p.hitungan and ad["hitungan"] and ad["hitungan"] <= p.hitungan:
        raise Tolak("Passkey ini terdeteksi disalin. Hapus di Setelan lalu daftarkan ulang.")
    p.hitungan, p.terakhir = ad["hitungan"], timezone.now()
    p.save(update_fields=["hitungan", "terakhir"])
    return p
