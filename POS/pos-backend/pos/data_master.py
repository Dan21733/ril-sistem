"""Data barang dari master yang ikut dibaca kasir: status aktif dan rak.

Master menyimpan `aktif` dan `rak` per varian di `master_varian_info`. Kasir
(config.settings) tidak memuat aplikasi master, jadi tabel itu dibaca lewat SQL,
dan hanya kalau tabelnya ada (database uji POS tidak punya tabel master).

- Varian nonaktif tidak dikirim ke tablet (daftar barang dan pencarian), jadi
  barang yang sudah pernah terjual bisa disembunyikan dari kasir tanpa dihapus.
- Kode rak dikirim supaya kasir tahu letak barang.
"""
import re

from django.db import DatabaseError, connection, transaction

TABEL = "master_varian_info"
_ada = None


def tersedia():
    global _ada
    if _ada is None:
        try:
            with connection.cursor() as cursor:
                _ada = TABEL in connection.introspection.table_names(cursor)
        except DatabaseError:
            _ada = False
    return _ada


def lupakan_cache():
    global _ada
    _ada = None


RAK_SQL = (
    "SELECT v.rak_id FROM master_varian_info v "
    "WHERE v.item_name = {t}.item_name AND v.variant = {t}.variant"
)
NONAKTIF_SQL = (
    "SELECT v.item_name, v.variant FROM master_varian_info v WHERE NOT v.aktif"
)


def saring(queryset):
    """Queryset Item: buang varian nonaktif dan tambahkan `rak_kode`."""
    if not tersedia():
        return queryset
    return queryset.extra(
        select={"rak_kode": RAK_SQL.format(t="item")},
        where=[
            "NOT EXISTS (SELECT 1 FROM master_varian_info v2 WHERE v2.item_name = item.item_name "
            "AND v2.variant = item.variant AND NOT v2.aktif)"
        ],
    )


def kunci_alami(teks):
    """Urutan enak dibaca: huruf besar-kecil sama, angka sebagai angka
    (CW 53 sebelum CW 415), spasi diabaikan. Sama dengan master."""
    rapat = re.sub(r"\s+", "", str(teks or "").casefold())
    return [int(b) if b.isdigit() else b for b in re.split(r"(\d+)", rapat)]


def urut_alami(items):
    return sorted(items, key=lambda i: (kunci_alami(i.item_name), kunci_alami(i.variant)))


def alias_untuk(nama_item):
    """{item_name: "frasa1 frasa2 ..."} dari tabel `item_alias` kasir.

    Tabel ini sudah disatukan dengan alias master (master/layanan/alias_kasir.py).
    Database tanpa tabel alias (uji POS lama) mengembalikan dict kosong.
    """
    from .models import ItemAliasPhrase

    if not nama_item:
        return {}
    hasil = {}
    try:
        # Savepoint: di PostgreSQL, query yang gagal di dalam transaksi
        # membatalkan sisa transaksi kalau tidak dibungkus.
        with transaction.atomic():
            baris = list(ItemAliasPhrase.objects.filter(item_name__in=nama_item).order_by(
                "item_name", "frasa").values_list("item_name", "frasa"))
        for nama, frasa in baris:
            hasil.setdefault(nama, []).append(frasa)
    except DatabaseError:
        return {}
    return {k: " ".join(v) for k, v in hasil.items()}
