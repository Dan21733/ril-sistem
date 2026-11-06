"""Index waktu nota dan barang di tabel nota kasir (tabel POS, tidak dikelola Django).

Disetujui pemilik 6 Nov 2026. Hanya menambah index (IF NOT EXISTS); isi tabel tidak berubah.
"""
from django.db import migrations


def tambah(apps, schema_editor):
    from master.layanan.legacy import INDEKS_LAPORAN
    koneksi = schema_editor.connection
    tabel = set(koneksi.introspection.table_names())
    with koneksi.cursor() as cursor:
        for ddl in INDEKS_LAPORAN:
            if ddl.split(" ON ")[1].split(" ")[0] in tabel:
                cursor.execute(ddl)


class Migration(migrations.Migration):
    dependencies = [("master", "0039_kunci_perangkat_ops")]
    operations = [migrations.RunPython(tambah, migrations.RunPython.noop)]
