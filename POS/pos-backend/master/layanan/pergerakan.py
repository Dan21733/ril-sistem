"""Laporan pergerakan stok: barang masuk, keluar, dan penyesuaian dalam satu periode,
plus barang yang tidak laku (masih ada stok, tidak terjual N hari).

Sumber: master.MutasiStok (semua perubahan lewat master) dan pos.MutasiKasir (penjualan kasir).
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from pos.models import Item, MutasiKasir

from master.models import MutasiStok, VarianInfo

KELOMPOK = {
    "masuk": ("Barang masuk", ["beli", "transfer_masuk", "retur_jual", "awal"]),
    "keluar": ("Barang keluar", ["jual_kasir", "jual_manual", "transfer_keluar", "retur_beli"]),
    "sesuai": ("Penyesuaian", ["hitung", "rusak", "hilang", "koreksi", "lain", "impor", "buka_dus"]),
}
LABEL = dict(MutasiStok.JENIS) | {"jual_kasir": "Penjualan kasir"}


def ringkasan(mulai, selesai):
    """Per jenis: jumlah barang bertambah/berkurang dan jumlah kejadian; plus per kategori (masuk vs keluar)."""
    per_jenis = defaultdict(lambda: {"tambah": 0, "kurang": 0, "kali": 0})
    per_barang = defaultdict(lambda: {"masuk": 0, "keluar": 0})
    jenis_ke_kelompok = {j: k for k, (_, daftar) in KELOMPOK.items() for j in daftar}

    def catat(jenis, nama, varian, jumlah):
        d = per_jenis[jenis]
        d["kali"] += 1
        if jumlah >= 0:
            d["tambah"] += jumlah
        else:
            d["kurang"] -= jumlah
        kel = jenis_ke_kelompok.get(jenis)
        if kel == "masuk":
            per_barang[(nama, varian)]["masuk"] += max(jumlah, 0)
        elif kel == "keluar":
            per_barang[(nama, varian)]["keluar"] += max(-jumlah, 0)

    for m in MutasiStok.objects.filter(waktu__gte=mulai, waktu__lt=selesai).values_list("jenis", "item_name", "variant", "jumlah"):
        catat(*m)
    for m in MutasiKasir.objects.filter(waktu__gte=mulai, waktu__lt=selesai).values_list("item_name", "variant", "jumlah"):
        catat("jual_kasir", *m)

    kelompok = []
    for kunci, (judul, daftar) in KELOMPOK.items():
        baris = [{"jenis": j, "label": LABEL.get(j, j), **per_jenis[j]} for j in daftar if j in per_jenis]
        kelompok.append({"kunci": kunci, "judul": judul, "baris": baris,
                         "tambah": sum(b["tambah"] for b in baris), "kurang": sum(b["kurang"] for b in baris)})

    kategori = dict(Item.objects.filter(item_name__in={k[0] for k in per_barang})
                    .values_list("item_name", "category").distinct())
    per_kat = defaultdict(lambda: {"masuk": 0, "keluar": 0})
    for (nama, _), d in per_barang.items():
        k = kategori.get(nama) or "(sudah dihapus)"
        per_kat[k]["masuk"] += d["masuk"]
        per_kat[k]["keluar"] += d["keluar"]
    daftar_kat = sorted(({"nama": k, **v} for k, v in per_kat.items()), key=lambda d: -(d["masuk"] + d["keluar"]))
    puncak = max((max(d["masuk"], d["keluar"]) for d in daftar_kat), default=0)
    for d in daftar_kat:
        d["bagian_masuk"] = round(d["masuk"] / puncak * 100) if puncak else 0
        d["bagian_keluar"] = round(d["keluar"] / puncak * 100) if puncak else 0
    return kelompok, daftar_kat[:20]


def tidak_laku(hari=60, batas=100, lihat_modal=False):
    """Varian aktif yang masih ada stok tapi tidak terjual `hari` hari terakhir (kasir maupun nota manual).

    Diurutkan dari nilai stok (modal x stok) terbesar kalau boleh lihat modal, selain itu dari stok terbanyak.
    """
    sejak = timezone.now() - timedelta(days=hari)
    laku = {(n, v) for n, v in MutasiKasir.objects.filter(waktu__gte=sejak).values_list("item_name", "variant")}
    laku |= {(n, v) for n, v in MutasiStok.objects.filter(waktu__gte=sejak, jenis="jual_manual")
             .values_list("item_name", "variant")}
    aktif = {(n, v) for n, v in VarianInfo.objects.filter(aktif=True, sampah__isnull=True).values_list("item_name", "variant")}
    terakhir = {(r["item_name"], r["variant"]): r["akhir"] for r in
                MutasiKasir.objects.values("item_name", "variant").annotate(akhir=Max("waktu"))}
    hasil = []
    for b in Item.objects.filter(in_stock__gt=0).values("item_name", "variant", "category", "in_stock", "cost_amount"):
        k = (b["item_name"], b["variant"])
        if k in laku or k not in aktif:
            continue
        nilai = (b["cost_amount"] or 0) * b["in_stock"]
        hasil.append({**b, "nilai": nilai if lihat_modal else None, "terakhir": terakhir.get(k)})
    hasil.sort(key=lambda d: (-(d["nilai"] or 0), -d["in_stock"]) if lihat_modal else (-d["in_stock"],))
    total_nilai = sum(d["nilai"] or 0 for d in hasil) if lihat_modal else None
    return hasil[:batas], len(hasil), total_nilai
