"""Pindahkan SELURUH isi database master dari berkas SQLite (PC Windows) ke database ini.

Beda dengan master_cadangan + master_pulihkan: cadangan hanya memuat data master dan
akun. Selama master memakai SQLite sendiri, tabel kasir yang diubah master (item: harga,
modal, stok; juga nota kalau ada) hanya ada di berkas SQLite itu. Perintah ini memindahkan
keduanya dalam satu transaksi: kalau ada satu saja yang gagal atau jumlah barisnya beda,
database tujuan dikembalikan kosong.

Urutan di server (database tujuan baru di-migrate, belum master_setup):
  python manage.py migrate --settings=master.settings
  python manage.py master_pindah_db /jalur/ke/db.sqlite3 --yakin --settings=master.settings
  python manage.py master_setup --settings=master.settings
Berkas foto (folder media) disalin terpisah; perintah ini hanya melaporkan yang belum ada.

Berkas SQLite hanya dibaca (mode=ro), tidak pernah diubah.

Sekali pakai: setelah perpindahan terverifikasi, berkas perintah ini dihapus dari repo
(POS/AGENTS.md §3, skrip migrasi data sekali-pakai).
"""
import copy
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connection, connections, transaction
from django.db.migrations.recorder import MigrationRecorder

from master.layanan.legacy import buat_tabel_pos
from pos.models import (Device, DeviceTransactionSequence, Item, ItemAliasPhrase, PaymentProof, RefundEvent,
                        RefundEventDetail, TransactionDetail, Transactions, Worker)

from ._cadangan import APLIKASI, muat_data, pastikan_kosong

ASAL = "asal_pindah"

# Urutan mengikuti foreign key: induk dulu.
TABEL_POS = [Worker, Device, DeviceTransactionSequence, Item, Transactions, TransactionDetail,
             PaymentProof, RefundEvent, RefundEventDetail, ItemAliasPhrase]

UKURAN_BATCH = 500


def daftarkan_asal(berkas):
    """Daftarkan berkas SQLite sebagai koneksi baca-saja bernama ASAL."""
    berkas = Path(berkas).resolve()
    if not berkas.is_file():
        raise CommandError(f"Berkas tidak ditemukan: {berkas}")
    if connection.vendor == "sqlite" and Path(str(settings.DATABASES[DEFAULT_DB_ALIAS]["NAME"])).resolve() == berkas:
        raise CommandError("Berkas asal sama dengan database tujuan.")
    isi = {
        DEFAULT_DB_ALIAS: copy.deepcopy(settings.DATABASES[DEFAULT_DB_ALIAS]),
        ASAL: {"ENGINE": "django.db.backends.sqlite3", "NAME": berkas.as_uri() + "?mode=ro"},
    }
    connections.settings[ASAL] = connections.configure_settings(isi)[ASAL]
    return berkas


def lepas_asal():
    if ASAL in connections:
        connections[ASAL].close()
    connections.settings.pop(ASAL, None)


def _kolom(alias, tabel):
    koneksi = connections[alias]
    with koneksi.cursor() as c:
        if tabel not in koneksi.introspection.table_names(c):
            return None
        return {k.name for k in koneksi.introspection.get_table_description(c, tabel)}


def _migrasi(alias):
    return {(app, nama) for app, nama in MigrationRecorder(connections[alias]).applied_migrations()
            if app in ("master", "auth")}


def _salin_tabel(model, asal):
    """Salin satu tabel POS dari koneksi asal ke tujuan. Kembalikan jumlah baris."""
    tabel = model._meta.db_table
    kolom_asal = _kolom(asal, tabel)
    if kolom_asal is None:
        return None
    kolom_tujuan = _kolom(DEFAULT_DB_ALIAS, tabel)
    jumlah = model.objects.using(asal).count()
    if kolom_tujuan is None:
        if jumlah:
            raise CommandError(f"Tabel {tabel} berisi {jumlah} baris di asal tetapi tidak ada di tujuan.")
        return 0

    field = [f for f in model._meta.concrete_fields if f.column in kolom_asal]
    dikenal = {f.column for f in model._meta.concrete_fields}
    asing = kolom_asal - dikenal
    if asing:
        # Kolom yang tidak dikenal model tidak bisa dipindah dengan aman: jangan hilangkan diam-diam.
        raise CommandError(f"Tabel {tabel} di asal punya kolom yang tidak dikenal kode: {', '.join(sorted(asing))}.")
    kurang = [f.column for f in field if f.column not in kolom_tujuan]
    if kurang:
        raise CommandError(f"Tabel {tabel} di tujuan tidak punya kolom: {', '.join(kurang)}.")

    sql = "INSERT INTO {} ({}) VALUES ({})".format(
        connection.ops.quote_name(tabel),
        ", ".join(connection.ops.quote_name(f.column) for f in field),
        ", ".join(["%s"] * len(field)),
    )
    baris = model.objects.using(asal).order_by().values_list(*[f.attname for f in field])
    with connection.cursor() as c:
        kumpulan = []
        for isi in baris.iterator(chunk_size=UKURAN_BATCH):
            kumpulan.append([f.get_db_prep_save(v, connection) for f, v in zip(field, isi)])
            if len(kumpulan) >= UKURAN_BATCH:
                c.executemany(sql, kumpulan)
                kumpulan = []
        if kumpulan:
            c.executemany(sql, kumpulan)
    return jumlah


def _model_master():
    from django.apps import apps
    return [*apps.get_app_config("master").get_models(), User, Group]


def pindahkan(berkas, asal=None):
    """Isi database tujuan dari berkas SQLite. Semua atau tidak sama sekali.

    `asal`: nama koneksi yang sudah terdaftar untuk berkas itu (dipakai uji, karena
    test runner Django melarang koneksi baru). Bawaan: berkas dibuka baca-saja di sini.
    """
    if asal is None:
        daftarkan_asal(berkas)
    try:
        return _pindahkan(asal or ASAL)
    finally:
        if asal is None:
            lepas_asal()


def _pindahkan(asal):
    if _migrasi(asal) != _migrasi(DEFAULT_DB_ALIAS):
        lebih = sorted(_migrasi(asal) - _migrasi(DEFAULT_DB_ALIAS))
        kurang = sorted(_migrasi(DEFAULT_DB_ALIAS) - _migrasi(asal))
        raise CommandError(
            "Versi tabel master di berkas asal dan di database ini tidak sama.\n"
            f"  hanya di asal: {lebih or '-'}\n  hanya di tujuan: {kurang or '-'}\n"
            "Samakan versi kode di kedua tempat lalu jalankan migrate di keduanya.")
    try:
        pastikan_kosong()
    except ValueError as galat:
        raise CommandError(str(galat)) from galat
    if _kolom(DEFAULT_DB_ALIAS, "item") is not None and Item.objects.exists():
        raise CommandError("Tabel item di database tujuan sudah berisi. Pindahkan hanya ke database baru.")

    hasil = {}
    with transaction.atomic():
        buat_tabel_pos()
        for model in TABEL_POS:
            n = _salin_tabel(model, asal)
            if n is not None:
                hasil[model._meta.db_table] = n

        with tempfile.TemporaryDirectory() as folder:
            berkas_json = Path(folder) / "master.json"
            call_command("dumpdata", *APLIKASI, database=asal, natural_foreign=True, natural_primary=True,
                         output=str(berkas_json), verbosity=0)
            muat_data(berkas_json)

        # Periksa ulang: jumlah baris tiap tabel harus sama persis dengan asal.
        beda = []
        for model in TABEL_POS:
            tabel = model._meta.db_table
            if tabel in hasil and model.objects.count() != hasil[tabel]:
                beda.append(f"{tabel}: asal {hasil[tabel]}, tujuan {model.objects.count()}")
        for model in _model_master():
            a, t = model.objects.using(asal).count(), model.objects.count()
            hasil[model._meta.db_table] = a
            if a != t:
                beda.append(f"{model._meta.db_table}: asal {a}, tujuan {t}")
        if beda:
            raise CommandError("Jumlah baris tidak sama, tidak ada yang disimpan:\n  " + "\n  ".join(beda))

    from master.models import FotoFaktur, FotoNota
    kunci = [*FotoFaktur.objects.values_list("object_key", flat=True),
             *FotoNota.objects.values_list("object_key", flat=True),
             *PaymentProof.objects.values_list("object_key", flat=True)]
    hilang = [k for k in kunci if not default_storage.exists(k)]
    return hasil, len(kunci), hilang


class Command(BaseCommand):
    help = "Pindahkan seluruh database master (data master + tabel kasir) dari berkas SQLite ke database ini."

    def add_arguments(self, parser):
        parser.add_argument("berkas", help="Berkas db.sqlite3 dari PC master lama.")
        parser.add_argument("--yakin", action="store_true")

    def handle(self, *args, **o):
        if not o["yakin"]:
            raise CommandError("Tambahkan --yakin untuk benar-benar memindahkan.")
        hasil, foto, hilang = pindahkan(o["berkas"])
        terisi = {k: v for k, v in sorted(hasil.items()) if v}
        self.stdout.write(self.style.SUCCESS(f"Dipindahkan ke {connection.vendor}. Baris per tabel:"))
        for tabel, n in terisi.items():
            self.stdout.write(f"  {tabel}: {n}")
        if hilang:
            self.stdout.write(self.style.WARNING(
                f"{len(hilang)} dari {foto} berkas foto belum ada di {settings.MEDIA_ROOT}. "
                "Salin folder media dari PC lama; perintah ini tidak perlu diulang."))
        self.stdout.write("Lanjutkan dengan: python manage.py master_setup --settings=master.settings")
