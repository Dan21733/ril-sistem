"""HP versi baru (3 Nov 2026): pindah toko + Semua toko, lembar Cek ACC, Periksa opname, warna toko."""
from django.urls import reverse

from pos.models import Item

from master.models import Opname, Toko
from master.tests.dasar import Dasar
from master.tests.test_hp import masuk_hp


class BerandaHpTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.toko1 = Toko.objects.filter(stok_di_kasir=True).first() or Toko.objects.create(
            kode="T1", nama="Toko 1", stok_di_kasir=True)
        cls.toko2 = Toko.objects.create(kode="T2X", nama="Toko Dua", warna="#123456", urutan=9)

    def setUp(self):
        masuk_hp(self.client, self.admin)

    def _opname(self):
        from master.layanan import opname
        opname.simpan_hitungan(self.gudang, {f"hitung_{self.info('Palu', 'Kecil').id}": "7",
                                             f"hitung_{self.info('Palu', 'Besar').id}": "10",
                                             f"hitung_{self.info('Kikir').id}": "4"})
        return opname.ajukan(self.gudang, dihitung_oleh="Andi, Budi")

    def test_beranda_satu_toko_dan_lembar_toko(self):
        r = self.client.get(reverse("hp:beranda"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Penjualan · Hari ini")
        self.assertContains(r, "Pindah toko")
        self.assertContains(r, "Semua toko")
        self.assertContains(r, "Toko Dua")
        self.assertContains(r, "#123456")                 # warna toko di lembar pindah toko
        self.assertNotContains(r, "Laba kotor")
        self.assertContains(r, "Tidak ada yang menunggu")

    def test_semua_toko_lalu_menu_lain_tetap_toko_satuan(self):
        self.client.post(reverse("hp:pilih_toko"), {"toko": "semua"})
        r = self.client.get(reverse("hp:beranda"))
        self.assertTemplateUsed(r, "hp/beranda_semua.html")
        self.assertContains(r, "Buka Toko Dua")
        # Menu lain: chip toko satuan (bukan "Semua toko").
        r = self.client.get(reverse("hp:barang"))
        self.assertEqual(r.context["toko_hp"], self.toko1)
        self.assertContains(r, f"Toko: {self.toko1.nama}")
        # Pilih toko satuan: keluar dari Semua toko, kembali ke halaman asal.
        r = self.client.post(reverse("hp:pilih_toko"), {"toko": self.toko2.id, "next": "/hp/barang/"})
        self.assertRedirects(r, "/hp/barang/", fetch_redirect_response=False)
        r = self.client.get(reverse("hp:beranda"))
        self.assertTemplateUsed(r, "hp/beranda.html")
        self.assertEqual(r.context["toko_hp"], self.toko2)
        # Alamat kembali di luar HP diabaikan.
        r = self.client.post(reverse("hp:pilih_toko"), {"toko": self.toko1.id, "next": "https://jahat.example/"})
        self.assertRedirects(r, reverse("hp:beranda"), fetch_redirect_response=False)

    def test_acc_opname_lewat_periksa_sebagian(self):
        op = self._opname()
        r = self.client.get(reverse("hp:beranda"))
        self.assertContains(r, "3 barang dihitung · 2 selisih")
        self.assertContains(r, reverse("hp:opname", args=[op.id]))
        r = self.client.get(reverse("hp:opname", args=[op.id]))
        self.assertContains(r, "Andi, Budi")
        self.assertContains(r, "1 barang cocok")
        kecil = op.baris.get(item_name="Palu", variant="Kecil")
        cocok = op.baris.get(item_name="Palu", variant="Besar")
        r = self.client.post(reverse("hp:opname", args=[op.id]), {"aksi": "acc", "pilih": [kecil.id, cocok.id]},
                             content_type="application/json")
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 7)
        self.assertEqual(Item.objects.get(item_name="Kikir").in_stock, 10)      # tidak dicentang: tidak diterapkan
        self.assertEqual(Opname.objects.get(pk=op.pk).status, "disetujui")

    def test_opname_tanpa_hak_ditolak(self):
        op = self._opname()
        masuk_hp(self.client, self.finance)
        self.assertNotEqual(self.client.get(reverse("hp:opname", args=[op.id])).status_code, 200)

    def test_warna_toko_di_master(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("master:toko"), {"aksi": "simpan", "id": self.toko2.id, "kode": "T2X", "nama": "Toko Dua",
                                                  "pkp": "0", "warna": "#aabbcc"})
        self.assertEqual(Toko.objects.get(pk=self.toko2.pk).warna, "#AABBCC")
        self.client.post(reverse("master:toko"), {"aksi": "simpan", "id": self.toko2.id, "kode": "T2X", "nama": "Toko Dua",
                                                  "pkp": "0", "warna": "merah"})
        self.assertEqual(Toko.objects.get(pk=self.toko2.pk).warna, "")
        self.assertTrue(Toko.objects.get(pk=self.toko2.pk).warna_tampil.startswith("#"))
