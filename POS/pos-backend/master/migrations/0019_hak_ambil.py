"""Akun yang sudah boleh mencatat barang datang juga mendapat hak konfirmasi barang diambil."""
from django.db import migrations


def maju(apps, schema_editor):
    HakAkun = apps.get_model("master", "HakAkun")
    for h in HakAkun.objects.all():
        if "gudang_datang" in h.hak and "gudang_ambil" not in h.hak:
            h.hak = sorted(set(h.hak) | {"gudang_ambil"})
            h.save(update_fields=["hak"])


class Migration(migrations.Migration):
    dependencies = [("master", "0018_permintaan_ambil")]
    operations = [migrations.RunPython(maju, migrations.RunPython.noop)]
