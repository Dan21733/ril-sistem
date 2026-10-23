"""Uji master_pindah_db: SQLite lama -> database tujuan, semua atau tidak sama sekali."""
import atexit
import copy
import shutil
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS, connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from master.layanan import inti as layanan
from master.layanan.legacy import buat_tabel_pos
from master.management.commands.master_pindah_db import pindahkan
from master.models import Supplier, VarianInfo
from pos.models import Item, TransactionDetail, Transactions, Worker

SUMBER = "uji_sumber_pindah"

# Berkas SQLite "lama" didaftarkan saat modul dimuat supaya test runner Django
# membuat dan me-migrate database uji untuknya, tepat di berkas ini. Database uji
# bawaan menjadi database tujuan.
_FOLDER = tempfile.mkdtemp(prefix="uji-pindah-")
atexit.register(shutil.rmtree, _FOLDER, ignore_errors=True)
BERKAS = Path(_FOLDER) / "lama.sqlite3"
connections.settings[SUMBER] = connections.configure_settings({
    DEFAULT_DB_ALIAS: copy.deepcopy(settings.DATABASES[DEFAULT_DB_ALIAS]),
    SUMBER: {"ENGINE": "django.db.backends.sqlite3", "NAME": str(BERKAS), "TEST": {"NAME": str(BERKAS)}},
})[SUMBER]

TABEL_POS = ["refund_event_detail", "refund_event", "payment_proof", "transaction_detail", "transactions",
             "item", "device_transaction_sequence", "device", "worker"]


def _buang_tabel_pos(koneksi):
    """Tabel POS tidak dikelola Django, jadi tidak ikut dibersihkan antar-uji."""
    with koneksi.cursor() as c:
        for tabel in TABEL_POS:
            c.execute(f"DROP TABLE IF EXISTS {tabel}")


class PindahDb(TransactionTestCase):
    databases = {DEFAULT_DB_ALIAS, SUMBER}

    def setUp(self):
        self.berkas = BERKAS
        buat_tabel_pos(connections[SUMBER])

        s = SUMBER
        for nama, varian, stok, modal in [("Semen Uji", "40kg", -3, 52000), ("Paku Uji", "N/A", 12, None)]:
            Item.objects.using(s).create(
                item_name=nama, variant=varian, category="Bahan", brand="ABC", sku=None, moka_id=None,
                min_price=55000, max_price=60000, cost_amount=modal, in_stock=stok, stock_alert=None,
                ecommerce_item=False, track_stock=True, track_alert=True)
        Worker.objects.using(s).create(id="ID901", name="Uji", pin="x")
        self.sync = uuid.uuid4()
        Transactions.objects.using(s).create(
            sync_id=self.sync, transaction_id="T09-000001", worker_id="ID901", transaction_time=timezone.now(),
            item_amount=60000, shipping_amount=0, total_amount=60000, payment_method="cash",
            payment_amount=60000, change_amount=0, cust_name="Pak Budi", phone_no=None)
        TransactionDetail.objects.using(s).create(sync_id=self.sync, item_name="Semen Uji", variant="40kg",
                                                  quantity=1, unit_price=60000, discount=0, subtotal=60000)
        get_user_model().objects.db_manager(s).create_user("pemilik", password="rahasia-123")
        Supplier.objects.using(s).create(nama="CV Uji")
        for item in Item.objects.using(s).all():
            VarianInfo.objects.using(s).create(item_name=item.item_name, variant=item.variant, modal_rata=51500)
        connections[SUMBER].close()

    def tearDown(self):
        _buang_tabel_pos(connections[SUMBER])
        _buang_tabel_pos(connection)
        connections[SUMBER].close()

    def test_semua_pindah_sama_persis(self):
        hasil, _, _ = pindahkan(self.berkas, asal=SUMBER)

        self.assertEqual(hasil["item"], 2)
        semen = Item.objects.get(item_name="Semen Uji", variant="40kg")
        self.assertEqual((semen.in_stock, semen.cost_amount, semen.track_alert), (-3, 52000, True))
        self.assertIsNone(Item.objects.get(item_name="Paku Uji").cost_amount)
        nota = Transactions.objects.get(sync_id=self.sync)
        self.assertEqual((nota.cust_name, nota.worker_id), ("Pak Budi", "ID901"))
        self.assertEqual(TransactionDetail.objects.get(sync_id=self.sync).subtotal, 60000)
        self.assertTrue(get_user_model().objects.get(username="pemilik").check_password("rahasia-123"))
        self.assertEqual(VarianInfo.objects.get(item_name="Semen Uji").modal_rata, 51500)
        # Penghitung id ikut disetel: data baru tidak bentrok dengan id lama.
        Supplier.objects.create(nama="CV Baru")
        layanan.sinkron_info()

    def test_tujuan_sudah_berisi_ditolak(self):
        pindahkan(self.berkas, asal=SUMBER)
        with self.assertRaisesMessage(CommandError, "sudah berisi"):
            pindahkan(self.berkas, asal=SUMBER)

    def test_gagal_di_tengah_tidak_menyisakan_apa_pun(self):
        # Kolom yang tidak dikenal kode tidak boleh hilang diam-diam: seluruh perpindahan batal.
        with connections[SUMBER].cursor() as c:
            c.execute("ALTER TABLE item ADD COLUMN kolom_liar integer")
        connections[SUMBER].close()

        with self.assertRaisesMessage(CommandError, "kolom_liar"):
            pindahkan(self.berkas, asal=SUMBER)

        self.assertFalse(Supplier.objects.exists())
        self.assertFalse(get_user_model().objects.filter(username="pemilik").exists())
        with connection.cursor() as c:
            if "item" in connection.introspection.table_names(c):
                self.assertFalse(Item.objects.exists())

    def test_versi_tabel_beda_ditolak(self):
        with connections[SUMBER].cursor() as c:
            c.execute("INSERT INTO django_migrations (app, name, applied) VALUES ('master', '9999_palsu', '2026-01-01')")
        connections[SUMBER].close()
        try:
            with self.assertRaisesMessage(CommandError, "tidak sama"):
                pindahkan(self.berkas, asal=SUMBER)
        finally:
            with connections[SUMBER].cursor() as c:
                c.execute("DELETE FROM django_migrations WHERE name = '9999_palsu'")
        self.assertFalse(Supplier.objects.exists())
