"""Pembantu bersama untuk uji master: data awal, akun uji, dan pengaturan uji."""
import uuid

from django.conf import settings as _settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from pos.models import Item, TransactionDetail, Transactions, Worker

from master.layanan import inti as layanan
from master.akun.akses import PERAN
from master.layanan.legacy import buat_tabel_pos
from master.models import Rak, VarianInfo


def buat_item(nama, varian="N/A", *, floor=1000, jual=1500, modal=800, kategori="Paku", brand="ABC", stok=10):
    return Item.objects.create(
        item_name=nama, variant=varian, category=kategori, brand=brand, sku=None, moka_id=None,
        min_price=floor, max_price=jual, cost_amount=modal, in_stock=stok, stock_alert=None,
        ecommerce_item=False, track_stock=True, track_alert=False,
    )


# Gerbang perangkat aplikasi operasional diuji tersendiri (test_ops.py); uji lain
# memakai klien uji tanpa cookie perangkat.
@override_settings(OPS_WAJIB_PERANGKAT=False)
class Dasar(TestCase):
    @classmethod
    def setUpTestData(cls):
        buat_tabel_pos()
        for nama in PERAN:
            Group.objects.get_or_create(name=nama)
        Pengguna = get_user_model()
        cls.admin = Pengguna.objects.create_user("admin", password="rahasia-123")
        cls.admin.groups.add(Group.objects.get(name="Super Admin"))
        cls.finance = Pengguna.objects.create_user("finance", password="rahasia-123")
        cls.finance.groups.add(Group.objects.get(name="Finance"))
        cls.gudang = Pengguna.objects.create_user("gudang", password="rahasia-123")
        cls.gudang.groups.add(Group.objects.get(name="Gudang"))
        Rak.objects.create(kode="TK-01", nama="Rak Hardware")
        buat_item("Palu", "Kecil")
        buat_item("Palu", "Besar", floor=3000, jual=2500, modal=2000)   # floor terbalik
        buat_item("Kikir", floor=0, jual=0, modal=None)                 # harga nol, modal kosong
        buat_item("Oracle", "PD 135", floor=1500000, jual=135000, modal=700000)
        layanan.sinkron_info()
        Worker.objects.create(id="ID001", name="Rusdi", pin="x")
        cls.nota = Transactions.objects.create(
            sync_id=uuid.uuid4(), transaction_id="T01-000001", worker_id="ID001",
            transaction_time=timezone.now(), item_amount=3000, shipping_amount=0, total_amount=3000,
            payment_method="cash", payment_amount=3000, change_amount=0,
        )
        TransactionDetail.objects.create(sync_id=cls.nota.sync_id, item_name="Palu", variant="Kecil",
                                         quantity=2, unit_price=1500, discount=0, subtotal=3000)

    def info(self, nama, varian="N/A"):
        return VarianInfo.objects.get(item_name=nama, variant=varian)


def hasil_batch(impor):
    impor.refresh_from_db()
    return impor.hasil["batch"]


PENYIMPANAN_UJI = {**_settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}}

LOKAL = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "uji-masuk"}}
