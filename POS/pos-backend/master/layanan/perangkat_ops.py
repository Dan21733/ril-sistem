"""Perangkat yang boleh membuka aplikasi operasional (/gudang/).

Setiap browser/aplikasi yang membuka /gudang/ diberi cookie acak (ditandatangani,
HttpOnly, berlaku lama). Perangkat baru tercatat "menunggu" dengan kode pendek
(mis. OPS-7K3P) yang tampil di layarnya. Pemilik menyetujui kode itu di master,
menu Kasir > Perangkat operasional. Sebelum disetujui, halaman masuk pun tidak
tampil, jadi PIN tidak bisa dicoba dari perangkat lain.

Yang disimpan di database hanya sidik (sha256) cookie-nya, bukan cookie-nya.
Kalau cookie perangkat terhapus (data browser dibersihkan), perangkat itu muncul
lagi sebagai perangkat baru dan perlu disetujui ulang.
"""
import datetime
import hashlib
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, rapikan_teks
from master.models import PerangkatOps

COOKIE = "ops_perangkat"
GARAM = "master.ops-perangkat"
UMUR_COOKIE = 10 * 365 * 24 * 3600
BATAS_MENUNGGU = 30          # perangkat menunggu sekaligus; lebih dari ini tidak dicatat lagi
HAPUS_MENUNGGU_HARI = 7      # permintaan yang tidak disetujui dihapus otomatis
SEGARKAN_TERAKHIR = datetime.timedelta(minutes=5)
_HURUF = "ACDEFGHJKMNPQRTUVWXY34679"


def wajib():
    return getattr(settings, "OPS_WAJIB_PERANGKAT", True)


def _sidik(kunci):
    return hashlib.sha256(kunci.encode()).hexdigest()


def _kode_baru():
    for _ in range(50):
        kode = "OPS-" + "".join(secrets.choice(_HURUF) for _ in range(4))
        if not PerangkatOps.objects.filter(kode=kode).exists():
            return kode
    raise RuntimeError("Gagal membuat kode perangkat.")


def kenali(request):
    """(perangkat atau None, cookie baru atau None). Perangkat baru dicatat sebagai menunggu."""
    kunci = request.get_signed_cookie(COOKIE, default=None, salt=GARAM)
    if kunci:
        p = PerangkatOps.objects.filter(kunci=_sidik(kunci)).first()
        if p is not None:
            sekarang = timezone.now()
            if p.disetujui and not p.dicabut and (p.terakhir is None or sekarang - p.terakhir > SEGARKAN_TERAKHIR):
                PerangkatOps.objects.filter(pk=p.pk).update(terakhir=sekarang)
            return p, None
    batas = timezone.now() - datetime.timedelta(days=HAPUS_MENUNGGU_HARI)
    PerangkatOps.objects.filter(disetujui__isnull=True, dicabut__isnull=True, dibuat__lt=batas).delete()
    if PerangkatOps.objects.filter(disetujui__isnull=True, dicabut__isnull=True).count() >= BATAS_MENUNGGU:
        return None, None
    kunci = secrets.token_urlsafe(32)
    agen = rapikan_teks(request.META.get("HTTP_USER_AGENT", ""))[:200]
    p = PerangkatOps.objects.create(kunci=_sidik(kunci), kode=_kode_baru(), keterangan=agen)
    return p, kunci


def pasang_cookie(response, kunci):
    response.set_signed_cookie(COOKIE, kunci, salt=GARAM, max_age=UMUR_COOKIE, httponly=True, samesite="Lax",
                               secure=getattr(settings, "SESSION_COOKIE_SECURE", False))


# ---------------------------------------------------------------------------
# Pemilik (master)
# ---------------------------------------------------------------------------


def _ambil(pid):
    p = PerangkatOps.objects.filter(pk=pid if str(pid or "").isdigit() else 0).first()
    if p is None:
        raise Tolak("Perangkat tidak ditemukan.")
    return p


def setujui(pengguna, pid, nama=""):
    p = _ambil(pid)
    nama = rapikan_teks(nama)[:80]
    if not nama and not p.nama:
        raise Tolak("Beri nama perangkatnya dulu, mis. 'Laptop admin' atau 'HP kepala gudang'.")
    with transaction.atomic():
        p.nama = nama or p.nama
        p.disetujui, p.disetujui_oleh, p.dicabut = timezone.now(), pengguna.username, None
        p.save()
        layanan.catat(pengguna, objek="perangkat", kolom="perangkat", baru=f"{p.kode} disetujui: {p.nama}")
    return p


def ganti_nama(pengguna, pid, nama):
    p = _ambil(pid)
    nama = rapikan_teks(nama)[:80]
    if not nama:
        raise Tolak("Nama perangkat wajib diisi.")
    lama, p.nama = p.nama, nama
    p.save(update_fields=["nama"])
    layanan.catat(pengguna, objek="perangkat", kolom="nama", lama=f"{p.kode} {lama}", baru=f"{p.kode} {nama}")


def cabut(pengguna, pid):
    """Perangkat langsung tidak bisa membuka aplikasi; sesi yang sedang masuk ikut keluar."""
    p = _ambil(pid)
    p.dicabut = timezone.now()
    p.save(update_fields=["dicabut"])
    layanan.catat(pengguna, objek="perangkat", kolom="perangkat", baru=f"{p.kode} dicabut: {p.nama}")


def hapus_menunggu(pengguna, pid):
    p = _ambil(pid)
    if p.disetujui and not p.dicabut:
        raise Tolak("Perangkat yang aktif dicabut dulu.")
    p.delete()


def daftar():
    semua = list(PerangkatOps.objects.all())
    return {
        "menunggu": [p for p in semua if p.status == "menunggu"],
        "disetujui": [p for p in semua if p.status == "disetujui"],
        "dicabut": [p for p in semua if p.status == "dicabut"],
    }
