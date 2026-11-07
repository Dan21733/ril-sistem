"""Pengeluaran (biaya) toko — tahap 1 (rancangan `claude/rancangan-pembukuan-toko.md`, 7 Nov 2026).

- Uang keluar dari laci (`kas_gerak` jenis keluar) ikut tercatat sebagai biaya
  sumber "laci". Yang dicatat dari tablet menunggu ACC; yang dicatat pemilik
  lewat master/HP langsung disetujui. Tabel POS tidak diubah: tautannya
  `Biaya.gerak_id`.
- Uang laci yang ditolak tetap keluar dari kas (uangnya memang sudah keluar),
  tidak dihitung sebagai biaya toko, dan menjadi tanggungan pencatatnya sampai
  ditandai "sudah diganti".
- Kategori diisi sendiri oleh pemilik; tanda `biaya_usaha` memisahkan biaya
  dari yang bukan biaya (diambil pemilik, bayar supplier).
"""
import datetime
import os
import uuid

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, rapikan_teks
from master.models import Biaya, KategoriBiaya

# Pencatat yang dianggap pemilik: kas keluar yang dicatat dari master atau HP.
DARI_PEMILIK = ("master", "hp")
JENIS_FOTO = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}
MAKS_FOTO = 15 * 1024 * 1024


def rp(n):
    return "Rp " + f"{int(n or 0):,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Kategori
# ---------------------------------------------------------------------------

def kategori_semua():
    return list(KategoriBiaya.objects.all())


def kategori_aktif():
    return list(KategoriBiaya.objects.filter(aktif=True))


def kategori_usaha():
    return set(KategoriBiaya.objects.filter(biaya_usaha=True).values_list("nama", flat=True))


def kategori_simpan(pengguna, data):
    """Tambah (tanpa id) atau ubah satu kategori: nama, biaya_usaha, aktif."""
    nama = rapikan_teks(data.get("nama"))[:40]
    if not nama:
        raise Tolak("Isi nama kategori.")
    usaha = data.get("biaya_usaha") in ("1", "on", True)
    aktif = data.get("aktif", "1") in ("1", "on", True)
    kid = data.get("id")
    dobel = KategoriBiaya.objects.filter(nama__iexact=nama)
    if kid:
        dobel = dobel.exclude(pk=kid)
    if dobel.exists():
        raise Tolak(f"Kategori '{nama}' sudah ada.")
    with transaction.atomic():
        if kid:
            k = KategoriBiaya.objects.select_for_update().filter(pk=kid).first()
            if k is None:
                raise Tolak("Kategori tidak ditemukan.")
            lama = k.nama
            k.nama, k.biaya_usaha, k.aktif = nama, usaha, aktif
            k.save()
            if lama != nama:
                # Catatan biaya ikut nama baru. Catatan uang keluar di kas (tabel POS) tetap nama lama.
                Biaya.objects.filter(kategori=lama).update(kategori=nama)
            layanan.catat(pengguna, aksi="ubah", objek="biaya", kolom="kategori", lama=lama,
                          baru=f"{nama} ({'biaya usaha' if usaha else 'bukan biaya'}{'' if aktif else ', nonaktif'})")
        else:
            urutan = (KategoriBiaya.objects.order_by("-urutan").values_list("urutan", flat=True).first() or 0) + 10
            k = KategoriBiaya.objects.create(nama=nama, biaya_usaha=usaha, aktif=True, urutan=urutan)
            layanan.catat(pengguna, aksi="tambah", objek="biaya", kolom="kategori",
                          baru=f"{nama} ({'biaya usaha' if usaha else 'bukan biaya'})")
    return k


# ---------------------------------------------------------------------------
# Tarik uang keluar laci
# ---------------------------------------------------------------------------

def tarik_dari_kas():
    """Samakan biaya dengan uang keluar laci: catatan baru masuk, yang dibatalkan ikut batal."""
    from pos.models import KasGerak
    from master.models import Toko
    ada = Biaya.objects.exclude(gerak_id=None).values("gerak_id")
    toko_ada = set(Toko.objects.values_list("pk", flat=True))
    baru = []
    for g in KasGerak.objects.filter(jenis="keluar", batal=False).exclude(gerak_id__in=ada).order_by("waktu")[:500]:
        pemilik = (g.device_code or "") in DARI_PEMILIK
        baru.append(Biaya(
            toko_id=g.toko_id if g.toko_id in toko_ada else None, tanggal=timezone.localdate(g.waktu),
            kategori=g.kategori or "Lainnya", jumlah=g.jumlah, sumber="laci", keterangan=g.keterangan,
            gerak_id=g.gerak_id, dicatat_oleh=g.oleh, worker_id=g.worker_id, dicatat_waktu=g.waktu,
            status="disetujui" if pemilik else "menunggu",
            acc_oleh=g.oleh if pemilik else "", acc_waktu=g.waktu if pemilik else None))
    if baru:
        Biaya.objects.bulk_create(baru, ignore_conflicts=True)
    batal = {g.gerak_id: g for g in KasGerak.objects.filter(
        jenis="keluar", batal=True, gerak_id__in=Biaya.objects.exclude(status="batal").exclude(gerak_id=None)
        .values("gerak_id"))}
    for b in Biaya.objects.filter(gerak_id__in=list(batal)).exclude(status="batal"):
        g = batal[b.gerak_id]
        b.status, b.alasan = "batal", (f"Uang keluar dibatalkan di kas oleh {g.batal_oleh}: {g.batal_alasan}")[:300]
        b.save(update_fields=["status", "alasan"])
    return len(baru)


def jumlah_menunggu():
    """Untuk angka di menu: yang menunggu + uang keluar kasir yang belum ditarik (tanpa menulis apa pun)."""
    from pos.models import KasGerak
    belum = (KasGerak.objects.filter(jenis="keluar", batal=False).exclude(device_code__in=DARI_PEMILIK)
             .exclude(gerak_id__in=Biaya.objects.exclude(gerak_id=None).values("gerak_id")).count())
    return Biaya.objects.filter(status="menunggu").count() + belum


# ---------------------------------------------------------------------------
# Catat, ACC, ganti, batal
# ---------------------------------------------------------------------------

def _cek_foto(foto):
    if foto is None:
        return
    if foto.content_type not in JENIS_FOTO:
        raise Tolak("Foto nota hanya JPG/PNG/WEBP atau PDF.")
    if foto.size > MAKS_FOTO:
        raise Tolak("Foto nota lebih dari 15 MB.")


def _simpan_foto(b, foto):
    if foto is None:
        return
    kunci = default_storage.save(f"master/biaya/{b.pk}/{uuid.uuid4().hex}{JENIS_FOTO[foto.content_type]}", foto)
    lama = b.foto_key
    b.foto_key, b.foto_type = kunci, foto.content_type
    b.save(update_fields=["foto_key", "foto_type"])
    if lama:
        transaction.on_commit(lambda: default_storage.delete(lama))


def _baca_jumlah(teks):
    angka = "".join(ch for ch in str(teks or "") if ch.isdigit())
    if not angka or int(angka) <= 0:
        raise Tolak("Isi jumlah uangnya.")
    return int(angka)


def catat(pengguna, toko, data, foto=None, dari="master"):
    """Pemilik mencatat pengeluaran. Langsung disetujui.

    Sumber laci: dicatat sebagai uang keluar di kas toko itu (angka kas ikut berkurang),
    lalu muncul di sini lewat `tarik_dari_kas`.
    """
    from master.layanan import kas as svc_kas
    kategori = rapikan_teks(data.get("kategori"))
    if kategori not in {k.nama for k in kategori_aktif()}:
        raise Tolak("Pilih kategori.")
    jumlah = _baca_jumlah(data.get("jumlah"))
    keterangan = rapikan_teks(data.get("keterangan"))[:300]
    if kategori == "Lainnya" and not keterangan:
        raise Tolak("Tulis keterangan untuk kategori Lainnya.")
    sumber = data.get("sumber") or "luar_laci"
    if sumber not in dict(Biaya.SUMBER):
        raise Tolak("Pilih sumber uangnya.")
    _cek_foto(foto)
    if toko is None:
        raise Tolak("Pilih tokonya dulu.")
    with transaction.atomic():
        if sumber == "laci":
            g = svc_kas.catat_gerak(pengguna, toko, {"gerak_id": str(uuid.uuid4()), "jenis": "keluar",
                                                     "jumlah": jumlah, "kategori": kategori, "keterangan": keterangan},
                                    dari=dari)
            tarik_dari_kas()
            b = Biaya.objects.get(gerak_id=g.gerak_id)
        else:
            from master.layanan.pembelian import baca_tanggal
            tanggal = baca_tanggal(data.get("tanggal"), label="Tanggal") or timezone.localdate()
            if tanggal > timezone.localdate():
                raise Tolak("Tanggal tidak boleh di masa depan.")
            nama = svc_kas.nama(pengguna)
            b = Biaya.objects.create(toko=toko, tanggal=tanggal, kategori=kategori, jumlah=jumlah, sumber=sumber,
                                     keterangan=keterangan, dicatat_oleh=nama, status="disetujui",
                                     acc_oleh=nama, acc_waktu=timezone.now())
            layanan.catat(pengguna, aksi="tambah", objek="biaya", kolom=kategori,
                          baru=f"{rp(jumlah)} {b.get_sumber_display().lower()} {keterangan}".strip())
        _simpan_foto(b, foto)
    return b


def acc(pengguna, biaya_id, setuju, alasan=""):
    from master.layanan.kas import nama
    alasan = rapikan_teks(alasan)[:300]
    if not setuju and not alasan:
        raise Tolak("Tulis alasan menolak.")
    with transaction.atomic():
        b = Biaya.objects.select_for_update().filter(pk=biaya_id).first()
        if b is None:
            raise Tolak("Catatan biaya tidak ditemukan.")
        if b.status != "menunggu":
            raise Tolak("Biaya ini sudah diputuskan.")
        b.status = "disetujui" if setuju else "ditolak"
        b.acc_oleh, b.acc_waktu, b.alasan = nama(pengguna), timezone.now(), alasan
        b.save(update_fields=["status", "acc_oleh", "acc_waktu", "alasan"])
        layanan.catat(pengguna, aksi="ubah", objek="biaya", kolom="ACC biaya",
                      baru=f"{'disetujui' if setuju else 'ditolak'}: {b.kategori} {rp(b.jumlah)} ({b.dicatat_oleh})",
                      catatan=alasan)
    return b


def tandai_diganti(pengguna, biaya_id, sudah=True):
    from master.layanan.kas import nama
    with transaction.atomic():
        b = Biaya.objects.select_for_update().filter(pk=biaya_id).first()
        if b is None or b.status != "ditolak" or b.sumber != "laci":
            raise Tolak("Hanya uang laci yang ditolak yang perlu diganti.")
        b.diganti = bool(sudah)
        b.diganti_waktu = timezone.now() if sudah else None
        b.diganti_oleh = nama(pengguna) if sudah else ""
        b.save(update_fields=["diganti", "diganti_waktu", "diganti_oleh"])
        layanan.catat(pengguna, aksi="ubah", objek="biaya", kolom="uang diganti",
                      baru=f"{b.dicatat_oleh} {rp(b.jumlah)}: {'sudah' if sudah else 'belum'} diganti")
    return b


def batal(pengguna, biaya_id, alasan):
    """Batalkan catatan tunai luar laci. Uang laci dibatalkan lewat kas, supaya angka kas ikut kembali."""
    from master.layanan import kas as svc_kas
    alasan = rapikan_teks(alasan)[:300]
    if not alasan:
        raise Tolak("Tulis alasan membatalkan.")
    b = Biaya.objects.filter(pk=biaya_id).first()
    if b is None or b.status == "batal":
        raise Tolak("Catatan biaya tidak ditemukan.")
    if b.gerak_id:
        svc_kas.batal_gerak(pengguna, b.gerak_id, alasan)
        tarik_dari_kas()
        b.refresh_from_db()
        return b
    b.status, b.alasan = "batal", alasan
    b.save(update_fields=["status", "alasan"])
    layanan.catat(pengguna, aksi="hapus", objek="biaya", kolom=b.kategori, lama=rp(b.jumlah), catatan=alasan)
    return b


def buka_foto(b):
    return default_storage.open(b.foto_key, "rb")


# ---------------------------------------------------------------------------
# Daftar & ringkasan
# ---------------------------------------------------------------------------

def bulan_ini():
    return timezone.localdate().replace(day=1)


def rentang_bulan(teks):
    """'2026-11' -> (1 Nov, 1 Des). Kosong/salah -> bulan ini."""
    try:
        awal = datetime.date(int(teks[:4]), int(teks[5:7]), 1)
    except (TypeError, ValueError):
        awal = bulan_ini()
    akhir = (awal + datetime.timedelta(days=32)).replace(day=1)
    return awal, akhir


def daftar(toko, awal, akhir, status="", kategori=""):
    qs = Biaya.objects.filter(tanggal__gte=awal, tanggal__lt=akhir).select_related("toko")
    if toko is not None:
        qs = qs.filter(toko=toko)
    if status == "perlu_diganti":
        qs = qs.filter(status="ditolak", sumber="laci", diganti=False)
    elif status in dict(Biaya.STATUS):
        qs = qs.filter(status=status)
    if kategori:
        qs = qs.filter(kategori=kategori)
    return qs


def terbuka(toko):
    """Semua biaya toko itu tanpa batas bulan (untuk daftar menunggu ACC / perlu diganti)."""
    qs = Biaya.objects.select_related("toko").order_by("dicatat_waktu")
    return qs if toko is None else qs.filter(Q(toko=toko) | Q(toko=None))


def ringkas(toko, awal, akhir):
    """Total per kategori (hanya yang disetujui), dipisah biaya usaha / bukan biaya."""
    qs = Biaya.objects.filter(tanggal__gte=awal, tanggal__lt=akhir)
    if toko is not None:
        qs = qs.filter(toko=toko)
    usaha = kategori_usaha()
    per = (qs.filter(status="disetujui").values("kategori").annotate(total=Sum("jumlah"), n=Count("id"))
           .order_by("-total"))
    baris_usaha = [p for p in per if p["kategori"] in usaha]
    baris_lain = [p for p in per if p["kategori"] not in usaha]
    hitung = qs.aggregate(menunggu=Count("id", filter=Q(status="menunggu")),
                          menunggu_rp=Sum("jumlah", filter=Q(status="menunggu")),
                          tanggungan=Count("id", filter=Q(status="ditolak", sumber="laci", diganti=False)),
                          tanggungan_rp=Sum("jumlah", filter=Q(status="ditolak", sumber="laci", diganti=False)))
    return {
        "usaha": baris_usaha, "total_usaha": sum(p["total"] for p in baris_usaha),
        "lain": baris_lain, "total_lain": sum(p["total"] for p in baris_lain),
        "menunggu": hitung["menunggu"] or 0, "menunggu_rp": hitung["menunggu_rp"] or 0,
        "perlu_diganti": hitung["tanggungan"] or 0, "perlu_diganti_rp": hitung["tanggungan_rp"] or 0,
    }


def nama_berkas(b):
    return os.path.basename(b.foto_key or "")
