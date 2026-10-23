"""Uji: catatan barang diambil dari supplier mana, dan riwayat dari pembelian."""
from datetime import date

from django.urls import reverse

from master.akun.akses import SEMUA_HAK
from master.layanan import pemasok
from master.layanan.inti import Tolak
from master.models import ItemInfo, Log, Pembelian, PembelianBaris, PemasokBarang, Supplier
from master.tests.dasar import Dasar


class PemasokTest(Dasar):
    def setUp(self):
        self.client.force_login(self.admin)
        self.palu = ItemInfo.objects.get(item_name="Palu")

    def test_tambah_dari_panel_dan_supplier_baru_otomatis(self):
        url = reverse("master:item", args=[self.palu.id])
        self.client.post(url, {"aksi": "pemasok_tambah", "supplier": "CV Semen Jaya", "catatan": "sales Pak Budi"})
        p = PemasokBarang.objects.get(item_info=self.palu)
        self.assertEqual((p.supplier.nama, p.catatan), ("CV Semen Jaya", "sales Pak Budi"))
        # Nama sama (beda huruf besar kecil) tidak membuat supplier dobel; catatan diperbarui.
        self.client.post(url, {"aksi": "pemasok_tambah", "supplier": "cv semen jaya", "catatan": "min 50 sak"})
        self.assertEqual(Supplier.objects.filter(nama__iexact="cv semen jaya").count(), 1)
        self.assertEqual(PemasokBarang.objects.get(item_info=self.palu).catatan, "min 50 sak")
        r = self.client.get(reverse("master:item_panel", args=[self.palu.id]))
        self.assertContains(r, "CV Semen Jaya")
        self.assertContains(r, "min 50 sak")
        self.assertTrue(Log.objects.filter(kolom="pemasok", rahasia=True).exists())
        self.client.post(url, {"aksi": "pemasok_hapus", "id": p.id})
        self.assertFalse(PemasokBarang.objects.exists())

    def test_riwayat_pembelian_otomatis_dan_filter_daftar_barang(self):
        s = Supplier.objects.create(nama="Toko Besi Maju")
        po = Pembelian.objects.create(nomor="PO-1", supplier=s, tanggal_pesan=date(2026, 10, 1), status="diterima")
        PembelianBaris.objects.create(pembelian=po, varian=self.info("Palu", "Kecil"), item_name="Palu",
                                      variant="Kecil", jumlah_pesan=5, harga=777)
        data = pemasok.untuk_barang(self.palu, SEMUA_HAK)
        self.assertEqual((data["riwayat"][0]["nama"], data["riwayat"][0]["po"]), ("Toko Besi Maju", 1))
        self.assertEqual(data["riwayat"][0]["harga"][0]["harga"], 777)
        tanpa_modal = pemasok.untuk_barang(self.palu, {"pembelian"})
        self.assertEqual(tanpa_modal["riwayat"][0]["harga"], [])
        r = self.client.get(reverse("master:barang") + f"?supplier={s.id}")
        self.assertContains(r, "Palu")
        self.assertNotContains(r, "Kikir")

    def test_tanpa_hak_pembelian_tidak_melihat_dan_tidak_bisa_mengubah(self):
        PemasokBarang.objects.create(item_info=self.palu, supplier=Supplier.objects.create(nama="Rahasia Jaya"))
        self.client.force_login(self.finance)
        r = self.client.get(reverse("master:item_panel", args=[self.palu.id]))
        self.assertNotContains(r, "Rahasia Jaya")
        self.assertNotContains(r, "Diambil dari")
        with self.assertRaises(Tolak):
            pemasok.tambah(self.finance, self.palu, nama_supplier="X", catatan="", hak={"master", "ubah_info"})

    def test_hak_pembelian_saja_tidak_bisa_membuat_supplier_baru(self):
        with self.assertRaises(Tolak):
            pemasok.tambah(self.admin, self.palu, nama_supplier="Belum Ada", catatan="", hak={"pembelian"})
        self.assertFalse(Supplier.objects.filter(nama="Belum Ada").exists())
