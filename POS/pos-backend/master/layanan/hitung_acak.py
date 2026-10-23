"""Hitung acak oleh pemilik.

Sistem memilih beberapa varian secara acak di satu lokasi. Pemilik menghitung
fisiknya tanpa melihat angka sistem; angka sistem baru dibekukan dan
dibandingkan saat hitungan diselesaikan. Selisih bisa diterapkan per baris.
"""
import secrets

from django.db import transaction
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka
from master.layanan.lokasi import lokasi_toko, stok_di
from master.models import HitungAcak, HitungAcakBaris, StokLokasi, VarianInfo

HAK = "opname"
BATAS = 50


def mulai(pengguna, *, lokasi, jumlah=10, hak):
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh membuat cek stok acak.")
    n = baca_angka(jumlah, boleh_kosong=True) or 10
    if not 1 <= n <= BATAS:
        raise Tolak(f"Jumlah barang 1 sampai {BATAS}.")
    if lokasi.jenis == "gudang":
        calon = list(StokLokasi.objects.filter(lokasi=lokasi, varian__sampah__isnull=True)
                     .exclude(jumlah=0).values_list("varian_id", flat=True))
    else:
        # Toko: varian yang masih ada stoknya di kasir.
        ada = set(Item.objects.filter(in_stock__gt=0).values_list("item_name", "variant"))
        calon = [v.id for v in VarianInfo.objects.filter(sampah__isnull=True, aktif=True)
                 if (v.item_name, v.variant) in ada]
    if not calon:
        raise Tolak(f"Tidak ada barang dengan stok di {lokasi.nama}.")
    acak = secrets.SystemRandom()
    pilih = acak.sample(calon, min(n, len(calon)))
    with transaction.atomic():
        h = HitungAcak.objects.create(oleh=pengguna.username, lokasi=lokasi)
        HitungAcakBaris.objects.bulk_create([HitungAcakBaris(hitung_acak=h, varian_id=v) for v in pilih])
    return h


def simpan(pengguna, h, data):
    if h.status != "draf":
        raise Tolak("Cek stok acak ini sudah selesai.")
    for b in h.baris.all():
        mentah = data.get(f"hitung_{b.id}")
        if mentah is not None and str(mentah).strip() != "":
            b.hitung = baca_angka(mentah, boleh_kosong=False)
            b.save(update_fields=["hitung"])


def selesai(pengguna, h, data):
    """Simpan hitungan terakhir, lalu bekukan stok sistem untuk dibandingkan."""
    simpan(pengguna, h, data)
    baris = list(h.baris.select_related("varian"))
    kosong = [b for b in baris if b.hitung is None]
    if kosong:
        raise Tolak(f"{len(kosong)} barang belum dihitung.")
    with transaction.atomic():
        for b in baris:
            b.stok_sistem = stok_di(b.varian, h.lokasi)
            b.save(update_fields=["stok_sistem"])
        h.status = "selesai"
        h.selesai_waktu = timezone.now()
        h.save(update_fields=["status", "selesai_waktu"])
        beda = sum(1 for b in baris if b.selisih)
        layanan.catat(pengguna, aksi="tambah", objek="hitung_acak", kolom="hitung_acak",
                      baru=f"Cek stok acak #{h.id} di {h.lokasi.nama}: {len(baris)} barang, {beda} selisih")


def terapkan(pengguna, h, baris_ids, *, hak):
    """Samakan stok sistem dengan hitungan untuk baris yang dipilih (selisihnya, bukan angkanya)."""
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh menerapkan selisih.")
    if h.status != "selesai":
        raise Tolak("Selesaikan hitungannya dulu.")
    pilih = {int(x) for x in baris_ids if str(x).isdigit()}
    n = 0
    with transaction.atomic():
        for b in h.baris.select_related("varian").filter(pk__in=pilih, diterapkan=False):
            if b.selisih:
                layanan.sesuaikan_stok(
                    pengguna, b.varian, cara="tambah" if b.selisih > 0 else "kurang", angka=abs(b.selisih),
                    jenis="hitung", hak=hak | {"ubah_stok"}, lokasi=h.lokasi,
                    catatan=f"Cek stok acak #{h.id} di {h.lokasi.nama}: dihitung {b.hitung}, sistem {b.stok_sistem}")
            b.diterapkan = True
            b.save(update_fields=["diterapkan"])
            n += 1
    return n


def lokasi_bawaan():
    return lokasi_toko()
