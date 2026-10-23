"""Uji sambungan master <-> kasir: alias, barang nonaktif, rak, urutan, riwayat stok kasir."""
import datetime
import uuid

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from pos import data_master
from pos import pengaturan as aturan
from pos.models import Item, ItemAliasPhrase, MutasiKasir
from pos.transaction_service import persist_transaction

from master.layanan import alias_kasir
from master.layanan import inti as layanan
from master.models import ItemInfo, VarianInfo
from master.tests.dasar import Dasar, buat_item


def daftar_kasir(**param):
    from pos.device_authentication import AuthenticatedDevice
    from pos.views import ItemListAPIView

    req = APIRequestFactory().get("/api/items/", param)
    force_authenticate(req, user=AuthenticatedDevice(device=object()))
    return ItemListAPIView.as_view()(req).data


class AliasKasirTest(Dasar):
    def setUp(self):
        aturan.lupakan_cache()

    def test_ubah_alias_di_master_ikut_ke_kasir(self):
        info = ItemInfo.objects.get(item_name="Palu")
        layanan.ubah_alias_item(self.admin, info, "martil, Penokok", hak={"ubah_info"})
        self.assertEqual(set(ItemAliasPhrase.objects.filter(item_name="Palu").values_list("frasa", flat=True)),
                         {"martil", "penokok"})
        layanan.ubah_varian(self.admin, self.info("Palu", "Besar"), "alias_tambahan", "godam", hak={"ubah_info"})
        self.assertIn("godam", ItemAliasPhrase.objects.filter(item_name="Palu").values_list("frasa", flat=True))
        layanan.ubah_alias_item(self.admin, info, "", hak={"ubah_info"})
        self.assertEqual(list(ItemAliasPhrase.objects.filter(item_name="Palu").values_list("frasa", flat=True)),
                         ["godam"])

    def test_alias_lama_kasir_digabung_sekali_lalu_master_yang_menentukan(self):
        ItemAliasPhrase.objects.create(frasa="martil", item_name="Palu")
        ItemAliasPhrase.objects.create(frasa="zink", item_name="Barang Tak Dikenal")
        hasil = alias_kasir.gabung_sekali()
        self.assertEqual(hasil["digabung"], 1)
        self.assertIn("martil", ItemInfo.objects.get(item_name="Palu").alias)
        # Alias untuk nama yang tidak ada di master tidak dihapus.
        self.assertTrue(ItemAliasPhrase.objects.filter(item_name="Barang Tak Dikenal").exists())
        # Kedua kali tidak menggabung lagi: alias yang dihapus di master tidak kembali.
        info = ItemInfo.objects.get(item_name="Palu")
        layanan.ubah_alias_item(self.admin, info, "", hak={"ubah_info"})
        self.assertEqual(alias_kasir.gabung_sekali()["digabung"], 0)
        self.assertEqual(ItemInfo.objects.get(item_name="Palu").alias, "")

    def test_ganti_nama_memindahkan_alias(self):
        info = ItemInfo.objects.get(item_name="Kikir")
        layanan.ubah_alias_item(self.admin, info, "parut", hak={"ubah_info"})
        layanan.ganti_nama_item(self.admin, info, "Kikir Besi", hak={"ganti_nama"})
        self.assertFalse(ItemAliasPhrase.objects.filter(item_name="Kikir").exists())
        self.assertTrue(ItemAliasPhrase.objects.filter(item_name="Kikir Besi", frasa="parut").exists())


class DaftarKasirTest(Dasar):
    def setUp(self):
        data_master.lupakan_cache()

    def test_nonaktif_disembunyikan_dan_rak_ikut(self):
        VarianInfo.objects.filter(item_name="Palu", variant="Besar").update(aktif=False)
        VarianInfo.objects.filter(item_name="Palu", variant="Kecil").update(rak_id="TK-01")
        hasil = {(r["item_name"], r["variant"]): r for r in daftar_kasir()["results"]}
        self.assertNotIn(("Palu", "Besar"), hasil)
        self.assertEqual(hasil[("Palu", "Kecil")]["rak"], "TK-01")
        self.assertIsNone(hasil[("Kikir", "N/A")]["rak"])

    def test_alias_ikut_terkirim_untuk_pencarian_offline(self):
        layanan.ubah_alias_item(self.admin, ItemInfo.objects.get(item_name="Palu"), "martil, penokok", hak={"ubah_info"})
        hasil = {(r["item_name"], r["variant"]): r for r in daftar_kasir(page_size=200)["results"]}
        self.assertEqual(set(hasil[("Palu", "Kecil")]["alias"].split()), {"martil", "penokok"})
        self.assertEqual(hasil[("Kikir", "N/A")]["alias"], "")

    def test_urutan_varian_alami(self):
        for v in ("10 MM", "2 MM", "8 MM"):
            buat_item("Mata Bor", v)
        varian = [r["variant"] for r in daftar_kasir(page_size=200)["results"] if r["item_name"] == "Mata Bor"]
        self.assertEqual(varian, ["2 MM", "8 MM", "10 MM"])


class RiwayatStokKasirTest(Dasar):
    def setUp(self):
        self.client.force_login(self.admin)

    def _jual(self, jumlah):
        sid = uuid.uuid4()
        persist_transaction({
            "sync_id": sid, "transaction_id": "T01-000099", "transaction_time": timezone.now(),
            "item_amount": 1500 * jumlah, "shipping_amount": 0, "total_amount": 1500 * jumlah,
            "payment_method": "cash", "payment_amount": 1500 * jumlah, "change_amount": 0,
            "items": [{"item_name": "Palu", "variant": "Kecil", "quantity": jumlah, "unit_price": 1500, "discount": 0}],
        }, "ID001", "T01")
        return sid

    def test_penjualan_kasir_tercatat_dengan_stok_sebelum_sesudah(self):
        stok = Item.objects.get(item_name="Palu", variant="Kecil").in_stock
        self._jual(3)
        m = MutasiKasir.objects.get()
        self.assertEqual((m.jumlah, m.stok_sebelum, m.stok_sesudah, m.worker_id), (-3, stok, stok - 3, "ID001"))

    def test_riwayat_menggabungkan_master_dan_kasir(self):
        self._jual(2)
        layanan.sesuaikan_stok(self.admin, self.info("Palu", "Kecil"), cara="tetapkan", angka="50", jenis="hitung",
                               catatan="catatan-unik-opname", hak={"ubah_stok"})
        isi = self.client.get(reverse("master:stok_riwayat")).content.decode()
        self.assertIn("Penjualan kasir", isi)
        self.assertIn("Nota T01-000099", isi)
        self.assertIn("Rusdi", isi)
        hanya = self.client.get(reverse("master:stok_riwayat"), {"jenis": "jual_kasir"}).content.decode()
        self.assertIn("Penjualan kasir", hanya)
        self.assertNotIn("catatan-unik-opname", hanya)
        self.assertIn("catatan-unik-opname", isi)
        cari = self.client.get(reverse("master:stok_riwayat"), {"q": "T01-000099"}).content.decode()
        self.assertIn("Nota T01-000099", cari)
