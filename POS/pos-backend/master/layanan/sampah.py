"""Keranjang sampah barang.

Buang  : baris `item` (tabel kasir) disalin utuh ke `Sampah.data_item` lalu
         dihapus, jadi barang hilang dari kasir dan dari semua daftar master.
         `VarianInfo` tetap ada: ID, PO, opname, transfer, nota manual, dan log
         tetap menunjuk ke barang yang sama.
Pulihkan: baris `item` dikembalikan dari salinan.
Hapus permanen: hanya untuk barang tanpa riwayat, dan wajib password akun.
"""
from django.db import connection, transaction

from pos.models import Item

from master.layanan.inti import Tolak, _kolom_tabel, catat
from master.models import (
    ItemInfo,
    Konversi,
    MutasiLokasi,
    MutasiStok,
    StokLokasi,
    NotaManualBaris,
    OpnameBaris,
    PembelianBaris,
    Sampah,
    TransferBaris,
    VarianInfo,
)

HAK = "hapus_barang"


def _baris_item(info):
    q = connection.ops.quote_name
    kolom = _kolom_tabel("item")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(q(k) for k in kolom)} FROM item WHERE item_name = %s AND variant = %s",
            [info.item_name, info.variant],
        )
        isi = cursor.fetchone()
    return dict(zip(kolom, isi)) if isi else None


def baris_nota_kasir(info):
    from pos.models import TransactionDetail

    return TransactionDetail.objects.filter(item_name=info.item_name, variant=info.variant).count()


def dokumen_terbuka(info):
    """Dokumen yang belum selesai dan memakai varian ini. Membuangnya akan membuat dokumen itu macet."""
    hasil = []
    for b in PembelianBaris.objects.filter(varian=info, pembelian__status__in=("dibuat", "menunggu")).select_related("pembelian"):
        hasil.append(f"PO {b.pembelian.nomor}")
    for b in OpnameBaris.objects.filter(varian=info, opname__status__in=("draf", "diajukan")).select_related("opname"):
        hasil.append(f"opname {getattr(b.opname, 'nomor', b.opname_id)}")
    for b in TransferBaris.objects.filter(varian=info, transfer__status__in=("draf", "dikirim")).select_related("transfer"):
        hasil.append(f"transfer {getattr(b.transfer, 'nomor', b.transfer_id)}")
    return sorted(set(hasil))


def riwayat(info):
    """Daftar riwayat yang membuat varian ini tidak boleh dihapus permanen."""
    from pos.models import TransactionDetail

    hitung = [
        ("baris nota kasir", TransactionDetail.objects.filter(item_name=info.item_name, variant=info.variant).count()),
        ("baris PO", PembelianBaris.objects.filter(varian=info).count()),
        ("baris opname", OpnameBaris.objects.filter(varian=info).count()),
        ("baris transfer", TransferBaris.objects.filter(varian=info).count()),
        ("baris nota manual", NotaManualBaris.objects.filter(varian=info).count()),
        ("riwayat stok", MutasiStok.objects.filter(varian=info).count()),
        ("pasangan buka dus", Konversi.objects.filter(dari=info).count() + Konversi.objects.filter(ke=info).count()),
        ("stok di gudang", StokLokasi.objects.filter(varian=info).exclude(jumlah=0).count()),
        ("riwayat pindah lokasi", MutasiLokasi.objects.filter(varian=info).count()),
    ]
    return [f"{n} {label}" for label, n in hitung if n]


def buang(pengguna, info, *, hak, alasan=""):
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh menghapus barang.")
    with transaction.atomic():
        info = VarianInfo.objects.select_for_update().get(pk=info.pk)
        if Sampah.objects.filter(varian=info).exists():
            raise Tolak("Barang ini sudah ada di keranjang sampah.")
        terjual = baris_nota_kasir(info)
        if terjual:
            raise Tolak(f"{info.item_name} / {info.variant} belum bisa dihapus: ada di {terjual} baris nota kasir, "
                        "dan database kasir mengunci barang yang sudah tercatat di nota. Ini bisa setelah kasir "
                        "punya tanda sembunyikan barang.")
        terbuka = dokumen_terbuka(info)
        if terbuka:
            raise Tolak("Barang ini masih dipakai dokumen yang belum selesai: " + ", ".join(terbuka)
                        + ". Selesaikan atau batalkan dulu.")
        data = _baris_item(info)
        if data is None:
            raise Tolak("Barang ini tidak ada di tabel kasir.")
        Item.objects.filter(item_name=info.item_name, variant=info.variant).delete()
        Konversi.objects.filter(dari=info).update(aktif=False)
        Konversi.objects.filter(ke=info).update(aktif=False)
        Sampah.objects.create(varian=info, data_item=data, oleh=getattr(pengguna, "username", ""),
                              alasan=(alasan or "")[:300])
        catat(pengguna, aksi="hapus", item_name=info.item_name, variant=info.variant, kolom="sampah",
              lama=f"stok {data.get('in_stock') or 0}", baru="dibuang ke sampah", catatan=alasan or "")
    return data.get("in_stock") or 0


def pulihkan(pengguna, sampah, *, hak):
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh memulihkan barang.")
    with transaction.atomic():
        sampah = Sampah.objects.select_for_update().select_related("varian").get(pk=sampah.pk)
        info = sampah.varian
        if Item.objects.filter(item_name=info.item_name, variant=info.variant).exists():
            raise Tolak(f"Sudah ada barang aktif bernama '{info.item_name} / {info.variant}'. "
                        "Ganti nama barang itu dulu.")
        kolom = _kolom_tabel("item")
        data = {k: v for k, v in sampah.data_item.items() if k in kolom}
        data["item_name"], data["variant"] = info.item_name, info.variant
        q = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO item ({', '.join(q(k) for k in data)}) VALUES ({', '.join(['%s'] * len(data))})",
                list(data.values()),
            )
        ItemInfo.objects.get_or_create(item_name=info.item_name)
        sampah.delete()
        catat(pengguna, aksi="tambah", item_name=info.item_name, variant=info.variant, kolom="sampah",
              lama="di sampah", baru=f"dipulihkan, stok {data.get('in_stock') or 0}",
              catatan="Pasangan buka dus tidak diaktifkan ulang otomatis.")
    return info


def hapus_permanen(pengguna, sampah, *, hak, password):
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh menghapus barang.")
    if not password or not pengguna.check_password(password):
        raise Tolak("Password salah. Barang tidak dihapus.")
    with transaction.atomic():
        sampah = Sampah.objects.select_for_update().select_related("varian").get(pk=sampah.pk)
        info = sampah.varian
        ada = riwayat(info)
        if ada:
            raise Tolak("Tidak bisa dihapus permanen karena punya riwayat (" + ", ".join(ada)
                        + "). Barang tetap di sampah.")
        nama, varian = info.item_name, info.variant
        sampah.delete()
        StokLokasi.objects.filter(varian=info, jumlah=0).delete()
        info.delete()
        if not VarianInfo.objects.filter(item_name=nama).exists() and not Item.objects.filter(item_name=nama).exists():
            ItemInfo.objects.filter(item_name=nama).delete()
        catat(pengguna, aksi="hapus", item_name=nama, variant=varian, kolom="sampah",
              lama="di sampah", baru="dihapus permanen")
