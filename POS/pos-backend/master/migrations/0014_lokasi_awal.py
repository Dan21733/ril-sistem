"""Lokasi awal (Toko/display) dan hak per akun dari peran yang sudah ada.

Hak disalin dari peran saat migrasi ini jalan, jadi akun yang sudah ada tidak
berubah kemampuannya. Akun berperan Gudang mendapat hak operasional yang
sekarang dipecah (barang datang, hitung stok, buka dus).
"""
from django.db import migrations

PERAN_LAMA = {
    "Finance": ["master", "ubah_harga", "ubah_info", "lihat_log", "lihat_stok"],
    "Gudang": ["gudang", "gudang_datang", "gudang_hitung", "gudang_buka_dus"],
}


def maju(apps, schema_editor):
    Lokasi = apps.get_model("master", "Lokasi")
    if not Lokasi.objects.filter(jenis="toko").exists():
        Lokasi.objects.create(kode="TOKO", nama="Toko (display)", jenis="toko", urutan=0)

    User = apps.get_model("auth", "User")
    HakAkun = apps.get_model("master", "HakAkun")
    for user in User.objects.filter(is_superuser=False):
        if HakAkun.objects.filter(user=user).exists():
            continue
        peran = set(user.groups.values_list("name", flat=True))
        if "Super Admin" in peran:
            HakAkun.objects.create(user=user, semua=True, hak=[])
            continue
        hak = set()
        for nama in peran:
            hak |= set(PERAN_LAMA.get(nama, []))
        if hak:
            HakAkun.objects.create(user=user, hak=sorted(hak))


def mundur(apps, schema_editor):
    apps.get_model("master", "HakAkun").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("master", "0013_lokasi"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(maju, mundur)]
