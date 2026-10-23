"""Cadangan data master: satu berkas .zip berisi data (JSON) dan foto faktur.

Yang dicadangkan hanya data milik master dan akun login. Tabel kasir (item,
nota, dll.) tidak ikut: itu milik POS dan dicadangkan di sisi POS.

Format JSON dipilih supaya cadangan dari SQLite bisa dipulihkan ke PostgreSQL
(dan sebaliknya) - jalur yang sama dipakai saat master pindah database.
"""
import io
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder

APLIKASI = ["auth.group", "auth.user", "master"]
AWALAN = "master-cadangan-"


def buat(folder, simpan=30):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data = io.StringIO()
    call_command("dumpdata", *APLIKASI, natural_foreign=True, natural_primary=True, stdout=data)
    isi = json.loads(data.getvalue())
    from master.models import FotoFaktur, FotoNota
    foto = list(FotoFaktur.objects.values_list("object_key", flat=True)) + list(FotoNota.objects.values_list("object_key", flat=True))
    hitung = {}
    for baris in isi:
        hitung[baris["model"]] = hitung.get(baris["model"], 0) + 1
    migrasi = list(MigrationRecorder.Migration.objects.filter(app="master").order_by("id").values_list("name", flat=True))
    info = {"waktu": datetime.now().isoformat(timespec="seconds"), "database": connection.vendor,
            "migrasi_terakhir": migrasi[-1] if migrasi else None, "jumlah": hitung, "foto": len(foto)}

    nama = f"{AWALAN}{datetime.now():%Y%m%d-%H%M%S}.zip"
    sementara = folder / (nama + ".sedang-ditulis")
    hilang = []
    with zipfile.ZipFile(sementara, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info, indent=2, ensure_ascii=False))
        z.writestr("data.json", data.getvalue())
        for kunci in foto:
            if default_storage.exists(kunci):
                with default_storage.open(kunci, "rb") as f:
                    z.writestr("media/" + kunci, f.read())
            else:
                hilang.append(kunci)
    os.replace(sementara, folder / nama)

    lama = sorted(folder.glob(f"{AWALAN}*.zip"))
    for p in lama[:-simpan] if simpan > 0 else []:
        p.unlink()
    return folder / nama, info, hilang


def pastikan_kosong():
    """Tolak kalau database tujuan sudah berisi data master."""
    from master import models as m
    terisi = [model.__name__ for model in (m.Supplier, m.Pembelian, m.FakturManual, m.Log, m.MutasiStok, m.Opname,
                                           m.VarianInfo, m.ItemInfo, m.Rak, m.Konversi, m.Transfer, m.NotaManual) if model.objects.exists()]
    if terisi:
        raise ValueError("Database tujuan sudah berisi data master (" + ", ".join(terisi) + "). "
                         "Pulihkan hanya ke database yang baru di-migrate, sebelum master_setup.")


def muat_data(berkas_json):
    """Isi data master + akun dari berkas JSON hasil dumpdata ke database yang baru di-migrate."""
    from master import models as m
    with transaction.atomic():
        # Pengaturan (id=1) bisa sudah dibuat otomatis; data cadangan yang menang.
        m.Pengaturan.objects.all().delete()
        # Toko pertama dibuat otomatis oleh migrasi; isi cadangan yang dipakai.
        m.Toko.objects.all().delete()
        call_command("loaddata", str(berkas_json), verbosity=0)
        # PostgreSQL: loaddata menulis id apa adanya, jadi penghitung id harus disetel ulang.
        from django.apps import apps
        from django.contrib.auth.models import Group, User
        sql = connection.ops.sequence_reset_sql(no_style(), [*apps.get_app_config("master").get_models(), User, Group])
        with connection.cursor() as c:
            for perintah in sql:
                c.execute(perintah)


def pulihkan(berkas):
    pastikan_kosong()
    with zipfile.ZipFile(berkas) as z:
        info = json.loads(z.read("info.json"))
        data = z.read("data.json")
        media = [n for n in z.namelist() if n.startswith("media/")]
        sementara = Path(berkas).with_suffix(".pulihkan.json")
        sementara.write_bytes(data)
        try:
            muat_data(sementara)
        finally:
            sementara.unlink(missing_ok=True)
        for nama in media:
            kunci = nama[len("media/"):]
            if not default_storage.exists(kunci):
                default_storage.save(kunci, ContentFile(z.read(nama)))
    return info
