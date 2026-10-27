"""Uji: urutan pencarian barang, nama dulu baru alias (27 Okt 2026)."""
from django.urls import reverse

from master.akun.akses import SEMUA_HAK
from master.layanan import inti as layanan
from master.models import ItemInfo
from master.tests.dasar import Dasar, buat_item


class UrutanCariTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Banyak cat yang aliasnya "cat besi" (nama diawali A, jadi kalau diurut abjad ia di atas).
        for i in range(70):
            buat_item(f"Avian {i:02d}", "1 Kg", kategori="Cat Minyak", brand="Avian")
        buat_item("Besi Beton", "10 MM", kategori="Besi", brand="Krakatau")
        buat_item("Kawat", "Bendrat", kategori="Besi", brand="Besindo")         # cocok lewat brand
        buat_item("Baut", "Besi 10", kategori="Besi", brand="ABC")              # cocok lewat varian
        layanan.sinkron_info()
        for i in range(70):
            layanan.ubah_alias_item(cls.admin, ItemInfo.objects.get(item_name=f"Avian {i:02d}"), "cat besi", hak=SEMUA_HAK)

    def test_daftar_barang_nama_dulu(self):
        self.client.force_login(self.admin)
        isi = self.client.get(reverse("master:barang") + "?q=besi").content.decode()
        posisi = [isi.index(n) for n in ("Besi Beton", "Baut", "Kawat", "Avian 00")]
        self.assertEqual(posisi, sorted(posisi))

    def test_harga_dan_stok_nama_dulu(self):
        self.client.force_login(self.admin)
        for nama in ("master:harga", "master:stok"):
            isi = self.client.get(reverse(nama) + "?q=besi").content.decode()
            self.assertLess(isi.index("Besi Beton"), isi.index("Avian 00"), nama)

    def test_cari_po_dan_nota_tidak_tergeser_alias(self):
        self.client.force_login(self.admin)
        for nama in ("master:pembelian_cari", "master:nota_manual_cari"):
            hasil = [h["nama"] for h in self.client.get(reverse(nama) + "?q=besi").json()["hasil"]]
            self.assertEqual(hasil[:3], ["Besi Beton", "Baut", "Kawat"], nama)   # walau ada 70 hasil alias
            self.assertIn("Avian 00", hasil)

    def test_cari_hitung_gudang(self):
        from master.views.gudang import _cari_varian
        hasil, _ = _cari_varian("besi", None)
        self.assertEqual([v.item_name for v in hasil[:2]], ["Besi Beton", "Baut"])
