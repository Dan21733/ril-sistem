"""Permintaan ambil barang dari gudang.

Dibuat otomatis saat barang terjual (nota manual, nanti juga kasir) atau dikirim
(transfer) dari lokasi gudang. Total stok sudah berkurang saat itu; stok gudang
baru berkurang saat kepala gudang atau pemilik mengonfirmasi "sudah diambil".
"""
from collections import defaultdict

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import MutasiLokasi, PermintaanAmbil

HAK = "gudang_ambil"


def buat(info, lokasi, jumlah, *, sumber, referensi, nota_manual=None, transfer=None):
    return PermintaanAmbil.objects.create(
        sumber=sumber, referensi=referensi[:60], nota_manual=nota_manual, transfer=transfer,
        varian=info, item_name=info.item_name, variant=info.variant, lokasi=lokasi, jumlah=jumlah)


def dipesan(varian_ids):
    """{varian_id: jumlah} yang masih menunggu diambil (masih ada di gudang, tapi sudah terjual)."""
    hasil = defaultdict(int)
    for v, n in (PermintaanAmbil.objects.filter(status="menunggu", varian_id__in=list(varian_ids))
                 .values("varian_id").annotate(n=Sum("jumlah")).values_list("varian_id", "n")):
        hasil[v] = n or 0
    return hasil


def konfirmasi(pengguna, p, *, jumlah=None, lokasi=None, catatan="", oleh_pemilik=False):
    from master.layanan.lokasi import lokasi_toko, ubah_gudang
    with transaction.atomic():
        p = PermintaanAmbil.objects.select_for_update().select_related("varian", "lokasi").get(pk=p.pk)
        if p.status != "menunggu":
            raise Tolak("Permintaan ini sudah diproses.")
        qty = p.jumlah if jumlah in (None, "") else baca_angka(jumlah, boleh_kosong=False)
        if qty < 0:
            raise Tolak("Jumlah tidak boleh minus.")
        lokasi = lokasi or p.lokasi
        if lokasi.jenis != "gudang":
            raise Tolak("Pilih gudang tempat barang diambil.")
        catatan = rapikan_teks(catatan)[:300]
        if qty != p.jumlah and not catatan:
            raise Tolak(f"{p.item_name}: jumlah yang diambil beda dari permintaan. Isi catatannya.")
        ubah_gudang(p.varian, lokasi, -qty)
        if qty:
            MutasiLokasi.objects.create(
                pengguna=pengguna if getattr(pengguna, "pk", None) else None,
                nama_pengguna=getattr(pengguna, "username", "") or "sistem",
                varian=p.varian, item_name=p.item_name, variant=p.variant, dari=lokasi, ke=lokasi_toko(),
                jumlah=qty, sumber="ambil", catatan=f"{p.referensi}" + (f". {catatan}" if catatan else ""))
        p.status = "diambil"
        p.diambil_jumlah, p.diambil_lokasi = qty, lokasi
        p.diambil_oleh, p.diambil_waktu = pengguna.username, timezone.now()
        p.oleh_pemilik, p.catatan = oleh_pemilik, catatan
        p.save()
        if qty != p.jumlah:
            layanan.catat(pengguna, item_name=p.item_name, variant=p.variant, kolom="ambil",
                          lama=f"diminta {p.jumlah}", baru=f"diambil {qty} dari {lokasi.nama}",
                          catatan=f"{p.referensi}: {catatan}")
    return p


def batalkan_untuk(qs, pengguna, alasan):
    """Dipanggil saat nota/transfer dibatalkan. Yang belum diambil cukup dibatalkan;
    yang sudah diambil dikembalikan ke gudang tempat ia diambil."""
    from master.layanan.lokasi import lokasi_toko, ubah_gudang
    for p in qs.select_for_update(of=("self",)).select_related("varian", "diambil_lokasi"):
        if p.status == "menunggu":
            p.status = "batal"
            p.catatan = rapikan_teks(alasan)[:300]
            p.save(update_fields=["status", "catatan"])
        elif p.status == "diambil" and p.diambil_jumlah:
            ubah_gudang(p.varian, p.diambil_lokasi, p.diambil_jumlah)
            MutasiLokasi.objects.create(
                pengguna=pengguna if getattr(pengguna, "pk", None) else None, nama_pengguna=pengguna.username,
                varian=p.varian, item_name=p.item_name, variant=p.variant, dari=lokasi_toko(), ke=p.diambil_lokasi,
                jumlah=p.diambil_jumlah, sumber="ambil", catatan=f"{p.referensi} dibatalkan: {alasan}")
