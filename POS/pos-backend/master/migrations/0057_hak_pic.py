"""Hak "pic" (12 Nov 2026): PIC gudang dipilih lewat centang di Karyawan & hak akses.

Supaya PIC yang sudah ada tidak hilang, akun yang sekarang memegang lokasi, rak, atau tim karyawan
lapangan otomatis diberi hak "pic".
"""
from django.db import migrations


def beri_pic(apps, schema_editor):
    Lokasi = apps.get_model("master", "Lokasi")
    Rak = apps.get_model("master", "Rak")
    Lapangan = apps.get_model("master", "KaryawanLapangan")
    HakAkun = apps.get_model("master", "HakAkun")
    AkunKaryawan = apps.get_model("master", "AkunKaryawan")
    ids = set(Lokasi.objects.exclude(pic=None).values_list("pic_id", flat=True))
    ids |= set(Rak.objects.exclude(pic=None).values_list("pic_id", flat=True))
    ids |= set(Lapangan.objects.exclude(ketua=None).values_list("ketua_id", flat=True))
    for h in HakAkun.objects.filter(user_id__in=ids, semua=False):
        if "pic" not in h.hak:
            h.hak = sorted(set(h.hak) | {"pic"})
            h.save(update_fields=["hak"])
    for a in AkunKaryawan.objects.filter(user_id__in=ids):
        if a.peran and "pic" not in a.peran:
            a.peran = list(a.peran) + ["pic"]
            a.save(update_fields=["peran"])


class Migration(migrations.Migration):
    dependencies = [("master", "0056_tiket_mulai")]
    operations = [migrations.RunPython(beri_pic, migrations.RunPython.noop)]
