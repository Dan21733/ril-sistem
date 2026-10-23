"""Buka dus: memecah kemasan besar menjadi satuan eceran.

- Pasangan (dari -> ke) dikunci di daftar konversi; tidak ada isian bebas.
- Stok "dari" berkurang, stok "ke" bertambah, dua-duanya tercatat di riwayat stok.
- Modal ikut pindah: modal satuan eceran = modal kemasan / isi sebenarnya.
- Harga floor dan harga jual tidak disentuh.
"""
from django.db import transaction

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import BukaDus, Konversi, MutasiStok, VarianInfo


def buat_konversi(pengguna, *, dari_id, ke_id, isi, isi_tidak_tetap=False, catatan=""):
    dari = VarianInfo.objects.filter(pk=dari_id).first()
    ke = VarianInfo.objects.filter(pk=ke_id).first()
    if dari is None or ke is None:
        raise Tolak("Pilih kedua barang dari hasil pencarian.")
    if dari.pk == ke.pk:
        raise Tolak("Barang asal dan hasil tidak boleh sama.")
    nilai = baca_angka(isi, boleh_kosong=False)
    if nilai < 2:
        raise Tolak("Isi per kemasan minimal 2.")
    if Konversi.objects.filter(dari=dari, ke=ke).exists():
        raise Tolak("Pasangan itu sudah ada.")
    if Konversi.objects.filter(dari=ke, ke=dari).exists():
        raise Tolak("Pasangan kebalikannya sudah ada.")
    k = Konversi.objects.create(dari=dari, ke=ke, isi=nilai, isi_tidak_tetap=bool(isi_tidak_tetap),
                                catatan=rapikan_teks(catatan)[:200])
    layanan.catat(pengguna, aksi="tambah", objek="konversi", kolom="konversi",
                  baru=f"{dari.item_name} / {dari.variant} -> {ke.item_name} / {ke.variant}, isi {nilai}")
    return k


def ubah_konversi(pengguna, k, *, isi, isi_tidak_tetap, aktif, catatan=""):
    nilai = baca_angka(isi, boleh_kosong=False)
    if nilai < 2:
        raise Tolak("Isi per kemasan minimal 2.")
    lama = f"isi {k.isi}, {'tidak tetap' if k.isi_tidak_tetap else 'tetap'}, {'aktif' if k.aktif else 'nonaktif'}"
    k.isi, k.isi_tidak_tetap, k.aktif, k.catatan = nilai, bool(isi_tidak_tetap), bool(aktif), rapikan_teks(catatan)[:200]
    k.save()
    baru = f"isi {k.isi}, {'tidak tetap' if k.isi_tidak_tetap else 'tetap'}, {'aktif' if k.aktif else 'nonaktif'}"
    if lama != baru:
        layanan.catat(pengguna, objek="konversi", kolom="konversi", lama=lama, baru=baru,
                      catatan=f"{k.dari.item_name} -> {k.ke.item_name}")


def buka(pengguna, k, *, jumlah, isi_total=None, catatan="", lokasi_dari=None, lokasi_ke=None):
    """Buka `jumlah` kemasan. `isi_total` hanya dipakai kalau isinya tidak tetap.

    lokasi_dari: tempat dus diambil; lokasi_ke: tempat isinya ditaruh. Kosong = Toko.
    Jumlah di gudang tidak diperiksa di sini, supaya kepala gudang tidak bisa mengintip
    stok lewat pesan galat; gudang yang jadi minus tampil sebagai temuan di master.
    """
    from master.layanan.lokasi import lokasi_toko, ubah_gudang
    lokasi_dari = lokasi_dari or lokasi_toko()
    lokasi_ke = lokasi_ke or lokasi_toko()
    if not k.aktif:
        raise Tolak("Pasangan ini nonaktif.")
    qty = baca_angka(jumlah, boleh_kosong=False)
    if qty <= 0:
        raise Tolak("Jumlah yang dibuka harus lebih dari nol.")
    standar = qty * k.isi
    if k.isi_tidak_tetap:
        total = baca_angka(isi_total, boleh_kosong=True)
        if total is None:
            total = standar
        if total <= 0:
            raise Tolak("Isi sebenarnya harus lebih dari nol.")
        # Pagar salah ketik: isi sebenarnya tidak wajar kalau terlalu jauh dari isi standar.
        if not standar * 0.5 <= total <= standar * 1.5:
            raise Tolak(f"Isi {total} terlalu jauh dari isi standar {standar}. Periksa lagi, atau ubah isi standarnya.")
    else:
        total = standar
    catatan = rapikan_teks(catatan)[:300]

    with transaction.atomic():
        dari = VarianInfo.objects.select_for_update().get(pk=k.dari_id)
        ke = VarianInfo.objects.select_for_update().get(pk=k.ke_id)
        baris_dari = Item.objects.select_for_update().filter(item_name=dari.item_name, variant=dari.variant)
        baris_ke = Item.objects.select_for_update().filter(item_name=ke.item_name, variant=ke.variant)
        a = baris_dari.values("in_stock", "cost_amount").first()
        b = baris_ke.values("in_stock", "cost_amount").first()
        if a is None or b is None:
            raise Tolak("Salah satu barang sudah tidak ada di tabel item.")
        stok_dari, stok_ke = a["in_stock"] or 0, b["in_stock"] or 0

        baris_dari.update(in_stock=stok_dari - qty)
        keterangan = f"{dari.item_name} / {dari.variant} -> {ke.item_name} / {ke.variant}"
        nama = pengguna.username
        MutasiStok.objects.create(pengguna=pengguna, nama_pengguna=nama, varian=dari, item_name=dari.item_name,
                                  variant=dari.variant, jenis="buka_dus", jumlah=-qty, stok_sebelum=stok_dari,
                                  stok_sesudah=stok_dari - qty, catatan=(keterangan + (". " + catatan if catatan else "")),
                                  lokasi=lokasi_dari if lokasi_dari.jenis == "gudang" else None)
        MutasiStok.objects.create(pengguna=pengguna, nama_pengguna=nama, varian=ke, item_name=ke.item_name,
                                  variant=ke.variant, jenis="buka_dus", jumlah=total, stok_sebelum=stok_ke,
                                  stok_sesudah=stok_ke + total, catatan=(keterangan + (". " + catatan if catatan else "")),
                                  lokasi=lokasi_ke if lokasi_ke.jenis == "gudang" else None)
        ubah_gudang(dari, lokasi_dari, -qty)
        ubah_gudang(ke, lokasi_ke, total)

        # Modal ikut pindah. Kalau modal kemasan kosong, modal eceran tidak diubah.
        modal_satuan = None
        ubah = {"in_stock": stok_ke + total}
        if a["cost_amount"]:
            modal_satuan = int(round(a["cost_amount"] * qty / total))
            modal_lama = b["cost_amount"]
            ubah["cost_amount"] = modal_satuan
            if modal_lama != modal_satuan:
                layanan.catat(pengguna, sumber="sistem", item_name=ke.item_name, variant=ke.variant,
                              kolom="cost_amount", lama=modal_lama, baru=modal_satuan, catatan="buka dus")
            sumber_rata = dari.modal_rata if dari.modal_rata else a["cost_amount"]
            rata_satuan = sumber_rata * qty / total
            dasar = max(stok_ke, 0)
            rata_lama = ke.modal_rata if ke.modal_rata is not None else (modal_lama if modal_lama is not None else rata_satuan)
            rata_baru = int(round((dasar * rata_lama + total * rata_satuan) / (dasar + total)))
            if ke.modal_rata != rata_baru:
                layanan.catat(pengguna, sumber="sistem", item_name=ke.item_name, variant=ke.variant,
                              kolom="modal_rata", lama=ke.modal_rata, baru=rata_baru, catatan="buka dus")
                ke.modal_rata = rata_baru
                ke.save(update_fields=["modal_rata"])
        baris_ke.update(**ubah)
        return BukaDus.objects.create(konversi=k, oleh=nama, jumlah=qty, isi_total=total,
                                      modal_satuan=modal_satuan, catatan=catatan,
                                      lokasi_dari=lokasi_dari if lokasi_dari.jenis == "gudang" else None,
                                      lokasi_ke=lokasi_ke if lokasi_ke.jenis == "gudang" else None)
