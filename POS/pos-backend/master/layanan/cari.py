"""Urutan hasil pencarian barang: yang cocok lewat NAMA dulu, alias paling akhir.

Semua pencarian barang di master dan aplikasi gudang memakai tingkat yang sama
(permintaan pemilik 27 Okt 2026: cari "besi" harus memunculkan "Besi ..." dulu,
bukan cat Avian yang aliasnya "cat besi"). Kasir sudah punya aturan yang sama
(pos/item_search.py dan localCatalogSearch.js: nama/varian dulu, alias belakangan).

Tingkat per kata (makin kecil makin atas), dijumlah untuk semua kata:
  0 nama barang diawali kata itu
  1 kata itu ada di nama barang
  2 ada di varian
  3 ada di brand / SKU / alias varian
  5 hanya cocok lewat alias barang
Tingkat dihitung di database, jadi hasil yang dipotong (mis. 60 teratas) tetap
mendahulukan yang cocok lewat nama.
"""
from django.db.models import Case, IntegerField, Value, When


def kata(teks):
    return [k for k in (teks or "").split(" ") if k]


def tingkat(teks, *, nama="item_name", varian="variant", lain=("brand", "sku")):
    """Ekspresi angka tingkat kecocokan untuk dipakai di annotate()/order_by()."""
    total = Value(0, output_field=IntegerField())
    for k in kata(teks):
        aturan = [When(**{f"{nama}__istartswith": k}, then=Value(0)),
                  When(**{f"{nama}__icontains": k}, then=Value(1))]
        if varian:
            aturan.append(When(**{f"{varian}__icontains": k}, then=Value(2)))
        aturan += [When(**{f"{kolom}__icontains": k}, then=Value(3)) for kolom in lain]
        total = total + Case(*aturan, default=Value(5), output_field=IntegerField())
    return total
