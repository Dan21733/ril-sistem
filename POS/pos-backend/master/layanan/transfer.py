"""Transfer barang antar toko.

Alur: Disiapkan -> Dalam perjalanan (dikirim) -> Diterima.
- Saat dikirim: modal per satuan dibekukan. Kalau toko asal adalah toko yang
  stoknya ada di kasir, stoknya berkurang saat itu juga.
- Saat diterima: jumlah yang sampai diisi (beda dari kiriman wajib catatan).
  Kalau toko tujuan adalah toko yang stoknya ada di kasir, stoknya bertambah.
  Nilai (jumlah diterima x modal) masuk saldo: toko tujuan berhutang ke toko asal.
- Modal rata tidak diubah: semua toko memakai modal barang yang sama.
- Stok toko yang belum punya kasir tidak dihitung sistem; transfernya tetap tercatat.
"""
from collections import defaultdict

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from pos.models import Item

from master.layanan import ambil
from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import MutasiStok, Pembayaran, PelunasanSaldo, Toko, Transfer, TransferBaris, VarianInfo
from master.layanan.pembelian import baca_tanggal


def _nomor_baru(tanggal):
    awalan = f"TR-{tanggal:%y%m}-"
    terakhir = Transfer.objects.filter(nomor__startswith=awalan).order_by("-nomor").values_list("nomor", flat=True).first()
    return f"{awalan}{(int(terakhir.rsplit('-', 1)[1]) + 1) if terakhir else 1:04d}"


def _harus(tr, *status):
    if tr.status not in status:
        raise Tolak(f"Tidak bisa dilakukan saat status {tr.get_status_display()}.")


def buat(pengguna, *, dari_id, ke_id, tanggal, catatan=""):
    dari = Toko.objects.filter(pk=dari_id or 0, aktif=True).first()
    ke = Toko.objects.filter(pk=ke_id or 0, aktif=True).first()
    if dari is None or ke is None:
        raise Tolak("Pilih toko asal dan toko tujuan.")
    if dari.pk == ke.pk:
        raise Tolak("Toko asal dan tujuan tidak boleh sama.")
    tanggal = baca_tanggal(tanggal, wajib=True, label="Tanggal")
    with transaction.atomic():
        tr = Transfer.objects.create(nomor=_nomor_baru(tanggal), dari=dari, ke=ke, tanggal=tanggal,
                                     catatan=rapikan_teks(catatan)[:500], dibuat_oleh=pengguna.username)
        layanan.catat(pengguna, aksi="tambah", objek="transfer", kolom="transfer", baru=f"{tr.nomor}: {dari.nama} -> {ke.nama}")
    return tr


def tambah_baris(tr, *, varian_id, jumlah):
    _harus(tr, "draf")
    info = VarianInfo.objects.filter(pk=varian_id or 0).first()
    if info is None:
        raise Tolak("Pilih barang dari hasil pencarian.")
    qty = baca_angka(jumlah, boleh_kosong=False)
    if qty <= 0:
        raise Tolak("Jumlah harus lebih dari nol.")
    if tr.baris.filter(varian=info).exists():
        raise Tolak("Barang itu sudah ada di daftar. Ubah jumlahnya di tabel.")
    return TransferBaris.objects.create(transfer=tr, varian=info, item_name=info.item_name, variant=info.variant,
                                        jumlah_kirim=qty)


def simpan_baris(tr, data):
    _harus(tr, "draf")
    with transaction.atomic():
        for b in tr.baris.all():
            if f"hapus_{b.id}" in data:
                b.delete()
                continue
            qty = baca_angka(data.get(f"qty_{b.id}"), boleh_kosong=False)
            if qty <= 0:
                raise Tolak(f"{b.item_name}: jumlah harus lebih dari nol.")
            b.jumlah_kirim = qty
            b.save(update_fields=["jumlah_kirim"])
            if tr.dari.stok_di_kasir and f"lokasi_{b.id}" in data:
                _isi_lokasi(b, data.get(f"lokasi_{b.id}"))


def _ubah_stok(pengguna, info, selisih, jenis, catatan, lokasi=None, ubah_lokasi=True):
    from master.layanan.lokasi import ubah_gudang
    baris = Item.objects.select_for_update().filter(item_name=info.item_name, variant=info.variant)
    sebelum = baris.values_list("in_stock", flat=True).first()
    if sebelum is None and not baris.exists():
        raise Tolak(f"{info.item_name} / {info.variant} sudah tidak ada di tabel barang kasir.")
    sebelum = sebelum or 0
    baris.update(in_stock=sebelum + selisih)
    MutasiStok.objects.create(pengguna=pengguna, nama_pengguna=pengguna.username, varian=info, item_name=info.item_name,
                              variant=info.variant, jenis=jenis, jumlah=selisih, stok_sebelum=sebelum,
                              stok_sesudah=sebelum + selisih, catatan=catatan, lokasi=lokasi)
    if ubah_lokasi:
        ubah_gudang(info, lokasi, selisih)


def _isi_lokasi(b, nilai):
    from master.layanan.lokasi import baca
    lokasi = baca(nilai)
    b.lokasi = lokasi if lokasi.jenis == "gudang" else None
    b.save(update_fields=["lokasi"])


def _modal(info):
    if info.modal_rata is not None:
        return info.modal_rata
    return Item.objects.filter(item_name=info.item_name, variant=info.variant).values_list("cost_amount", flat=True).first() or 0


def kirim(pengguna, tr):
    with transaction.atomic():
        tr = Transfer.objects.select_for_update().select_related("dari", "ke").get(pk=tr.pk)
        _harus(tr, "draf")
        if not tr.baris.exists():
            raise Tolak("Belum ada barang yang dikirim.")
        for b in tr.baris.select_related("varian"):
            b.modal_satuan = _modal(b.varian)
            b.save(update_fields=["modal_satuan"])
            if tr.dari.stok_di_kasir:
                # Dari gudang: stok gudang baru berkurang saat kepala gudang mengonfirmasi barang diambil.
                _ubah_stok(pengguna, b.varian, -b.jumlah_kirim, "transfer_keluar", f"{tr.nomor} ke {tr.ke.nama}", b.lokasi,
                           ubah_lokasi=False)
                if b.lokasi is not None:
                    ambil.buat(b.varian, b.lokasi, b.jumlah_kirim, sumber="transfer", referensi=tr.nomor, transfer=tr)
        tr.status = "dikirim"
        tr.dikirim_oleh, tr.dikirim_waktu = pengguna.username, timezone.now()
        tr.save()
        layanan.catat(pengguna, objek="transfer", kolom="status", lama="Disiapkan", baru="Dalam perjalanan", catatan=tr.nomor)
    return tr


def terima(pengguna, tr, data):
    catatan = rapikan_teks(data.get("catatan_terima"))[:500]
    with transaction.atomic():
        tr = Transfer.objects.select_for_update().select_related("dari", "ke").get(pk=tr.pk)
        _harus(tr, "dikirim")
        beda, nilai = False, 0
        for b in tr.baris.select_related("varian"):
            mentah = data.get(f"terima_{b.id}")
            if mentah is None or not str(mentah).strip():
                raise Tolak(f"{b.item_name}: jumlah yang sampai belum diisi. Isi 0 kalau tidak sampai.")
            qty = baca_angka(mentah, boleh_kosong=False)
            b.jumlah_terima = qty
            b.save(update_fields=["jumlah_terima"])
            beda = beda or qty != b.jumlah_kirim
            nilai += qty * b.modal_satuan
            if tr.ke.stok_di_kasir:
                _isi_lokasi(b, data.get(f"lokasi_{b.id}"))
                if qty:
                    _ubah_stok(pengguna, b.varian, qty, "transfer_masuk", f"{tr.nomor} dari {tr.dari.nama}", b.lokasi)
        if beda and not catatan:
            raise Tolak("Jumlah yang sampai beda dari kiriman. Isi catatannya.")
        tr.status = "diterima"
        tr.diterima_oleh, tr.diterima_waktu, tr.catatan_terima, tr.nilai = pengguna.username, timezone.now(), catatan, nilai
        tr.save()
        layanan.catat(pengguna, objek="transfer", kolom="status", lama="Dalam perjalanan", baru="Diterima",
                      catatan=f"{tr.nomor}: nilai {nilai}" + (f". {catatan}" if catatan else ""))
    return tr


def batal(pengguna, tr, alasan):
    alasan = rapikan_teks(alasan)[:300]
    if not alasan:
        raise Tolak("Isi alasan pembatalan.")
    with transaction.atomic():
        tr = Transfer.objects.select_for_update().select_related("dari", "ke").get(pk=tr.pk)
        _harus(tr, "draf", "dikirim")
        if tr.status == "dikirim" and tr.dari.stok_di_kasir:
            # Barang kembali ke toko asal.
            for b in tr.baris.select_related("varian"):
                _ubah_stok(pengguna, b.varian, b.jumlah_kirim, "transfer_masuk", f"{tr.nomor} dibatalkan: {alasan}", b.lokasi,
                           ubah_lokasi=False)
            ambil.batalkan_untuk(tr.permintaan.all(), pengguna, alasan)
        lama = tr.get_status_display()
        tr.status = "batal"
        tr.catatan = (tr.catatan + "\n" if tr.catatan else "") + f"Dibatalkan: {alasan}"
        tr.save()
        layanan.catat(pengguna, objek="transfer", kolom="status", lama=lama, baru="Batal", catatan=f"{tr.nomor}: {alasan}")


def saldo():
    """Daftar hutang antar toko yang belum lunas: [{penghutang, pemberi, jumlah}], jumlah > 0."""
    bersih = defaultdict(int)   # (a, b) -> a berhutang ke b sebesar ini (a.id < b.id, bisa minus)

    def tambah(penghutang, pemberi, nilai):
        if penghutang < pemberi:
            bersih[(penghutang, pemberi)] += nilai
        else:
            bersih[(pemberi, penghutang)] -= nilai

    for t in Transfer.objects.filter(status="diterima").values("dari_id", "ke_id").annotate(n=Sum("nilai")):
        tambah(t["ke_id"], t["dari_id"], t["n"] or 0)
    for p in PelunasanSaldo.objects.values("pembayar_id", "penerima_id").annotate(n=Sum("jumlah")):
        tambah(p["pembayar_id"], p["penerima_id"], -(p["n"] or 0))
    toko = {t.id: t for t in Toko.objects.all()}
    hasil = []
    for (a, b), n in bersih.items():
        if n > 0:
            hasil.append({"penghutang": toko[a], "pemberi": toko[b], "jumlah": n})
        elif n < 0:
            hasil.append({"penghutang": toko[b], "pemberi": toko[a], "jumlah": -n})
    hasil.sort(key=lambda h: -h["jumlah"])
    return hasil


def hutang_antara(penghutang, pemberi):
    return next((h["jumlah"] for h in saldo() if h["penghutang"].pk == penghutang.pk and h["pemberi"].pk == pemberi.pk), 0)


def lunasi(pengguna, *, pembayar_id, penerima_id, tanggal, jumlah, cara, catatan=""):
    pembayar = Toko.objects.filter(pk=pembayar_id or 0).first()
    penerima = Toko.objects.filter(pk=penerima_id or 0).first()
    if pembayar is None or penerima is None or pembayar.pk == penerima.pk:
        raise Tolak("Pilih toko yang membayar dan yang menerima.")
    nilai = baca_angka(jumlah, boleh_kosong=False)
    if nilai <= 0:
        raise Tolak("Jumlah harus lebih dari nol.")
    sisa = hutang_antara(pembayar, penerima)
    if nilai > sisa:
        raise Tolak(f"{pembayar.nama} hanya berhutang {sisa:,} ke {penerima.nama}.".replace(",", "."))
    if cara not in dict(Pembayaran.CARA):
        raise Tolak("Pilih cara bayar.")
    with transaction.atomic():
        p = PelunasanSaldo.objects.create(pembayar=pembayar, penerima=penerima, jumlah=nilai, cara=cara,
                                          tanggal=baca_tanggal(tanggal, wajib=True, label="Tanggal"),
                                          catatan=rapikan_teks(catatan)[:300], oleh=pengguna.username)
        layanan.catat(pengguna, aksi="tambah", objek="saldo_toko", kolom="pelunasan", baru=nilai,
                      catatan=f"{pembayar.nama} ke {penerima.nama}")
    return p
