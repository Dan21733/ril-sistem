"""Nota penjualan manual: penjualan yang ditulis di nota tangan, dimasukkan dari master.

- Barang dipilih dari daftar barang (bukan teks bebas) supaya stok dan laporan benar.
- Harga diketik seperti di nota. Harga di bawah floor boleh, tapi ditandai.
- Kalau tokonya memegang stok kasir, stok langsung berkurang saat nota disimpan,
  dan kembali kalau nota dibatalkan. Toko tanpa kasir: hanya dicatat.
- Modal rata saat nota disimpan dibekukan per baris untuk laporan laba.
- Barang, jumlah, dan harga tidak diubah setelah disimpan; kalau salah, batalkan lalu input ulang.
  Yang boleh diperbaiki (dengan alasan, tercatat di Log): tanggal, nomor di nota, pembeli,
  cara bayar. Tidak menyentuh stok dan modal; nomor NM-... tetap.
"""
import json
import uuid
from datetime import datetime, time

from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from pos.models import Item

from master.layanan import ambil
from master.layanan import inti as layanan
from master.layanan.faktur import JENIS_BERKAS, MAKS_UKURAN
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import FotoNota, MutasiStok, NotaManual, NotaManualBaris, VarianInfo
from master.layanan.pembelian import baca_tanggal

MAKS_FOTO = 4


def _nomor_baru(tanggal):
    awalan = f"NM-{tanggal:%y%m}-"
    terakhir = NotaManual.objects.filter(nomor__startswith=awalan).order_by("-nomor").values_list("nomor", flat=True).first()
    return f"{awalan}{(int(terakhir.rsplit('-', 1)[1]) + 1) if terakhir else 1:04d}"


def waktu_nota(n):
    """Nota manual hanya punya tanggal; untuk laporan dianggap jam 12 siang."""
    return timezone.make_aware(datetime.combine(n.tanggal, time(12, 0)), timezone.get_current_timezone())


def _ubah_stok(pengguna, info, selisih, catatan, lokasi=None, ubah_lokasi=True):
    from master.layanan.lokasi import ubah_gudang
    baris = Item.objects.select_for_update().filter(item_name=info.item_name, variant=info.variant)
    sebelum = baris.values_list("in_stock", flat=True).first()
    if sebelum is None and not baris.exists():
        raise Tolak(f"{info.item_name} / {info.variant} sudah tidak ada di tabel barang kasir.")
    sebelum = sebelum or 0
    baris.update(in_stock=sebelum + selisih)
    MutasiStok.objects.create(pengguna=pengguna, nama_pengguna=pengguna.username, varian=info, item_name=info.item_name,
                              variant=info.variant, jenis="jual_manual", jumlah=selisih, stok_sebelum=sebelum,
                              stok_sesudah=sebelum + selisih, catatan=catatan, lokasi=lokasi)
    if ubah_lokasi:
        ubah_gudang(info, lokasi, selisih)


def _baca_baris(mentah):
    try:
        data = json.loads(mentah or "[]")
    except ValueError:
        raise Tolak("Daftar barang tidak terbaca. Muat ulang halaman lalu coba lagi.") from None
    if not isinstance(data, list) or not data:
        raise Tolak("Tambahkan minimal satu barang.")
    hasil, sudah = [], set()
    for d in data:
        info = VarianInfo.objects.filter(pk=d.get("id") if str(d.get("id", "")).isdigit() else 0).first()
        if info is None:
            raise Tolak("Ada barang yang tidak dikenal. Pilih barang dari hasil pencarian.")
        if info.pk in sudah:
            raise Tolak(f"{info.item_name} / {info.variant} dimasukkan dua kali. Gabungkan jumlahnya.")
        sudah.add(info.pk)
        qty = baca_angka(d.get("jumlah"), boleh_kosong=False)
        if qty <= 0:
            raise Tolak(f"{info.item_name}: jumlah harus lebih dari nol.")
        harga = baca_angka(d.get("harga"), boleh_kosong=False)
        hasil.append((info, qty, harga))
    return hasil


def buat(pengguna, data, berkas=()):
    from master.layanan import toko as svc_toko
    toko = svc_toko.ambil(data.get("toko"))
    tanggal = baca_tanggal(data.get("tanggal"), wajib=True, label="Tanggal nota")
    if tanggal > timezone.localdate():
        raise Tolak("Tanggal nota tidak boleh di masa depan.")
    nomor_tulisan = rapikan_teks(data.get("nomor_tulisan"))[:40]
    if nomor_tulisan and NotaManual.objects.filter(toko=toko, nomor_tulisan__iexact=nomor_tulisan, batal=False).exists():
        raise Tolak(f"Nota nomor {nomor_tulisan} di {toko.nama} sudah pernah dimasukkan.")
    cara = data.get("cara_bayar") or "cash"
    if cara not in dict(NotaManual.CARA):
        raise Tolak("Pilih cara bayar.")
    ongkir = baca_angka(data.get("ongkir"), boleh_kosong=True) or 0
    baris = _baca_baris(data.get("baris"))
    lokasi = None
    if toko.stok_di_kasir:
        from master.layanan.lokasi import baca
        lokasi = baca(data.get("lokasi"))
        lokasi = lokasi if lokasi.jenis == "gudang" else None
    if len(berkas) > MAKS_FOTO:
        raise Tolak(f"Paling banyak {MAKS_FOTO} foto per nota.")
    for f in berkas:
        if f.content_type not in JENIS_BERKAS or f.size > MAKS_UKURAN:
            raise Tolak(f"{f.name}: hanya foto JPG/PNG/WEBP atau PDF, paling besar 15 MB.")

    with transaction.atomic():
        n = NotaManual.objects.create(
            nomor=_nomor_baru(tanggal), toko=toko, nomor_tulisan=nomor_tulisan, tanggal=tanggal,
            pembeli=rapikan_teks(data.get("pembeli"))[:150], cara_bayar=cara, ongkir=ongkir,
            catatan=(data.get("catatan") or "").strip()[:1000], dibuat_oleh=pengguna.username)
        total = 0
        modal_item = {(i, v): c for i, v, c in Item.objects.filter(item_name__in={b[0].item_name for b in baris})
                      .values_list("item_name", "variant", "cost_amount")}
        for info, qty, harga in baris:
            modal = info.modal_rata if info.modal_rata is not None else modal_item.get((info.item_name, info.variant))
            NotaManualBaris.objects.create(nota=n, varian=info, item_name=info.item_name, variant=info.variant,
                                           jumlah=qty, harga=harga, subtotal=qty * harga, modal_satuan=modal,
                                           lokasi=lokasi)
            total += qty * harga
            if toko.stok_di_kasir:
                # Dari gudang: stok gudang baru berkurang saat kepala gudang mengonfirmasi barang diambil.
                _ubah_stok(pengguna, info, -qty, f"{n.nomor}" + (f" (nota {nomor_tulisan})" if nomor_tulisan else ""),
                           lokasi, ubah_lokasi=False)
                if lokasi is not None:
                    ambil.buat(info, lokasi, qty, sumber="nota_manual", referensi=n.nomor, nota_manual=n)
        n.total_barang, n.total = total, total + ongkir
        n.save(update_fields=["total_barang", "total"])
        for f in berkas:
            kunci = default_storage.save(f"master/nota/{n.pk}/{uuid.uuid4().hex}{JENIS_BERKAS[f.content_type]}", f)
            FotoNota.objects.create(nota=n, object_key=kunci, content_type=f.content_type, ukuran=f.size)
        layanan.catat(pengguna, aksi="tambah", objek="nota_manual", kolom="nota_manual",
                      baru=f"{n.nomor} {toko.kode}: {n.total}", catatan=nomor_tulisan)
    return n


def batalkan(pengguna, n, alasan):
    alasan = rapikan_teks(alasan)[:300]
    if not alasan:
        raise Tolak("Isi alasan pembatalan.")
    with transaction.atomic():
        n = NotaManual.objects.select_for_update().select_related("toko").get(pk=n.pk)
        if n.batal:
            raise Tolak("Nota ini sudah dibatalkan.")
        if n.toko.stok_di_kasir:
            for b in n.baris.select_related("varian"):
                _ubah_stok(pengguna, b.varian, b.jumlah, f"{n.nomor} dibatalkan: {alasan}", b.lokasi, ubah_lokasi=False)
            ambil.batalkan_untuk(n.permintaan.all(), pengguna, alasan)
        n.batal, n.batal_alasan, n.batal_oleh, n.batal_waktu = True, alasan, pengguna.username, timezone.now()
        n.save()
        layanan.catat(pengguna, objek="nota_manual", kolom="batal", lama=n.total, baru=0, catatan=f"{n.nomor}: {alasan}")
    return n


# ---------------------------------------------------------------------------
# Perbaikan keterangan nota (bukan barang/harga)
# ---------------------------------------------------------------------------

LABEL_UBAH = {"tanggal": "Tanggal", "nomor_tulisan": "Nomor di nota", "pembeli": "Pembeli", "cara_bayar": "Cara bayar"}


def _nilai_baru(n, data):
    """Nilai baru per kolom yang dikirim. Kolom yang tidak dikirim dibiarkan."""
    baru = {}
    if "tanggal" in data:
        tanggal = baca_tanggal(data.get("tanggal"), wajib=True, label="Tanggal nota")
        if tanggal > timezone.localdate():
            raise Tolak("Tanggal nota tidak boleh di masa depan.")
        baru["tanggal"] = tanggal
    if "nomor_tulisan" in data:
        nomor = rapikan_teks(data.get("nomor_tulisan"))[:40]
        if nomor and NotaManual.objects.filter(toko=n.toko, nomor_tulisan__iexact=nomor, batal=False).exclude(pk=n.pk).exists():
            raise Tolak(f"Nota nomor {nomor} di {n.toko.nama} sudah ada.")
        baru["nomor_tulisan"] = nomor
    if "pembeli" in data:
        baru["pembeli"] = rapikan_teks(data.get("pembeli"))[:150]
    if "cara_bayar" in data:
        if data.get("cara_bayar") not in dict(NotaManual.CARA):
            raise Tolak("Pilih cara bayar.")
        baru["cara_bayar"] = data.get("cara_bayar")
    return baru


def ubah(pengguna, n, data, alasan):
    """Perbaiki tanggal/nomor di nota/pembeli/cara bayar. Mengembalikan daftar kolom yang berubah."""
    alasan = rapikan_teks(alasan)[:300]
    if not alasan:
        raise Tolak("Isi alasan perubahan, mis. 'salah tanggal waktu input'.")
    with transaction.atomic():
        n = NotaManual.objects.select_for_update().select_related("toko").get(pk=n.pk)
        if n.batal:
            raise Tolak("Nota yang sudah dibatalkan tidak bisa diubah.")
        berubah = [(k, getattr(n, k), v) for k, v in _nilai_baru(n, data).items() if getattr(n, k) != v]
        if not berubah:
            return []
        for k, lama, v in berubah:
            setattr(n, k, v)
            label = dict(NotaManual.CARA) if k == "cara_bayar" else {}
            layanan.catat(pengguna, objek="nota_manual", kolom=f"{LABEL_UBAH[k].lower()} nota",
                          lama=f"{n.nomor}: {label.get(lama, lama) or '(kosong)'}",
                          baru=f"{n.nomor}: {label.get(v, v) or '(kosong)'}", catatan=alasan)
        n.save(update_fields=[k for k, _, _ in berubah])
    return [k for k, _, _ in berubah]


def ubah_tanggal_massal(pengguna, ids, tanggal, alasan):
    """Ganti tanggal beberapa nota sekaligus. Hasil: (jumlah berubah, nomor yang dilewati karena batal)."""
    ids = [int(x) for x in ids if str(x).isdigit()]
    if not ids:
        raise Tolak("Centang nota yang tanggalnya mau diganti.")
    if not rapikan_teks(alasan):
        raise Tolak("Isi alasan perubahan, mis. 'salah tanggal waktu input'.")
    baca_tanggal(tanggal, wajib=True, label="Tanggal baru")
    berubah, lewat = 0, []
    with transaction.atomic():
        for n in NotaManual.objects.filter(pk__in=ids).select_related("toko").order_by("id"):
            if n.batal:
                lewat.append(n.nomor)
                continue
            berubah += bool(ubah(pengguna, n, {"tanggal": tanggal}, alasan))
    return berubah, lewat
