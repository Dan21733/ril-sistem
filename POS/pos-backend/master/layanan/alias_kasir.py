"""Alias barang: master sumber utama, tabel `item_alias` milik kasir mengikuti.

Kasir mencari lewat tabel `item_alias` (frasa -> item_name), master menyimpan
alias di `master_item_info.alias` (per barang) dan `master_varian_info.
alias_tambahan` (per varian). Supaya tidak ada dua tempat yang diisi
terpisah, setiap kali alias diubah di master, baris `item_alias` untuk barang itu
ditulis ulang dari master. Alias varian ikut dipetakan ke nama barangnya, karena
pencarian kasir mencocokkan alias per barang.

Sekali di awal (`gabung_sekali`), alias yang sudah ada di `item_alias` (hasil
impor berkas pembersihan pemilik) digabung dulu ke master supaya tidak hilang.
"""
from django.db import connection, transaction

from pos import pengaturan as aturan
from pos.models import ItemAliasPhrase

from master.layanan.inti import rapikan_alias
from master.models import ItemInfo, VarianInfo


def _tabel_ada():
    return ItemAliasPhrase._meta.db_table in connection.introspection.table_names()


def _pecah(teks):
    return [f for f in rapikan_alias(teks).split(", ") if f] if teks else []


def frasa_untuk(item_names):
    """{item_name: set(frasa)} dari alias barang + alias tambahan variannya."""
    hasil = {n: set() for n in item_names}
    for nama, alias in ItemInfo.objects.filter(item_name__in=item_names).values_list("item_name", "alias"):
        hasil[nama].update(_pecah(alias))
    for nama, alias in (VarianInfo.objects.filter(item_name__in=item_names).exclude(alias_tambahan="")
                        .values_list("item_name", "alias_tambahan")):
        hasil[nama].update(_pecah(alias))
    return hasil


def tulis(item_names):
    """Tulis ulang baris item_alias untuk barang-barang ini dari data master."""
    item_names = {n for n in item_names if n}
    if not item_names or not _tabel_ada():
        return 0
    frasa = frasa_untuk(item_names)
    baris = [ItemAliasPhrase(frasa=f[:255], item_name=n) for n, fs in frasa.items() for f in sorted(fs)]
    with transaction.atomic():
        ItemAliasPhrase.objects.filter(item_name__in=item_names).delete()
        ItemAliasPhrase.objects.bulk_create(baris, batch_size=1000, ignore_conflicts=True)
    return len(baris)


def ganti_nama(nama_lama, nama_baru):
    if not _tabel_ada():
        return
    ItemAliasPhrase.objects.filter(item_name=nama_lama).delete()
    tulis([nama_baru])


def gabung_sekali():
    """Gabungkan alias lama kasir ke master (sekali), lalu samakan semuanya."""
    if not _tabel_ada():
        return {"digabung": 0, "ditulis": 0}
    digabung = 0
    if aturan.baca("alias_tergabung") != "1":
        lama = {}
        for frasa, nama in ItemAliasPhrase.objects.values_list("frasa", "item_name"):
            lama.setdefault(nama, set()).add(frasa.strip().lower())
        infos = {i.item_name: i for i in ItemInfo.objects.filter(item_name__in=list(lama))}
        berubah = []
        for nama, fs in lama.items():
            info = infos.get(nama)
            if info is None:
                continue  # nama yang tidak ada di daftar barang master: dibiarkan
            gabungan = rapikan_alias(", ".join([info.alias or "", *sorted(fs)]))
            if gabungan != info.alias:
                info.alias = gabungan
                berubah.append(info)
                digabung += 1
        ItemInfo.objects.bulk_update(berubah, ["alias"], batch_size=500)
        aturan.simpan(alias_tergabung="1")
    nama = set(ItemInfo.objects.exclude(alias="").values_list("item_name", flat=True))
    nama |= set(VarianInfo.objects.exclude(alias_tambahan="").values_list("item_name", flat=True))
    return {"digabung": digabung, "ditulis": tulis(nama)}
