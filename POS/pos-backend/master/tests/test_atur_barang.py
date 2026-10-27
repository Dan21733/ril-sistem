"""Uji: atur ulang barang — pindah, pisah, gabung varian; aktif massal; harga boleh menyusul."""
import json
import uuid

from django.urls import reverse
from django.utils import timezone

from pos.models import Item, MutasiKasir, TransactionDetail, Transactions

from master.akun.akses import SEMUA_HAK
from master.layanan import atur_barang
from master.layanan import inti as layanan
from master.layanan.inti import Tolak
from master.models import ItemInfo, Log, VarianInfo
from master.tests.dasar import Dasar, buat_item


class AturBarangTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for v, harga in (("Adana Grey", 78000), ("Alica Grey", 79000), ("Alice Grey", 75000)):
            buat_item("Keramik 50 X 50", v, floor=harga - 2000, jual=harga, modal=60000, kategori="Keramik", brand="Avigon", stok=3)
        layanan.sinkron_info()
        layanan.ubah_alias_item(cls.admin, ItemInfo.objects.get(item_name="Keramik 50 X 50"), "kramik", hak=SEMUA_HAK)

    def v(self, nama, varian):
        return VarianInfo.objects.get(item_name=nama, variant=varian)

    def api(self, **data):
        return self.client.post(reverse("master:barang_atur"), json.dumps(data), content_type="application/json")

    def test_pisah_jadi_barang_sendiri(self):
        adana, alica = self.v("Keramik 50 X 50", "Adana Grey"), self.v("Keramik 50 X 50", "Alica Grey")
        rencana = atur_barang.periksa(atur_barang.susun("pisah", varian_ids=[adana.pk, alica.pk]))
        self.assertEqual([(r["item"], r["varian"]) for r in rencana],
                         [("Keramik 50 X 50 Adana Grey", "N/A"), ("Keramik 50 X 50 Alica Grey", "N/A")])
        self.assertFalse(any(r["masalah"] for r in rencana))
        n, _ = atur_barang.terapkan(self.admin, rencana, hak=SEMUA_HAK)
        self.assertEqual(n, 2)
        i = Item.objects.get(item_name="Keramik 50 X 50 Adana Grey", variant="N/A")
        self.assertEqual((i.max_price, i.in_stock, i.brand), (78000, 3, "Avigon"))
        adana.refresh_from_db()
        self.assertEqual((adana.item_name, adana.variant), ("Keramik 50 X 50 Adana Grey", "N/A"))   # ID tetap
        self.assertEqual(ItemInfo.objects.get(item_name="Keramik 50 X 50 Adana Grey").alias, "kramik")  # alias ikut
        self.assertTrue(Item.objects.filter(item_name="Keramik 50 X 50", variant="Alice Grey").exists())
        self.assertTrue(Log.objects.filter(kolom="atur ulang").exists())

    def test_gabung_lagi_per_brand_dan_konflik_terdeteksi(self):
        ids = [self.v("Keramik 50 X 50", v).pk for v in ("Adana Grey", "Alica Grey")]
        atur_barang.terapkan(self.admin, atur_barang.susun("pisah", varian_ids=ids), hak=SEMUA_HAK)
        item_ids = list(ItemInfo.objects.filter(item_name__startswith="Keramik 50 X 50 A").values_list("pk", flat=True))
        rencana = atur_barang.periksa(atur_barang.susun("gabung", item_ids=item_ids, tujuan="Keramik 50 X 50 Avigon"))
        self.assertEqual(sorted((r["item"], r["varian"]) for r in rencana),
                         [("Keramik 50 X 50 Avigon", "Adana Grey"), ("Keramik 50 X 50 Avigon", "Alica Grey")])
        # varian kembar ditandai sebelum diterapkan
        kembar = [dict(r, varian="Sama") for r in rencana]
        self.assertTrue(any(r["masalah"] for r in atur_barang.periksa(kembar)))
        with self.assertRaises(Tolak):
            atur_barang.terapkan(self.admin, kembar, hak=SEMUA_HAK)
        atur_barang.terapkan(self.admin, rencana, hak=SEMUA_HAK)
        self.assertEqual(set(Item.objects.filter(item_name="Keramik 50 X 50 Avigon").values_list("variant", flat=True)),
                         {"Adana Grey", "Alica Grey"})

    def test_pindah_ke_barang_yang_ada_dan_bentrok(self):
        buat_item("Keramik 50 X 50 Roman", "Alice Grey", kategori="Keramik")
        layanan.sinkron_info()
        alice, adana = self.v("Keramik 50 X 50", "Alice Grey"), self.v("Keramik 50 X 50", "Adana Grey")
        rencana = atur_barang.periksa(atur_barang.susun("pindah", varian_ids=[alice.pk, adana.pk], tujuan="keramik 50 x 50 roman"))
        self.assertEqual(rencana[0]["item"], "Keramik 50 X 50 Roman")                      # ikut penulisan yang ada
        self.assertTrue(rencana[0]["masalah"])                                             # Alice Grey sudah ada
        self.assertIn("Masuk ke barang yang sudah ada", rencana[1]["catatan"][0])

    def test_nota_retur_dan_mutasi_kasir_ikut_pindah(self):
        sid = uuid.uuid4()
        Transactions.objects.create(sync_id=sid, transaction_id="T01-000777", worker_id="ID001", transaction_time=timezone.now(),
                                    item_amount=78000, shipping_amount=0, total_amount=78000, payment_method="cash",
                                    payment_amount=78000, change_amount=0)
        TransactionDetail.objects.create(sync_id=sid, item_name="Keramik 50 X 50", variant="Adana Grey", quantity=1,
                                         unit_price=78000, discount=0, subtotal=78000)
        MutasiKasir.objects.create(sync_id=sid, item_name="Keramik 50 X 50", variant="Adana Grey", jumlah=-1,
                                   stok_sebelum=4, stok_sesudah=3)
        rencana = atur_barang.periksa(atur_barang.susun("pisah", varian_ids=[self.v("Keramik 50 X 50", "Adana Grey").pk]))
        self.assertEqual(rencana[0]["nota"], 1)
        atur_barang.terapkan(self.admin, rencana, hak=SEMUA_HAK)
        self.assertEqual(TransactionDetail.objects.get(sync_id=sid).item_name, "Keramik 50 X 50 Adana Grey")
        self.assertEqual(MutasiKasir.objects.get(sync_id=sid).item_name, "Keramik 50 X 50 Adana Grey")

    def test_lewat_layar_dan_hak(self):
        ids = [self.v("Keramik 50 X 50", "Adana Grey").pk]
        self.client.force_login(self.finance)
        r = self.api(aksi="susun", jenis="pisah", varian=ids)
        self.assertEqual(r.status_code, 400)
        self.client.force_login(self.admin)
        d = self.api(aksi="susun", jenis="pisah", varian=ids).json()
        self.assertTrue(d["ok"])
        d = self.api(aksi="terapkan", baris=d["baris"]).json()
        self.assertTrue(d["ok"])
        self.assertEqual(ItemInfo.objects.get(pk=d["buka"]).item_name, "Keramik 50 X 50 Adana Grey")
        d = self.api(aksi="nonaktifkan", varian=[self.v("Keramik 50 X 50", "Alice Grey").pk]).json()
        self.assertEqual(d["pesan"], "1 varian dinonaktifkan.")
        isi = self.client.get(reverse("master:barang") + f"item/{ItemInfo.objects.get(item_name='Keramik 50 X 50').pk}/panel/").content.decode()
        self.assertIn("Nonaktif (1)", isi)
        self.assertLess(isi.index("Alica Grey"), isi.index("Nonaktif (1)"))
        self.assertGreater(isi.index("Alice Grey"), isi.index("Nonaktif (1)"))

    def test_harga_boleh_menyusul(self):
        layanan.tambah_varian(self.admin, hak=SEMUA_HAK, item_name="Keramik 50 X 50", variant="Baru", category="Keramik",
                              min_price="", max_price="")
        i = Item.objects.get(item_name="Keramik 50 X 50", variant="Baru")
        self.assertEqual((i.min_price, i.max_price), (None, None))
        layanan.tambah_barang(self.admin, hak=SEMUA_HAK, item_name="Granit Baru", category="Granit",
                              varian=[{"variant": "60x60", "min_price": "", "max_price": ""}])
        self.assertTrue(Item.objects.filter(item_name="Granit Baru", max_price__isnull=True).exists())
        with self.assertRaises(Tolak):
            layanan.tambah_varian(self.admin, hak=SEMUA_HAK, item_name="Keramik 50 X 50", variant="Salah", category="Keramik",
                                  min_price="9000", max_price="5000")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:barang") + "?masalah=harga_kosong")
        self.assertEqual(r.status_code, 200)
