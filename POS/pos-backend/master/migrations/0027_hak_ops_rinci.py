"""Hak aplikasi operasional dipecah per hak (23 Okt 2026).

- ops_tiket  -> ops_tiket_pindah + ops_tiket_rak
- ops_barang -> ops_barang_harga + ops_barang_alias + ops_barang_aktif
- AkunKaryawan.peran (dulu nama paket) -> daftar hak rinci
Karyawan yang sudah punya akses tetap bisa semua yang bisa ia kerjakan sebelumnya.
"""
from django.db import migrations

PECAH = {
    "ops_tiket": ["ops_tiket_pindah", "ops_tiket_rak"],
    "ops_barang": ["ops_barang_harga", "ops_barang_alias", "ops_barang_aktif"],
}
PAKET_LAMA = {
    "kepala_gudang": ["gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil"],
    "admin_gudang": ["ops_tiket_pindah", "ops_tiket_rak", "ops_lihat_stok"],
    "admin_barang": ["ops_barang_harga", "ops_barang_alias", "ops_barang_aktif"],
}


def maju(apps, schema_editor):
    HakAkun = apps.get_model("master", "HakAkun")
    for h in HakAkun.objects.all():
        baru = []
        for x in h.hak or []:
            baru.extend(PECAH.get(x, [x]))
        baru = sorted(set(baru))
        if baru != sorted(set(h.hak or [])):
            h.hak = baru
            h.save(update_fields=["hak"])
    AkunKaryawan = apps.get_model("master", "AkunKaryawan")
    for a in AkunKaryawan.objects.all():
        hak = []
        for p in a.peran or []:
            hak.extend(PAKET_LAMA.get(p, PECAH.get(p, [p])))
        a.peran = sorted(set(hak))
        a.save(update_fields=["peran"])


class Migration(migrations.Migration):
    dependencies = [("master", "0026_ops_pin_tiket_barang")]
    operations = [migrations.RunPython(maju, migrations.RunPython.noop)]
