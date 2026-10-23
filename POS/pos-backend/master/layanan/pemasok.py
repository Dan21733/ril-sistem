"""Barang ini diambil dari mana: catatan manual + riwayat pembelian (PO) yang sudah ada.

Nama supplier dan harga beli tidak untuk staf. Yang boleh melihat: akun dengan hak
"Pembelian" atau "Kelola supplier". Harga beli hanya untuk akun dengan hak "Melihat modal".
"""
from django.db.models import Count, Max

from master.layanan import inti as layanan
from master.layanan.inti import Tolak
from master.models import PembelianBaris, PemasokBarang, Supplier

HAK_LIHAT = ("pembelian", "kelola_supplier")


def boleh_lihat(hak):
    return any(h in hak for h in HAK_LIHAT)


def untuk_barang(item_info, hak):
    """{"catatan": [...], "riwayat": [...]} untuk panel barang."""
    catatan = list(PemasokBarang.objects.filter(item_info=item_info).select_related("supplier"))
    baris = (PembelianBaris.objects.filter(item_name=item_info.item_name).exclude(pembelian__status="batal")
             .values("pembelian__supplier_id", "pembelian__supplier__nama")
             .annotate(po=Count("pembelian", distinct=True), terakhir=Max("pembelian__tanggal_pesan"))
             .order_by("-terakhir"))
    riwayat = []
    for r in baris:
        h = {"supplier_id": r["pembelian__supplier_id"], "nama": r["pembelian__supplier__nama"],
             "po": r["po"], "terakhir": r["terakhir"], "harga": []}
        if "lihat_modal" in hak:
            # Harga beli terakhir per varian dari supplier ini.
            sudah = set()
            for b in (PembelianBaris.objects.filter(item_name=item_info.item_name, pembelian__supplier_id=h["supplier_id"])
                      .exclude(pembelian__status="batal").order_by("-pembelian__tanggal_pesan", "-id")
                      .values("variant", "harga", "pembelian__tanggal_pesan")):
                if b["variant"] not in sudah:
                    sudah.add(b["variant"])
                    h["harga"].append(b)
        riwayat.append(h)
    return {"catatan": catatan, "riwayat": riwayat}


def tambah(pengguna, item_info, *, nama_supplier, catatan, hak):
    if not boleh_lihat(hak):
        raise Tolak("Akun ini tidak boleh mengubah data supplier barang.")
    nama = layanan.rapikan_teks(nama_supplier)
    if not nama:
        raise Tolak("Isi nama supplier atau distributornya.")
    supplier = Supplier.objects.filter(nama__iexact=nama).first()
    if supplier is None:
        if "kelola_supplier" not in hak:
            raise Tolak(f"Supplier '{nama}' belum ada. Tambahkan dulu di menu Supplier.")
        supplier = Supplier.objects.create(nama=nama[:150])
        layanan.catat(pengguna, aksi="tambah", objek="supplier", kolom="supplier", baru=supplier.nama,
                      catatan="dibuat dari halaman barang")
    catatan = layanan.rapikan_teks(catatan)[:300]
    obj, baru = PemasokBarang.objects.get_or_create(
        item_info=item_info, supplier=supplier,
        defaults={"catatan": catatan, "dibuat_oleh": getattr(pengguna, "username", "")})
    if not baru:
        if obj.catatan == catatan:
            return obj
        obj.catatan = catatan
        obj.save(update_fields=["catatan"])
    layanan.catat(pengguna, aksi="tambah" if baru else "ubah", item_name=item_info.item_name, kolom="pemasok",
                  baru=f"{supplier.nama}" + (f": {catatan}" if catatan else ""))
    return obj


def hapus(pengguna, item_info, pemasok_id, *, hak):
    if not boleh_lihat(hak):
        raise Tolak("Akun ini tidak boleh mengubah data supplier barang.")
    obj = PemasokBarang.objects.filter(pk=pemasok_id, item_info=item_info).select_related("supplier").first()
    if obj is None:
        return
    layanan.catat(pengguna, aksi="hapus", item_name=item_info.item_name, kolom="pemasok",
                  lama=obj.supplier.nama)
    obj.delete()


def nama_barang_dari(supplier_id):
    """Nama barang (item_name) yang tercatat diambil dari supplier ini, manual maupun lewat PO."""
    manual = PemasokBarang.objects.filter(supplier_id=supplier_id).values_list("item_info__item_name", flat=True)
    po = PembelianBaris.objects.filter(pembelian__supplier_id=supplier_id).exclude(
        pembelian__status="batal").values_list("item_name", flat=True)
    return set(manual) | set(po)
