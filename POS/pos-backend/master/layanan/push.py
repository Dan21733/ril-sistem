"""Notifikasi web (Web Push) untuk aplikasi HP pemilik, tanpa pustaka tambahan.

- Enkripsi isi: RFC 8291 (aes128gcm), tanda tangan VAPID: RFC 8292 (JWT ES256).
- iPhone/iPad: hanya untuk web yang sudah "Tambahkan ke Layar Utama" (iOS 16.4+), lewat HTTPS.
- Dikirim oleh penjadwal master (`master_jadwal`) tiap putaran: permintaan ACC baru
  (nota ditahan, tugas dikirim, buka dus, PO menunggu ACC) diberitahukan sekali saja.

Fungsi kripto di atas tidak memakai Django supaya bisa diuji tersendiri.
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(teks):
    teks = teks.strip()
    return base64.urlsafe_b64decode(teks + "=" * (-len(teks) % 4))


def _hmac(kunci, isi):
    return hmac.new(kunci, isi, hashlib.sha256).digest()


def _publik_mentah(kunci_publik):
    return kunci_publik.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def buat_kunci():
    """Pasangan kunci VAPID baru: (privat PEM, publik base64url untuk applicationServerKey)."""
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem.decode(), b64u(_publik_mentah(k.public_key()))


def enkripsi(isi, p256dh, auth, *, garam=None, kunci_pengirim=None):
    """Isi pesan terenkripsi aes128gcm untuk satu langganan (satu rekaman, rs 4096)."""
    ua_publik = unb64u(p256dh)
    rahasia_auth = unb64u(auth)
    as_privat = kunci_pengirim or ec.generate_private_key(ec.SECP256R1())
    as_publik = _publik_mentah(as_privat.public_key())
    ua_kunci = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_publik)
    bersama = as_privat.exchange(ec.ECDH(), ua_kunci)
    prk_kunci = _hmac(rahasia_auth, bersama)
    ikm = _hmac(prk_kunci, b"WebPush: info\x00" + ua_publik + as_publik + b"\x01")
    garam = garam or os.urandom(16)
    prk = _hmac(garam, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    sandi = AESGCM(cek).encrypt(nonce, isi + b"\x02", None)
    return garam + struct.pack("!L", 4096) + bytes([len(as_publik)]) + as_publik + sandi


def jwt_vapid(endpoint, kunci_privat_pem, subjek, berlaku=12 * 3600):
    u = urlsplit(endpoint)
    kepala = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    klaim = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + berlaku, "sub": subjek},
                            separators=(",", ":")).encode())
    k = serialization.load_pem_private_key(kunci_privat_pem.encode(), password=None)
    r, s = decode_dss_signature(k.sign(f"{kepala}.{klaim}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{kepala}.{klaim}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def kirim_satu(langganan, data, *, kunci_privat_pem, kunci_publik, subjek, ttl=6 * 3600, waktu_habis=10):
    """Kirim ke satu langganan {endpoint, p256dh, auth}. Hasil: kode HTTP (0 = jaringan gagal)."""
    badan = enkripsi(json.dumps(data).encode(), langganan["p256dh"], langganan["auth"])
    req = urllib.request.Request(langganan["endpoint"], data=badan, method="POST", headers={
        "TTL": str(ttl), "Urgency": "high", "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "Authorization": f"vapid t={jwt_vapid(langganan['endpoint'], kunci_privat_pem, subjek)}, k={kunci_publik}"})
    try:
        with urllib.request.urlopen(req, timeout=waktu_habis) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


# ---------------------------------------------------------------------------
# Bagian Django: kunci, langganan, dan pengiriman berkala
# ---------------------------------------------------------------------------


def kunci():
    from master.models import PushKunci
    k = PushKunci.objects.order_by("id").first()
    if k is None:
        privat, publik = buat_kunci()
        k = PushKunci.objects.create(kunci_privat=privat, kunci_publik=publik)
    return k


def subjek():
    from django.conf import settings
    s = os.getenv("MASTER_PUSH_SUB", "").strip()
    if s:
        return s
    asal = [o for o in getattr(settings, "CSRF_TRUSTED_ORIGINS", []) if o.startswith("https://")]
    return asal[0] if asal else "mailto:pemilik@example.com"


def simpan_langganan(user, data, perangkat=""):
    from master.models import PushLangganan
    from master.layanan.inti import Tolak
    sub = data if isinstance(data, dict) else {}
    endpoint, keys = sub.get("endpoint") or "", sub.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise Tolak("Data langganan notifikasi tidak lengkap.")
    PushLangganan.objects.update_or_create(endpoint=endpoint[:700], defaults={
        "user": user, "p256dh": keys["p256dh"][:200], "auth": keys["auth"][:60], "perangkat": perangkat[:120], "gagal": 0})


def kirim_ke(langganan_qs, data):
    """Kirim ke beberapa langganan. Langganan yang sudah mati (404/410) dihapus. Hasil: jumlah berhasil."""
    from django.utils import timezone
    k = kunci()
    ok = 0
    for l in list(langganan_qs):
        kode = kirim_satu({"endpoint": l.endpoint, "p256dh": l.p256dh, "auth": l.auth}, data,
                          kunci_privat_pem=k.kunci_privat, kunci_publik=k.kunci_publik, subjek=subjek())
        if 200 <= kode < 300:
            ok += 1
            l.terakhir_ok, l.gagal = timezone.now(), 0
            l.save(update_fields=["terakhir_ok", "gagal"])
        elif kode in (404, 410) or l.gagal >= 20:
            l.delete()
        else:
            l.gagal += 1
            l.save(update_fields=["gagal"])
    return ok


# Hak yang dibutuhkan untuk tiap jenis permintaan ACC.
HAK_JENIS = {"nota": "kelola_kasir", "tugas": "kelola_lokasi", "dus": "ubah_stok", "po": "pembelian"}


def menunggu():
    """Semua permintaan ACC yang sedang menunggu: [(kunci, jenis, judul, isi, url)]."""
    from django.urls import reverse
    from pos.models import NotaTinjau
    from master.models import BukaDus, Pembelian, Tiket
    hasil = []
    for n in NotaTinjau.objects.filter(status="menunggu").order_by("masuk")[:50]:
        floor = "floor" in (n.alasan or "").lower()
        total = (n.payload or {}).get("total_amount") or 0
        hasil.append((f"nota:{n.sync_id}", "nota", "Harga di bawah floor" if floor else "Nota kasir ditahan",
                      f"Nota {n.transaction_id or ''} Rp{int(total):,}".replace(",", ".") + " menunggu ACC.", reverse("hp:beranda")))
    for t in Tiket.objects.filter(status="dikirim").select_related("untuk")[:50]:
        nama = t.untuk.first_name or t.untuk.username
        hasil.append((f"tugas:{t.id}:{t.dikirim_waktu:%Y%m%d%H%M%S}" if t.dikirim_waktu else f"tugas:{t.id}", "tugas",
                      "Tugas selesai dikerjakan", f"{nama}: {t.tujuan}", reverse("hp:tugas_detail", args=[t.id])))
    for b in BukaDus.objects.filter(status="menunggu").select_related("konversi__dari")[:50]:
        hasil.append((f"dus:{b.id}", "dus", "Buka dus menunggu ACC",
                      f"{b.oleh}: {b.jumlah} {b.konversi.dari.item_name}", reverse("hp:beranda")))
    for po in Pembelian.objects.filter(status="menunggu").select_related("supplier")[:50]:
        hasil.append((f"po:{po.id}:menunggu", "po", "Barang datang, menunggu ACC",
                      f"{po.nomor} · {po.supplier.nama}", reverse("master:pembelian_detail", args=[po.id])))
    return hasil


def kirim_baru():
    """Dipanggil penjadwal. Mengirim permintaan ACC yang belum pernah diberitahukan. Hasil: jumlah pesan."""
    from datetime import timedelta
    from django.utils import timezone
    from master.akun.akses import hak_pengguna
    from master.models import PushLangganan, PushTerkirim
    PushTerkirim.objects.filter(waktu__lt=timezone.now() - timedelta(days=45)).delete()
    semua = menunggu()
    sudah = set(PushTerkirim.objects.filter(kunci__in=[s[0] for s in semua]).values_list("kunci", flat=True))
    baru = [s for s in semua if s[0] not in sudah]
    if not baru:
        return 0
    PushTerkirim.objects.bulk_create([PushTerkirim(kunci=s[0]) for s in baru], ignore_conflicts=True)
    langganan = list(PushLangganan.objects.select_related("user").filter(user__is_active=True))
    terkirim = 0
    for user in {l.user for l in langganan}:
        hak = hak_pengguna(user)
        miliknya = [s for s in baru if HAK_JENIS[s[1]] in hak]
        if not miliknya:
            continue
        jumlah = sum(1 for s in semua if HAK_JENIS[s[1]] in hak)
        if len(miliknya) == 1:
            _, jenis, judul, isi, url = miliknya[0]
            data = {"title": judul, "body": isi, "url": url, "tag": miliknya[0][0], "badge": jumlah}
        else:
            data = {"title": f"{len(miliknya)} permintaan ACC baru", "body": "; ".join(s[2] for s in miliknya[:4]),
                    "url": "/hp/", "tag": "acc", "badge": jumlah}
        terkirim += kirim_ke([l for l in langganan if l.user_id == user.id], data)
    return terkirim


def tandai_semua():
    """Anggap semua yang menunggu sudah diberitahukan (saat langganan pertama, supaya tidak banjir)."""
    from master.models import PushTerkirim
    PushTerkirim.objects.bulk_create([PushTerkirim(kunci=s[0]) for s in menunggu()], ignore_conflicts=True)
