"""Gabung kategori/brand yang dobel (permintaan pemilik 6 Nov 2026)."""
from django.urls import reverse

from pos.models import Item

from master.layanan import inti as layanan
from master.models import Log
from master.tests.dasar import Dasar, buat_item


class GabungKelompokTest(Dasar):
    def setUp(self):
        self.client.force_login(self.admin)
        buat_item("Engsel", "3 inch", kategori="Aksesoris Pintu")
        buat_item("Engsel", "4 inch", kategori="aksesoris  pintu")
        buat_item("Gembok", kategori="Alat Berat & Angkut")
        buat_item("Troli", kategori="alat berat dan angkut")
        layanan.sinkron_info()

    def test_kunci_kembar(self):
        self.assertEqual(layanan.kunci_kembar("Aksesoris  Pintu"), layanan.kunci_kembar("aksesoris-pintu"))
        self.assertEqual(layanan.kunci_kembar("Alat Berat & Angkut"), layanan.kunci_kembar("alat berat dan angkut"))
        self.assertNotEqual(layanan.kunci_kembar("Alat Cat"), layanan.kunci_kembar("Alat Tukang"))

    def test_halaman_menampilkan_kemungkinan_dobel(self):
        isi = self.client.get(reverse("master:kategori")).content.decode()
        self.assertIn("Kemungkinan dobel (2)", isi)

    def test_gabung_beberapa_ke_satu(self):
        r = self.client.post(reverse("master:kategori"), {"aksi": "gabung", "lama": ["Aksesoris Pintu", "aksesoris  pintu"],
                                                          "baru": "Aksesoris Pintu"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(set(Item.objects.filter(item_name="Engsel").values_list("category", flat=True)), {"Aksesoris Pintu"})
        self.assertTrue(Log.objects.filter(aksi="ganti_nama", objek="kategori", lama="aksesoris  pintu",
                                           baru="Aksesoris Pintu").exists())
        isi = self.client.get(reverse("master:kategori")).content.decode()
        self.assertIn("Kemungkinan dobel (1)", isi)

    def test_gabung_brand_dan_tolak_tanpa_tujuan(self):
        buat_item("Kunci", brand="Dekson")
        r = self.client.post(reverse("master:brand"), {"aksi": "gabung", "lama": ["Dekson"], "baru": "ABC"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Item.objects.filter(brand="Dekson").exists())
        self.client.post(reverse("master:brand"), {"aksi": "gabung", "lama": ["ABC"], "baru": ""})
        self.assertTrue(Item.objects.filter(brand="ABC").exists())

    def test_tanpa_hak_ditolak(self):
        self.client.force_login(self.gudang)
        self.client.post(reverse("master:kategori"), {"aksi": "gabung", "lama": ["Aksesoris Pintu"], "baru": "Lain"})
        self.assertTrue(Item.objects.filter(category="Aksesoris Pintu").exists())
