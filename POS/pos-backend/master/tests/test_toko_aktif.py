"""Uji: toko yang sedang dikerjakan (dipisah per toko) dan toko yang boleh dibuka tiap akun."""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from master.layanan import pembelian
from master.models import HakAkun, NotaManual, Pembelian, PreferensiAkun, Supplier, Toko
from master.tests.dasar import Dasar


class TokoAktifTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.t1 = Toko.objects.get(stok_di_kasir=True)
        cls.t2 = Toko.objects.create(kode="T2", nama="Toko Dua")
        cls.sup = Supplier.objects.create(nama="PT Sumber")
        pembelian.buat(cls.admin, supplier_id=cls.sup.id, tanggal_pesan=date(2026, 10, 1), toko=cls.t1)
        pembelian.buat(cls.admin, supplier_id=cls.sup.id, tanggal_pesan=date(2026, 10, 2), toko=cls.t2)

    def setUp(self):
        self.client.force_login(self.admin)

    def pilih(self, toko, client=None):
        return (client or self.client).post(reverse("master:pilih_toko"), {"toko": toko.id, "next": reverse("master:pembelian")})

    def test_bawaan_toko_stok_kasir_lalu_bisa_diganti_dan_diingat(self):
        isi = self.client.get(reverse("master:pembelian")).content.decode()
        self.assertIn(Pembelian.objects.get(toko=self.t1).nomor, isi)
        self.assertNotIn(Pembelian.objects.get(toko=self.t2).nomor, isi)
        r = self.pilih(self.t2)
        self.assertRedirects(r, reverse("master:pembelian"), fetch_redirect_response=False)
        isi = self.client.get(reverse("master:pembelian")).content.decode()
        self.assertIn(Pembelian.objects.get(toko=self.t2).nomor, isi)
        self.assertNotIn(Pembelian.objects.get(toko=self.t1).nomor, isi)
        self.assertEqual(PreferensiAkun.objects.get(user=self.admin).toko, self.t2)
        # Login baru (sesi lain) membuka toko terakhir.
        c = Client(); c.force_login(self.admin)
        self.assertContains(c.get(reverse("master:pembelian_baru")), 'name="toko" value="%d"' % self.t2.id)

    def test_formulir_memakai_toko_aktif(self):
        self.pilih(self.t2)
        r = self.client.get(reverse("master:pembelian_baru"))
        self.assertContains(r, "Toko Dua")
        self.assertNotContains(r, '<select name="toko"')
        self.client.post(reverse("master:pembelian_baru"), {"supplier": self.sup.id, "toko": self.t2.id,
                                                             "tanggal_pesan": "2026-10-05"})
        self.assertEqual(Pembelian.objects.order_by("-id").first().toko, self.t2)

    def test_akun_dibatasi_satu_toko(self):
        akun = get_user_model().objects.create_user("ibu", password="x-12345678")
        HakAkun.objects.create(user=akun, semua=True, toko=[self.t2.id])
        c = Client(); c.force_login(akun)
        isi = c.get(reverse("master:pembelian")).content.decode()
        self.assertIn(Pembelian.objects.get(toko=self.t2).nomor, isi)
        self.assertNotIn('id="pilih-toko"', isi)  # hanya satu toko: tidak ada pilihan
        self.pilih(self.t1, client=c)
        self.assertNotIn(Pembelian.objects.get(toko=self.t1).nomor, c.get(reverse("master:pembelian")).content.decode())

    def test_aplikasi_gudang_ikut_izin_toko_dan_menu_stok_hanya_toko_kasir(self):
        akun = get_user_model().objects.create_user("kepala", password="x-12345678")
        HakAkun.objects.create(user=akun, hak=["gudang", "gudang_hitung", "gudang_ambil", "ops_lihat_stok"],
                               toko=[self.t2.id])
        c = Client(); c.force_login(akun)
        r = c.get(reverse("gudang:beranda") + f"?toko={self.t1.id}")  # coba pindah ke toko yang tidak diizinkan
        self.assertContains(r, '<b class="judul">Toko Dua</b>')
        self.assertNotContains(r, reverse("gudang:ambil"))
        self.assertRedirects(c.get(reverse("gudang:ambil")), reverse("gudang:beranda"), fetch_redirect_response=False)

    def test_dashboard_dan_nota_tulis_tangan_per_toko(self):
        NotaManual.objects.create(nomor="NM-T2-1", toko=self.t2, tanggal=date.today(), total=50000, total_barang=50000,
                                  cara_bayar="cash", dibuat_oleh="admin")
        self.assertNotContains(self.client.get(reverse("master:nota_manual")), "NM-T2-1")
        self.pilih(self.t2)
        self.assertContains(self.client.get(reverse("master:nota_manual")), "NM-T2-1")

    def test_simpan_izin_toko_dari_halaman_pengguna(self):
        akun = get_user_model().objects.create_user("staf", password="x-12345678")
        self.client.post(reverse("master:pengguna"), {"aksi": "hak", "id": akun.id, "hak": ["gudang"], "toko": [self.t1.id]})
        self.assertEqual(HakAkun.objects.get(user=akun).toko, [self.t1.id])
