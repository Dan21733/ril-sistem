"""Uji: panel barang di daftar barang dikirim lewat fetch (X-Panel), varian baru langsung di bawah tabel."""
from django.urls import reverse

from pos.models import Item

from master.models import ItemInfo, VarianInfo
from master.tests.dasar import Dasar


class PanelBarangTest(Dasar):
    def setUp(self):
        self.client.force_login(self.admin)
        self.palu = ItemInfo.objects.get(item_name="Palu")
        self.url = reverse("master:item", args=[self.palu.id])

    def test_baris_varian_baru_di_bawah_tabel(self):
        isi = self.client.get(reverse("master:barang") + f"item/{self.palu.id}/panel/").content.decode()
        self.assertIn('class="varian-baru"', isi)
        self.assertLess(isi.index('class="varian-baru"'), isi.index("</table>"))
        self.assertIn('id="form-varian-baru"', isi)

    def test_tambah_varian_lewat_panel_jawab_json(self):
        r = self.client.post(self.url, {"aksi": "tambah_varian", "variant": "Sedang", "min_price": "1.200",
                                        "max_price": "1800", "in_stock": "0"}, HTTP_X_PANEL="1")
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual((d["aksi"], d["hilang"]), ("tambah_varian", False))
        self.assertTrue(Item.objects.filter(item_name="Palu", variant="Sedang").exists())
        r = self.client.post(self.url, {"aksi": "tambah_varian", "variant": "Sedang", "min_price": "1",
                                        "max_price": "2"}, HTTP_X_PANEL="1")
        self.assertFalse(r.json()["ok"])                                 # varian kembar ditolak, pesan ikut

    def test_hapus_varian_terakhir_tidak_hilang_pencarian(self):
        kikir = ItemInfo.objects.get(item_name="Kikir")
        v = VarianInfo.objects.get(item_name="Kikir")
        url = reverse("master:item", args=[kikir.id])
        kembali = reverse("master:barang") + f"?q=ki&kategori=Paku&buka={kikir.id}"
        r = self.client.post(url, {"aksi": "buang", "id": v.id, "alasan": "duplikat", "kembali": kembali})
        self.assertRedirects(r, reverse("master:barang") + "?q=ki&kategori=Paku", fetch_redirect_response=False)
        buat = Item.objects.filter(item_name="Oracle").first()
        oracle = ItemInfo.objects.get(item_name=buat.item_name)
        r = self.client.post(reverse("master:item", args=[oracle.id]),
                             {"aksi": "buang", "id": VarianInfo.objects.get(item_name="Oracle").id}, HTTP_X_PANEL="1")
        self.assertEqual(r.json()["hilang"], True)
