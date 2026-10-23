"""Buat toko pertama dari data yang sudah ada, lalu tandai semua data lama sebagai milik toko itu."""
from django.db import migrations


def isi(apps, schema_editor):
    Toko = apps.get_model("master", "Toko")
    if Toko.objects.exists():
        return
    Pengaturan = apps.get_model("master", "Pengaturan")
    atur = Pengaturan.objects.filter(pk=1).first()
    toko = Toko.objects.create(kode="T1", nama="Toko 1", stok_di_kasir=True, urutan=1,
                               pkp=bool(atur and atur.pkp), pkp_mulai=atur.pkp_mulai if atur else None)
    for nama in ("Pembelian", "FakturManual", "Opname"):
        apps.get_model("master", nama).objects.filter(toko__isnull=True).update(toko=toko)


class Migration(migrations.Migration):
    dependencies = [("master", "0009_toko")]
    operations = [migrations.RunPython(isi, migrations.RunPython.noop)]
