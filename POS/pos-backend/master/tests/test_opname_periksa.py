"""Persiapan opname Desember (D, 12 Nov 2026): hitung ulang pemeriksa, draf harga/modal saat ACC, alasan selisih."""
import json
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from pos.models import Item

from master.akun.akses import SEMUA_HAK
from master.layanan import inti as layanan
from master.layanan import sesi_opname as svc
from master.layanan import tiket
from master.layanan.inti import Tolak
from master.models import HitungTempat, PeriksaUlang, Rak, SesiHarga, SesiHasil, StokLokasi, Tiket
from master.tests.dasar import Dasar, buat_item


class PeriksaUlangTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.layanan import lokasi
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A")
        Rak.objects.create(kode="A3", nama="Rak 3", tempat=cls.ga)
        Rak.objects.create(kode="P1", nama="Pajangan 1", tempat=cls.toko)
        buat_item("Semen", "50kg", kategori="Semen", modal=60000, jual=70000, floor=65000, stok=20)
        layanan.sinkron_info()

    def setUp(self):
        self.kecil = self.info("Palu", "Kecil")      # stok 10, modal 800, kategori Paku
        self.semen = self.info("Semen", "50kg")      # stok 20, kategori Semen
        self.sesi = svc.buka(self.admin, "Opname Desember", hak=SEMUA_HAK)
        self.t = tiket.buka(self.admin, untuk=self.gudang, tujuan="hitung", jenis="hitung", hak=SEMUA_HAK,
                            batas=timezone.now() + timedelta(hours=2), lokasi=self.toko.id, sesi=self.sesi)
        svc.simpan(self.gudang, self.t, [
            {"kunci": "a", "varian": self.kecil.id, "rak": "P1", "jumlah": "9"},
            {"kunci": "b", "varian": self.semen.id, "rak": "P1", "jumlah": "20"}])
        svc.kirim(self.gudang, self.t, "Andi")
        r = svc.rekap(self.sesi)
        svc.anggap_nol(self.admin, self.sesi, [f"{x['v'].id}-{x['lokasi'].id}" for x in r["belum"]], hak=SEMUA_HAK)

    def _baris(self, v):
        return next(b for b in svc.rekap(self.sesi)["baris"] if b["v"].id == v.id and b["lokasi"].id == self.toko.id)

    def test_pilih_tahan_acc_lalu_ulang_jadi_final(self):
        svc.pilih_ulang(self.admin, self.sesi, [f"{self.kecil.id}-{self.toko.id}"], hak=SEMUA_HAK)
        self.assertTrue(self._baris(self.kecil)["perlu_ulang"])
        with self.assertRaisesMessage(Tolak, "menunggu hitung ulang"):
            svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)
        p = svc.isi_ulang(self.admin, self.sesi, self.kecil.id, self.toko.id, "7", hak=SEMUA_HAK)
        self.assertEqual((p.awal, p.ulang, p.penghitung), (9, 7, "Andi"))
        b = self._baris(self.kecil)
        self.assertEqual((b["awal"], b["hitung"], b["selisih"]), (9, 7, -3))
        # Pemeriksa bukan "dihitung dua kali".
        self.assertEqual(svc.rekap(self.sesi)["konflik"], [])
        self.assertEqual(HitungTempat.objects.filter(varian=self.kecil).count(), 1)
        svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 7)
        self.assertTrue(SesiHasil.objects.filter(sesi=self.sesi, varian=self.kecil, hitung=7).exists())

    def test_kategori_rawan_selalu_ikut(self):
        svc.atur_rawan(self.admin, ["Semen", "TidakAda"], hak=SEMUA_HAK)
        self.assertEqual(svc.kategori_rawan(), ["Semen"])
        b = self._baris(self.semen)
        self.assertTrue(b["rawan"] and b["perlu_ulang"])
        self.assertEqual(svc.jumlah_menunggu(self.sesi), 1)
        # Baris rawan tidak bisa diambil acak / dibatalkan; acak mengambil sisanya (yang cocok pun ikut).
        svc.ambil_acak(self.admin, self.sesi, 50, hak=SEMUA_HAK)
        acak = set(PeriksaUlang.objects.filter(alasan_pilih="acak").values_list("varian_id", flat=True))
        self.assertNotIn(self.semen.id, acak)
        self.assertIn(self.kecil.id, acak)
        with self.assertRaises(Tolak):
            svc.ambil_acak(self.admin, self.sesi, 5, hak=SEMUA_HAK)
        svc.batal_pilih(self.admin, self.sesi, [f"{self.kecil.id}-{self.toko.id}"], hak=SEMUA_HAK)
        svc.isi_ulang(self.admin, self.sesi, self.semen.id, self.toko.id, "20", hak=SEMUA_HAK)
        self.assertEqual(PeriksaUlang.objects.get(varian=self.semen).alasan_pilih, "rawan")
        self.assertEqual(svc.jumlah_menunggu(self.sesi), PeriksaUlang.objects.filter(ulang__isnull=True).count())

    def test_isi_baris_yang_tidak_dipilih_ditolak(self):
        with self.assertRaisesMessage(Tolak, "tidak dipilih"):
            svc.isi_ulang(self.admin, self.sesi, self.kecil.id, self.toko.id, "5", hak=SEMUA_HAK)

    def test_akurasi_per_orang_dan_lokasi(self):
        svc.pilih_ulang(self.admin, self.sesi, [f"{v.id}-{self.toko.id}" for v in (self.kecil, self.semen)], hak=SEMUA_HAK)
        svc.isi_ulang(self.admin, self.sesi, self.kecil.id, self.toko.id, "7", hak=SEMUA_HAK)
        svc.isi_ulang(self.admin, self.sesi, self.semen.id, self.toko.id, "20", hak=SEMUA_HAK)
        a = svc.akurasi(svc.rekap(self.sesi))
        self.assertEqual(a["orang"], [{"nama": "Andi", "diperiksa": 2, "beda": 1, "nilai": 2 * 800}])
        self.assertEqual(a["lokasi"][0]["beda"], 1)

    def test_draf_harga_diterapkan_saat_acc(self):
        svc.simpan_harga(self.admin, self.sesi, self.kecil.id, modal="900", jual="2.000", floor="1200", hak=SEMUA_HAK)
        svc.simpan_harga(self.admin, self.sesi, self.semen.id, jual="72000", hak=SEMUA_HAK)
        with self.assertRaisesMessage(Tolak, "floor lebih tinggi"):
            svc.simpan_harga(self.admin, self.sesi, self.semen.id, floor="80000", hak=SEMUA_HAK)
        with self.assertRaises(Tolak):
            svc.simpan_harga(self.admin, self.sesi, self.semen.id, modal="1", hak={"opname", "ubah_harga"})
        # Draf belum mengubah apa pun sebelum ACC.
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").cost_amount, 800)
        svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)
        palu = Item.objects.get(item_name="Palu", variant="Kecil")
        self.assertEqual((palu.cost_amount, palu.max_price, palu.min_price), (900, 2000, 1200))
        self.kecil.refresh_from_db()
        self.assertEqual(self.kecil.modal_rata, 900)
        semen = Item.objects.get(item_name="Semen", variant="50kg")
        self.assertEqual((semen.cost_amount, semen.max_price, semen.min_price), (60000, 72000, 65000))
        self.assertEqual(SesiHasil.objects.get(sesi=self.sesi, varian=self.kecil).modal, 900)

    def test_draf_kosong_dihapus(self):
        svc.simpan_harga(self.admin, self.sesi, self.kecil.id, jual="2000", hak=SEMUA_HAK)
        svc.simpan_harga(self.admin, self.sesi, self.kecil.id, hak=SEMUA_HAK)
        self.assertFalse(SesiHarga.objects.exists())

    def test_alasan_wajib(self):
        svc.atur_alasan_wajib(self.admin, self.sesi, True, hak=SEMUA_HAK)
        self.sesi.refresh_from_db()
        with self.assertRaisesMessage(Tolak, "tanpa alasan"):
            svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)
        with self.assertRaises(Tolak):
            svc.isi_alasan(self.admin, self.sesi, self.kecil.id, self.toko.id, "ngarang", hak=SEMUA_HAK)
        for b in svc.rekap(self.sesi)["beda"]:
            svc.isi_alasan(self.admin, self.sesi, b["v"].id, b["lokasi"].id, "tidak_tahu", hak=SEMUA_HAK)
        svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)

    def test_alasan_mati_bisa_acc(self):
        self.assertFalse(self.sesi.alasan_wajib)
        svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)

    def test_meteran_ulang_berkoma(self):
        buat_item("Fiber Pagar", "Hijau", stok=50)
        layanan.sinkron_info()
        from master.layanan import satuan
        satuan.atur(self.admin, "Fiber Pagar", "m", 10)
        fiber = self.info("Fiber Pagar", "Hijau")
        svc.pilih_ulang(self.admin, self.sesi, [f"{fiber.id}-{self.toko.id}"], hak=SEMUA_HAK)
        p = svc.isi_ulang(self.admin, self.sesi, fiber.id, self.toko.id, "47,5", hak=SEMUA_HAK)
        self.assertEqual(p.ulang, 475)
        with self.assertRaises(Tolak):
            svc.isi_ulang(self.admin, self.sesi, fiber.id, self.toko.id, "1,25", hak=SEMUA_HAK)
        # Tanpa hitungan karyawan di lokasi itu: hasil ulang tetap menjadi baris.
        b = self._baris(fiber)
        self.assertEqual((b["awal"], b["hitung"]), (0, 475))

    def test_transfer_diperingatkan(self):
        r = svc.rekap(self.sesi)
        self.assertEqual(r["transfer"], {"selama": 0, "di_jalan": 0})

    def test_halaman_master_dan_hp(self):
        svc.atur_rawan(self.admin, ["Semen"], hak=SEMUA_HAK)
        self.client.force_login(self.admin)
        url = reverse("master:opname_sesi", args=[self.sesi.id])
        for tab in ("", "ulang", "harga", "selisih", "semua"):
            self.assertEqual(self.client.get(url + (f"?tab={tab}" if tab else "")).status_code, 200, tab)
        r = self.client.post(url, {"aksi": "pilih_ulang", "pilih": [f"{self.kecil.id}-{self.toko.id}"], "tab": "ulang"})
        self.assertEqual(r.status_code, 302)
        r = self.client.post(url, json.dumps({"aksi": "isi_ulang", "varian": self.kecil.id, "lokasi": self.toko.id,
                                              "jumlah": "8"}), content_type="application/json")
        self.assertEqual(r.json()["ok"], True)
        r = self.client.post(url, json.dumps({"aksi": "harga", "varian": self.kecil.id, "jual": "abc"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.client.post(url, {"aksi": "alasan_wajib", "nyala": "1"})
        self.assertContains(self.client.get(url + "?tab=selisih"), "data-alasan")
        # HP: hitungan awal tidak dikirim ke halaman sebelum diisi.
        from master.tests.test_hp import masuk_hp
        masuk_hp(self.client, self.admin)
        self.assertContains(self.client.get(reverse("hp:beranda")), "Hitung ulang opname")
        h = self.client.get(reverse("hp:opname_ulang"))
        self.assertContains(h, "Semen")
        self.assertNotContains(h, "Palu")       # sudah diisi
        r = self.client.post(reverse("hp:opname_ulang"), json.dumps({"varian": self.semen.id, "lokasi": self.toko.id,
                                                                       "jumlah": "19"}), content_type="application/json")
        self.assertEqual((r.json()["ok"], r.json()["beda"]), (True, -1))
