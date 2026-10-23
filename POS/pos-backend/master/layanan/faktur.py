"""Faktur supplier di luar PO (faktur manual).

Dipakai untuk pesanan mendadak yang tidak lewat PO dan untuk memasukkan faktur
lama. Yang dicatat hanya hutang: stok dan modal tidak berubah dari sini.
"""
import os
import uuid

from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Sum

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import BayarFaktur, FakturManual, FotoFaktur, Pembayaran, Pembelian, Supplier
from master.layanan.pembelian import baca_tanggal

MAKS_FOTO = 6
MAKS_UKURAN = 15 * 1024 * 1024
JENIS_BERKAS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}


def _supplier(nama):
    nama = rapikan_teks(nama)[:150]
    if not nama:
        raise Tolak("Isi nama supplier.")
    s = Supplier.objects.filter(nama__iexact=nama).first()
    return s or Supplier(nama=nama)


def _isian(data):
    nomor = rapikan_teks(data.get("nomor_faktur"))[:60]
    if not nomor:
        raise Tolak("Isi nomor faktur.")
    keterangan = (data.get("keterangan") or "").strip()
    if not keterangan:
        raise Tolak("Isi daftar barangnya.")
    total = baca_angka(data.get("total"), boleh_kosong=False)
    if total <= 0:
        raise Tolak("Total harus lebih dari nol.")
    tanggal = baca_tanggal(data.get("tanggal_faktur"), wajib=True, label="Tanggal faktur")
    jatuh_tempo = baca_tanggal(data.get("jatuh_tempo"), label="Jatuh tempo")
    if jatuh_tempo and jatuh_tempo < tanggal:
        raise Tolak("Jatuh tempo lebih awal dari tanggal faktur.")
    return {"nomor_faktur": nomor, "keterangan": keterangan[:4000], "total": total, "tanggal_faktur": tanggal,
            "jatuh_tempo": jatuh_tempo, "catatan": rapikan_teks(data.get("catatan"))[:500]}


def _cek_ganda(toko, supplier, nomor, kecuali=None):
    if supplier.pk is None:
        return
    lain = FakturManual.objects.filter(toko=toko, supplier=supplier, nomor_faktur__iexact=nomor)
    if kecuali:
        lain = lain.exclude(pk=kecuali.pk)
    if lain.exists():
        raise Tolak(f"Faktur {nomor} dari {supplier.nama} sudah pernah dicatat.")
    po = Pembelian.objects.filter(toko=toko, supplier=supplier, nomor_faktur__iexact=nomor).exclude(status="batal").first()
    if po:
        raise Tolak(f"Faktur {nomor} sudah tercatat di pesanan {po.nomor}. Jangan dicatat dua kali, nanti hutangnya dobel.")


def _cek_berkas(berkas, sudah=0):
    if len(berkas) + sudah > MAKS_FOTO:
        raise Tolak(f"Paling banyak {MAKS_FOTO} foto per faktur.")
    for f in berkas:
        if f.content_type not in JENIS_BERKAS:
            raise Tolak(f"{f.name}: hanya foto JPG/PNG/WEBP atau PDF.")
        if f.size > MAKS_UKURAN:
            raise Tolak(f"{f.name}: lebih dari 15 MB.")


def _simpan_berkas(faktur, berkas):
    for f in berkas:
        kunci = default_storage.save(f"master/faktur/{faktur.pk}/{uuid.uuid4().hex}{JENIS_BERKAS[f.content_type]}", f)
        FotoFaktur.objects.create(faktur=faktur, object_key=kunci, content_type=f.content_type,
                                  nama_asli=os.path.basename(f.name)[:200], ukuran=f.size)


def buat(pengguna, data, berkas=()):
    from master.layanan import toko as svc_toko
    isian = _isian(data)
    toko = svc_toko.ambil(data.get("toko"))
    supplier = _supplier(data.get("supplier"))
    _cek_ganda(toko, supplier, isian["nomor_faktur"])
    _cek_berkas(berkas)
    lunas = data.get("sudah_lunas") == "1"
    if lunas:
        tanggal_lunas = baca_tanggal(data.get("tanggal_lunas"), label="Tanggal lunas") or isian["tanggal_faktur"]
        cara = data.get("cara_lunas") or "transfer"
        if cara not in dict(Pembayaran.CARA):
            raise Tolak("Pilih cara bayar.")
    try:
        with transaction.atomic():
            if supplier.pk is None:
                supplier.save()
                layanan.catat(pengguna, aksi="tambah", objek="supplier", kolom="supplier", baru=supplier.nama,
                              catatan="dibuat dari faktur manual")
            f = FakturManual.objects.create(toko=toko, supplier=supplier, dibuat_oleh=pengguna.username, **isian)
            if lunas:
                BayarFaktur.objects.create(faktur=f, tanggal=tanggal_lunas, jumlah=f.total, cara=cara,
                                           catatan="Dicatat lunas saat faktur dimasukkan", oleh=pengguna.username)
                f.terbayar = f.total
                f.save(update_fields=["terbayar"])
            _simpan_berkas(f, berkas)
            layanan.catat(pengguna, aksi="tambah", sumber="pembelian", objek="faktur_manual", kolom="faktur_manual",
                          baru=f"{toko.kode} {supplier.nama} {f.nomor_faktur}: {f.total}" + (" (lunas)" if lunas else ""))
    except IntegrityError:
        raise Tolak("Faktur itu sudah pernah dicatat.") from None
    return f


def ubah(pengguna, f, data):
    from master.layanan import toko as svc_toko
    isian = _isian(data)
    toko = svc_toko.ambil(data.get("toko")) if data.get("toko") else f.toko
    supplier = _supplier(data.get("supplier"))
    _cek_ganda(toko, supplier, isian["nomor_faktur"], kecuali=f)
    if isian["total"] < f.terbayar:
        raise Tolak(f"Total tidak boleh lebih kecil dari yang sudah dibayar ({f.terbayar}).")
    with transaction.atomic():
        if supplier.pk is None:
            supplier.save()
            layanan.catat(pengguna, aksi="tambah", objek="supplier", kolom="supplier", baru=supplier.nama,
                          catatan="dibuat dari faktur manual")
        berubah = []
        for k, v in {**isian, "supplier": supplier, "toko": toko}.items():
            if getattr(f, k) != v:
                berubah.append(f"{k}: {getattr(f, k)} -> {v}" if k not in ("keterangan",) else k)
                setattr(f, k, v)
        if berubah:
            f.save()
            layanan.catat(pengguna, sumber="pembelian", objek="faktur_manual", kolom="faktur_manual",
                          baru="; ".join(berubah)[:1000], catatan=f"{f.supplier.nama} {f.nomor_faktur}")
    return bool(berubah)


def tambah_foto(pengguna, f, berkas):
    if not berkas:
        raise Tolak("Pilih foto dulu.")
    _cek_berkas(berkas, f.foto.count())
    with transaction.atomic():
        _simpan_berkas(f, berkas)


def hapus_foto(pengguna, f, foto_id):
    foto = f.foto.filter(pk=foto_id).first()
    if foto is None:
        return
    kunci = foto.object_key
    foto.delete()
    transaction.on_commit(lambda: default_storage.delete(kunci))


def bayar(pengguna, f, *, tanggal, jumlah, cara, catatan=""):
    with transaction.atomic():
        f = FakturManual.objects.select_for_update().get(pk=f.pk)
        nilai = baca_angka(jumlah, boleh_kosong=False)
        if nilai <= 0:
            raise Tolak("Jumlah bayar harus lebih dari nol.")
        if nilai > f.sisa:
            raise Tolak(f"Jumlah bayar melebihi sisa hutang ({f.sisa}).")
        if cara not in dict(Pembayaran.CARA):
            raise Tolak("Pilih cara bayar.")
        BayarFaktur.objects.create(faktur=f, tanggal=baca_tanggal(tanggal, wajib=True, label="Tanggal bayar"),
                                   jumlah=nilai, cara=cara, catatan=rapikan_teks(catatan)[:300], oleh=pengguna.username)
        f.terbayar = f.pembayaran.aggregate(t=Sum("jumlah"))["t"] or 0
        f.save(update_fields=["terbayar"])
        layanan.catat(pengguna, aksi="tambah", sumber="pembelian", objek="pembayaran", kolom="pembayaran",
                      baru=nilai, catatan=f"{f.supplier.nama} {f.nomor_faktur}: sisa {f.sisa}")
    return f


def hapus(pengguna, f):
    """Hanya untuk salah input: faktur yang belum ada pembayarannya."""
    if f.pembayaran.exists():
        raise Tolak("Faktur yang sudah ada pembayarannya tidak bisa dihapus.")
    kunci = list(f.foto.values_list("object_key", flat=True))
    with transaction.atomic():
        layanan.catat(pengguna, aksi="hapus", sumber="pembelian", objek="faktur_manual", kolom="faktur_manual",
                      lama=f"{f.supplier.nama} {f.nomor_faktur}: {f.total}")
        f.delete()
        transaction.on_commit(lambda: [default_storage.delete(k) for k in kunci])
