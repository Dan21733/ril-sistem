import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

# Kategori awal = kategori uang keluar yang sudah dipakai tablet, supaya catatan lama tetap cocok.
# Selebihnya diisi sendiri oleh pemilik di master.
AWAL = [
    ("Diambil pemilik", False),
    ("Belanja keperluan toko", True),
    ("Ongkir / bensin", True),
    ("Makan & minum", True),
    ("Bayar supplier / barang", False),
    ("Lainnya", True),
]


def isi(apps, schema_editor):
    K = apps.get_model("master", "KategoriBiaya")
    for i, (nama, usaha) in enumerate(AWAL):
        K.objects.get_or_create(nama=nama, defaults={"biaya_usaha": usaha, "urutan": (i + 1) * 10})
    # Uang keluar laci yang tercatat sebelum fitur ini ada: masuk sebagai biaya yang sudah disetujui,
    # supaya daftar ACC tidak dibanjiri catatan lama. Catatan sesudahnya ditarik oleh layanan biaya.
    tabel = schema_editor.connection.introspection.table_names()
    if "kas_gerak" not in tabel:
        return
    Biaya = apps.get_model("master", "Biaya")
    Toko = apps.get_model("master", "Toko")
    toko_ada = set(Toko.objects.values_list("pk", flat=True))
    from django.utils import timezone
    with schema_editor.connection.cursor() as c:
        c.execute("SELECT gerak_id, toko_id, jumlah, kategori, keterangan, worker_id, oleh, waktu FROM kas_gerak "
                  "WHERE jenis = 'keluar' AND NOT batal")
        baris = c.fetchall()
    from django.utils.dateparse import parse_datetime
    baris = [(g, t, j, k, ket, wid, oleh, parse_datetime(w) if isinstance(w, str) else w)
             for g, t, j, k, ket, wid, oleh, w in baris]
    Biaya.objects.bulk_create([
        Biaya(gerak_id=g, toko_id=t if t in toko_ada else None, tanggal=timezone.localdate(w), kategori=k or "Lainnya",
              jumlah=j, sumber="laci", keterangan=ket or "", worker_id=wid or "", dicatat_oleh=oleh or "",
              dicatat_waktu=w, status="disetujui", acc_oleh="otomatis (catatan sebelum fitur biaya)", acc_waktu=w)
        for g, t, j, k, ket, wid, oleh, w in baris
    ], ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("master", "0043_supplier_alias"),
    ]

    operations = [
        migrations.CreateModel(
            name="KategoriBiaya",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nama", models.CharField(max_length=40, unique=True)),
                ("biaya_usaha", models.BooleanField(default=True)),
                ("aktif", models.BooleanField(default=True)),
                ("urutan", models.IntegerField(default=0)),
            ],
            options={"db_table": "master_kategori_biaya", "ordering": ["urutan", "nama"]},
        ),
        migrations.CreateModel(
            name="Biaya",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tanggal", models.DateField(db_index=True)),
                ("kategori", models.CharField(max_length=40)),
                ("jumlah", models.BigIntegerField()),
                ("sumber", models.CharField(choices=[("laci", "Laci kasir"), ("luar_laci", "Tunai luar laci")], default="luar_laci", max_length=20)),
                ("keterangan", models.CharField(blank=True, default="", max_length=300)),
                ("status", models.CharField(choices=[("menunggu", "Menunggu ACC"), ("disetujui", "Disetujui"), ("ditolak", "Ditolak"), ("batal", "Dibatalkan")], db_index=True, default="menunggu", max_length=10)),
                ("gerak_id", models.UUIDField(blank=True, null=True, unique=True)),
                ("dicatat_oleh", models.CharField(blank=True, default="", max_length=150)),
                ("worker_id", models.CharField(blank=True, default="", max_length=5)),
                ("dicatat_waktu", models.DateTimeField(default=django.utils.timezone.now)),
                ("acc_oleh", models.CharField(blank=True, default="", max_length=150)),
                ("acc_waktu", models.DateTimeField(blank=True, null=True)),
                ("alasan", models.CharField(blank=True, default="", max_length=300)),
                ("diganti", models.BooleanField(default=False)),
                ("diganti_waktu", models.DateTimeField(blank=True, null=True)),
                ("diganti_oleh", models.CharField(blank=True, default="", max_length=150)),
                ("foto_key", models.CharField(blank=True, default="", max_length=300)),
                ("foto_type", models.CharField(blank=True, default="", max_length=60)),
                ("toko", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="biaya", to="master.toko")),
            ],
            options={"db_table": "master_biaya", "ordering": ["-tanggal", "-dicatat_waktu"]},
        ),
        migrations.RunPython(isi, migrations.RunPython.noop),
    ]
