"""Uji: beranda widget, laporan per jam/per hari, pergerakan stok, menu baru."""
import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from pos.models import Item, MutasiKasir, TransactionDetail, Transactions

from master.models import HakAkun, MutasiStok, PreferensiAkun
from master.tests.dasar import Dasar, buat_item


class BerandaTest(Dasar):
    def _simpan(self, data):
        return self.client.post(reverse("master:dashboard"), json.dumps(data), content_type="application/json")

    def test_angka_hari_ini_dan_grafik(self):
        from master.layanan import dasbor
        from master.akun.akses import hak_pengguna
        j = dasbor.penjualan(None, hak_pengguna(self.admin))
        # nota dasar: 2 x Palu Kecil @1.500, modal 800
        self.assertEqual((j["hari"]["omzet"], j["hari"]["nota"], j["hari"]["laba"]), (3000, 1, 1400))
        self.assertEqual(j["hari"]["margin"], 46.7)
        self.assertEqual(len(j["minggu"]), 7)
        self.assertTrue(j["minggu"][-1]["hari_ini"])
        self.assertEqual(j["minggu"][-1]["tinggi"], 100)
        jam = timezone.localtime(self.nota.transaction_time).hour
        self.assertEqual(j["jam_ramai"], jam)
        self.assertEqual({b["label"] for b in j["jam"] if b["nilai"]}, {f"{jam:02d}"})
        self.assertEqual(j["terlaris"][0]["nama"], "Palu")

    def test_susunan_simpan_dan_kembalikan_bawaan(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:dashboard"))
        self.assertContains(r, 'data-k="laba_hari" data-lebar')
        self.assertContains(r, "Atur tampilan")
        self.assertEqual(self._simpan({"susunan": [{"k": "jam", "lebar": 1}, {"k": "omzet_hari", "lebar": 2},
                                                   {"k": "jam"}, {"k": "ngawur"}]}).json(), {"ok": True})
        self.assertEqual(PreferensiAkun.objects.get(user=self.admin).dasbor,
                         [{"k": "jam", "lebar": 1}, {"k": "omzet_hari", "lebar": 2}])
        isi = self.client.get(reverse("master:dashboard")).content.decode()
        self.assertLess(isi.index('data-k="jam"'), isi.index('data-k="omzet_hari"'))
        self.assertNotIn('data-k="laba_hari" data-lebar', isi)
        self._simpan({"bawaan": True})
        self.assertEqual(PreferensiAkun.objects.get(user=self.admin).dasbor, [])
        self.assertEqual(self._simpan("bukan json").status_code, 400)
        r = self.client.post(reverse("master:dashboard"), "{rusak", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_tanpa_hak_modal_tidak_ada_laba(self):
        u = get_user_model().objects.create_user("kasirku", password="x-123-abc")
        HakAkun.objects.create(user=u, hak=["master", "laporan"])
        self.client.force_login(u)
        self._simpan({"susunan": [{"k": "laba_hari"}, {"k": "omzet_hari"}]})
        self.assertEqual(PreferensiAkun.objects.get(user=u).dasbor, [{"k": "omzet_hari", "lebar": 1}])
        r = self.client.get(reverse("master:dashboard"))
        self.assertNotContains(r, 'data-k="laba_hari" data-lebar')
        self.assertNotContains(r, "Laba kotor")
        self.assertNotContains(r, "Margin <b>")
        self.assertContains(r, "Rp3.000")


class LaporanJamHarianTest(Dasar):
    def test_per_jam_dan_per_hari(self):
        self.client.force_login(self.admin)
        jam = timezone.localtime(self.nota.transaction_time).hour
        r = self.client.get(reverse("master:laporan") + "?periode=hari&lap=jam")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f"{jam:02d}.00")
        self.assertContains(r, "Nota")
        r = self.client.get(reverse("master:laporan") + "?periode=7&lap=harian")
        self.assertContains(r, "Senin")
        self.assertContains(r, "Minggu")

    def test_kelompok_jam_isi_jam_kosong(self):
        from master.layanan import laporan
        hari = timezone.localdate()
        nota, _ = laporan.ambil(*laporan.rentang(hari, hari))
        jam = laporan.kelompok_nota(nota, "jam")
        self.assertGreaterEqual(len(jam), 11)                          # 07..17 minimal
        self.assertEqual([h["nama"] for h in jam], sorted(h["nama"] for h in jam))
        self.assertEqual(sum(h["omzet"] for h in jam), 3000)
        self.assertEqual(len(laporan.kelompok_nota(nota, "harian")), 7)


class PergerakanTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        MutasiKasir.objects.create(sync_id=cls.nota.sync_id, item_name="Palu", variant="Kecil", jumlah=-2,
                                   stok_sebelum=12, stok_sesudah=10)
        MutasiStok.objects.create(item_name="Palu", variant="Besar", jenis="beli", jumlah=5, stok_sebelum=5,
                                  stok_sesudah=10)
        MutasiStok.objects.create(item_name="Palu", variant="Besar", jenis="rusak", jumlah=-1, stok_sebelum=10,
                                  stok_sesudah=9)
        buat_item("Gergaji", kategori="Alat", modal=50000, stok=4)
        from master.layanan import inti
        inti.sinkron_info()

    def test_ringkasan(self):
        from master.layanan import pergerakan
        hari = timezone.localdate()
        from master.layanan import laporan
        kelompok, kategori, tabel, total = pergerakan.ringkasan(*laporan.rentang(hari, hari))
        b = {x["kunci"]: x for x in tabel}
        self.assertEqual((b["jual"]["keluar"], b["beli"]["masuk"], b["rusak_hilang"]["keluar"], b["hitung"]["kali"]), (2, 5, 1, 0))
        self.assertEqual((total["masuk"], total["keluar"], total["bersih"]), (5, 3, 2))
        k = {x["kunci"]: x for x in kelompok}
        self.assertEqual((k["masuk"]["tambah"], k["keluar"]["kurang"], k["sesuai"]["kurang"]), (5, 2, 1))
        self.assertEqual(kategori[0]["nama"], "Paku")
        self.assertEqual((kategori[0]["masuk"], kategori[0]["keluar"]), (5, 2))

    def test_tidak_laku(self):
        from master.layanan import pergerakan
        daftar, jumlah, nilai = pergerakan.tidak_laku(30, lihat_modal=True)
        nama = [(d["item_name"], d["variant"]) for d in daftar]
        self.assertNotIn(("Palu", "Kecil"), nama)                     # terjual di kasir
        gergaji = next(d for d in daftar if d["item_name"] == "Gergaji")
        self.assertEqual(gergaji["nilai"], 200000)
        self.assertEqual([d["nilai"] for d in daftar], sorted((d["nilai"] for d in daftar), reverse=True))
        daftar, _, nilai = pergerakan.tidak_laku(30, lihat_modal=False)
        self.assertIsNone(nilai)
        self.assertTrue(all(d["nilai"] is None for d in daftar))

    def test_halaman(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:pergerakan") + "?periode=hari&hari=90")
        self.assertContains(r, "Stock opname / hitung ulang")
        self.assertContains(r, "Dipindahkan ke toko lain")
        self.assertContains(r, "Total perubahan stok")
        self.assertContains(r, "Gergaji")
        self.assertContains(r, "Modal mengendap")
        self.assertContains(r, '<option value="90" selected>')
        u = get_user_model().objects.create_user("stokan", password="x-123-abc")
        HakAkun.objects.create(user=u, hak=["master", "lihat_stok"])
        self.client.force_login(u)
        r = self.client.get(reverse("master:pergerakan"))
        self.assertContains(r, "Gergaji")
        self.assertNotContains(r, "Modal mengendap")
        self.assertNotContains(r, "200.000")


class MenuBaruTest(Dasar):
    def test_menu_dan_halaman_baru(self):
        self.client.force_login(self.admin)
        isi = self.client.get(reverse("master:dashboard")).content.decode()
        for teks in ("Pergerakan stok", "Tugas karyawan", "Karyawan &amp; hak akses", "Perangkat"):
            self.assertIn(teks, isi)
        r = self.client.get(reverse("master:tiket_baru"))
        self.assertContains(r, "Buka tugas baru")
        r = self.client.get(reverse("master:tiket"))
        self.assertContains(r, "+ Buka tugas baru")
        self.assertEqual(self.client.get(reverse("master:tablet")).status_code, 200)
