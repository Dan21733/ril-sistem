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


# ---------------------------------------------------------------------------
# Cari berdasarkan harga: "keran plastik @2000" (permintaan pemilik 30 Okt 2026)
# ---------------------------------------------------------------------------
# Nota tulis tangan sering tidak menulis jenis barang dengan lengkap, tapi harganya ada.
# Bagian setelah "@" (atau "~") dibaca sebagai harga; sisanya tetap kata pencarian biasa.
# "2000", "2.000", "2rb", "2,5rb", "1,2jt" semuanya dibaca. "@2000" saja = semua barang di sekitar harga itu.

import re  # noqa: E402

from django.db.models import BigIntegerField, ExpressionWrapper, F  # noqa: E402

POLA_HARGA = re.compile(r"[@~]\s*(\d[\d.,]*)\s*(rb|ribu|k|jt|juta)?(?![a-z])", re.IGNORECASE)
KALI = {"rb": 1000, "ribu": 1000, "k": 1000, "jt": 1000000, "juta": 1000000}
JAUH = 10 ** 13


def _baca_angka(angka, satuan):
    if not satuan:
        return int(angka.replace(".", "").replace(",", ""))
    if "," in angka:                                   # 2,5rb  1.250,5rb
        nilai = float(angka.replace(".", "").replace(",", "."))
    elif angka.count(".") == 1 and len(angka.split(".")[1]) <= 2:   # 2.5rb
        nilai = float(angka)
    else:
        nilai = float(angka.replace(".", ""))
    return round(nilai * KALI[satuan])


def pisah_harga(teks):
    """(teks tanpa bagian harga, harga atau None). Penanda harga: @ atau ~."""
    teks = teks or ""
    m = POLA_HARGA.search(teks)
    if not m:
        return " ".join(re.sub(r"[@~]", " ", teks).split()), None
    try:
        harga = _baca_angka(m.group(1), (m.group(2) or "").lower())
    except ValueError:
        harga = None
    sisa = re.sub(r"[@~]", " ", teks[:m.start()] + " " + teks[m.end():])
    return " ".join(sisa.split()), harga if harga and harga > 0 else None


def jarak_harga(harga, *, jual="max_price", floor="min_price"):
    """Ekspresi database: 0 harga pas, 1 di antara floor dan harga jual, selain itu jarak ke ujung terdekat + 1."""
    h = Value(harga, output_field=BigIntegerField())
    aturan = [When(**{f"{jual}__isnull": True}, then=Value(JAUH)), When(**{f"{jual}": harga}, then=Value(0))]
    if floor:
        aturan.append(When(**{f"{floor}__isnull": False, f"{floor}__lte": harga, f"{jual}__gte": harga}, then=Value(1)))
    satu = Value(1, output_field=BigIntegerField())
    aturan.append(When(**{f"{jual}__lt": harga}, then=ExpressionWrapper(h - F(jual) + satu, output_field=BigIntegerField())))
    if floor:
        aturan.append(When(**{f"{floor}__isnull": False}, then=ExpressionWrapper(F(floor) - h + satu, output_field=BigIntegerField())))
    return Case(*aturan, default=ExpressionWrapper(F(jual) - h + satu, output_field=BigIntegerField()), output_field=BigIntegerField())


def jarak_py(harga, jual, floor=None):
    """Sama dengan jarak_harga: 0 harga pas, 1 di atas floor (masih boleh), selain itu selisih + 1."""
    if jual is None:
        return JAUH
    if harga == jual:
        return 0
    if floor is not None and floor <= harga <= jual:
        return 1
    if harga > jual:
        return harga - jual + 1
    return (floor if floor is not None else jual) - harga + 1


def keterangan_harga(harga, jual, floor=None):
    """Teks singkat untuk hasil cari: 'harga pas', 'di atas floor', 'lebih mahal Rp500', 'lebih murah Rp250'."""
    if jual is None:
        return "harga kosong"
    if harga == jual:
        return "harga pas"
    if floor is not None and floor <= harga <= jual:
        return "di atas floor"
    selisih = jual - harga
    return f"{'lebih mahal' if selisih > 0 else 'lebih murah'} {rupiah(abs(selisih))}"


def rupiah(n):
    return f"Rp{n:,}".replace(",", ".")
