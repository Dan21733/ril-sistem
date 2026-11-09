"""Sesi opname (9 Nov 2026): banyak tugas paralel, hitungan per rak dijumlah, ACC sekali."""
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
from master.models import HitungTempat, Rak, SesiHasil, StokLokasi, Tiket, VarianInfo, VarianRak
from master.tests.dasar import Dasar, buat_item


class SesiOpnameTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from master.layanan import lokasi
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A")
        Rak.objects.create(kode="A3", nama="Rak 3", tempat=cls.ga)
        Rak.objects.create(kode="A7", nama="Rak 7", tempat=cls.ga)
        Rak.objects.create(kode="P1", nama="Pajangan 1", tempat=cls.toko)
        cls.gudang2 = get_user_model().objects.create_user("gudang2", password="rahasia-123", first_name="Budi")
        cls.gudang2.groups.add(Group.objects.get(name="Gudang"))

    def setUp(self):
        from master.layanan import lokasi
        self.kecil = self.info("Palu", "Kecil")   # stok total 10
        lokasi.pindah(self.admin, self.kecil, dari=self.toko, ke=self.ga, jumlah=10, hak=SEMUA_HAK)
        self.sesi = svc.buka(self.admin, "Opname Desember", hak=SEMUA_HAK)

    def _tugas(self, untuk, lokasi, **kw):
        return tiket.buka(self.admin, untuk=untuk, tujuan="hitung", jenis="hitung", hak=SEMUA_HAK,
                          batas=timezone.now() + timedelta(hours=2), lokasi=lokasi.id, sesi=self.sesi, **kw)

    def _isi(self, user, t, *entri):
        return svc.simpan(user, t, [{"kunci": f"{v.id}|{r}", "varian": v.id, "rak": r, "jumlah": j} for v, r, j in entri])

    def test_dua_rak_dua_orang_dijumlah_bukan_menimpa(self):
        """Contoh pemilik: palu 4 di rak 3 (Andi) + 6 di rak 7 (Budi) = 10, stok tidak berubah."""
        a, b = self._tugas(self.gudang, self.ga), self._tugas(self.gudang2, self.ga)
        self._isi(self.gudang, a, (self.kecil, "A3", "4"))
        self._isi(self.gudang2, b, (self.kecil, "A7", "6"))
        # Barang lain yang punya stok di toko harus dihitung juga sebelum ACC.
        with self.assertRaises(Tolak):
            svc.kembalikan(self.admin, a, hak=set())
        svc.kirim(self.gudang, a)
        svc.kirim(self.gudang2, b)
        r = svc.rekap(self.sesi)
        self.assertEqual([(x["v"].variant, x["lokasi"].kode, x["hitung"], x["selisih"]) for x in r["baris"]],
                         [("Kecil", "GA", 10, 0)])
        belum = {(x["v"].item_name, x["v"].variant, x["lokasi"].kode) for x in r["belum"]}
        self.assertIn(("Palu", "Besar", "TOKO"), belum)
        with self.assertRaises(Tolak):
            svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)
        svc.anggap_nol(self.admin, self.sesi, [f"{x['v'].id}-{x['lokasi'].id}" for x in r["belum"]], hak=SEMUA_HAK)
        svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Besar").in_stock, 0)
        self.assertEqual(StokLokasi.objects.get(varian=self.kecil, lokasi=self.ga).jumlah, 10)
        # Rak: utama = tempat terbanyak, sisanya rak tambahan.
        self.kecil.refresh_from_db()
        self.assertEqual(self.kecil.rak_id, "A7")
        self.assertEqual(list(VarianRak.objects.filter(varian=self.kecil).values_list("rak_id", flat=True)), ["A3"])
        self.sesi.refresh_from_db()
        self.assertEqual(self.sesi.status, "disetujui")
        self.assertEqual(Tiket.objects.get(pk=a.pk).status, "selesai")
        self.assertTrue(SesiHasil.objects.filter(sesi=self.sesi, varian=self.kecil, hitung=10, sistem=10).exists())

    def test_selisih_per_lokasi_gudang_lalu_toko(self):
        from master.layanan import lokasi
        lokasi.pindah(self.admin, self.kecil, dari=self.ga, ke=self.toko, jumlah=3, hak=SEMUA_HAK)  # GA 7, toko 3
        g, t = self._tugas(self.gudang, self.ga), self._tugas(self.gudang2, self.toko)
        self._isi(self.gudang, g, (self.kecil, "A3", "5"))           # GA kurang 2
        self._isi(self.gudang2, t, (self.kecil, "P1", "4"), (self.kecil, "", "1"))   # toko 5: lebih 2
        r = svc.rekap(self.sesi)
        svc.anggap_nol(self.admin, self.sesi, [f"{x['v'].id}-{x['lokasi'].id}" for x in r["belum"]], hak=SEMUA_HAK)
        Tiket.objects.filter(sesi=self.sesi).update(status="dikirim")
        svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)
        self.assertEqual(StokLokasi.objects.get(varian=self.kecil, lokasi=self.ga).jumlah, 5)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        self.assertEqual(lokasi.rincian(self.kecil)[self.toko.id], 5)

    def test_tempat_sama_dua_tugas_jadi_konflik(self):
        a, b = self._tugas(self.gudang, self.ga), self._tugas(self.gudang2, self.ga)
        self._isi(self.gudang, a, (self.kecil, "A3", "4"))
        self._isi(self.gudang2, b, (self.kecil, "A3", "4"))
        r = svc.rekap(self.sesi)
        self.assertEqual(len(r["konflik"]), 1)
        svc.hapus_hitungan(self.admin, self.sesi, r["konflik"][0]["hitungan"][1].id, hak=SEMUA_HAK)
        self.assertEqual(svc.rekap(self.sesi)["konflik"], [])

    def test_ketik_ulang_rak_sama_menimpa_dan_hapus(self):
        a = self._tugas(self.gudang, self.ga)
        self._isi(self.gudang, a, (self.kecil, "A3", "4"))
        self._isi(self.gudang, a, (self.kecil, "A3", "5"))
        self.assertEqual(list(HitungTempat.objects.values_list("jumlah", flat=True)), [5])
        hasil = svc.simpan(self.gudang, a, [{"kunci": "x", "varian": self.kecil.id, "rak": "A3", "hapus": True},
                                             {"kunci": "y", "varian": self.kecil.id, "rak": "P1", "jumlah": "2"},
                                             {"kunci": "z", "varian": self.kecil.id, "rak": "A7", "jumlah": "1,5"}])
        self.assertEqual(hasil["ok"], ["x"])
        self.assertEqual({g["kunci"] for g in hasil["gagal"]}, {"y", "z"})     # rak lokasi lain; bukan bilangan bulat
        self.assertFalse(HitungTempat.objects.exists())

    def test_satuan_meter_dan_tugas_ulang(self):
        buat_item("Fiber Pagar", "Hijau", stok=50)
        layanan.sinkron_info()
        from master.layanan import satuan
        satuan.atur(self.admin, "Fiber Pagar", "m", 10)                       # 50 m -> 500
        fiber = self.info("Fiber Pagar", "Hijau")
        r = svc.rekap(self.sesi)
        pasang = [f"{x['v'].id}-{x['lokasi'].id}" for x in r["belum"] if x["v"].id == fiber.id]
        tugas = svc.tugas_ulang(self.admin, self.sesi, pasang, untuk=self.gudang, hak=SEMUA_HAK)
        self.assertEqual(len(tugas), 1)
        t = tugas[0]
        self.assertEqual(t.saring["varian"], [fiber.id])
        self._isi(self.gudang, t, (fiber, "P1", "47,5"))
        self.assertEqual(HitungTempat.objects.get(varian=fiber).jumlah, 475)
        # Kasir: stok hanya ditutup untuk barang yang dihitung ulang (tugas lain tidak ada).
        from pos import data_master
        semua, kunci = data_master.stok_ditutup()
        self.assertFalse(semua)
        self.assertEqual(kunci, {("Fiber Pagar", "Hijau")})

    def test_tak_ditemukan_dinonaktifkan_saat_acc(self):
        buat_item("Gergaji", stok=0)
        layanan.sinkron_info()
        gergaji = self.info("Gergaji")
        a = self._tugas(self.gudang, self.ga)
        self._isi(self.gudang, a, (self.kecil, "A3", "10"))
        r = svc.rekap(self.sesi)
        self.assertIn(gergaji, r["tak_ditemukan"])
        svc.tandai_nonaktif(self.admin, self.sesi, [gergaji.id], True, hak=SEMUA_HAK)
        svc.anggap_nol(self.admin, self.sesi, [f"{x['v'].id}-{x['lokasi'].id}" for x in r["belum"]], hak=SEMUA_HAK)
        svc.kirim(self.gudang, a)
        svc.acc(self.admin, self.sesi, hak=SEMUA_HAK)
        gergaji.refresh_from_db()
        self.assertFalse(gergaji.aktif)

    def test_tablet_tanpa_stok_dan_kirim_json(self):
        a = self._tugas(self.gudang, self.ga, kategori=["Paku"])
        self.client.force_login(self.gudang)
        url = reverse("gudang:tiket_hitung", args=[a.id])
        r = self.client.get(url)
        self.assertContains(r, "data-barang")
        data = json.loads(r.content.decode().split('id="data-barang" type="application/json">')[1].split("</script>")[0])
        palu = next(x for x in data if x["i"] == self.kecil.id)
        self.assertNotIn("stok", json.dumps(data))
        self.assertTrue(palu["l"])
        self.assertEqual(set(palu), {"i", "n", "v", "b", "c", "a", "r", "s", "k", "l"})   # tanpa stok/harga/modal
        r = self.client.post(url, json.dumps({"entri": [{"kunci": "k1", "varian": self.kecil.id, "rak": "A3", "jumlah": "4"}]}),
                             content_type="application/json")
        self.assertEqual(r.json(), {"ok": ["k1"], "gagal": []})
        r = self.client.post(url, {"aksi": "kirim", "dihitung_oleh": "Andi"})
        self.assertEqual(Tiket.objects.get(pk=a.pk).status, "dikirim")
        # Setelah dikirim tidak bisa diisi lagi.
        r = self.client.post(url, json.dumps({"entri": []}), content_type="application/json")
        self.assertEqual(r.status_code, 409)

    def test_halaman_master_sesi(self):
        a = self._tugas(self.gudang, self.ga)
        self._isi(self.gudang, a, (self.kecil, "A3", "4"))
        self.client.force_login(self.admin)
        url = reverse("master:opname_sesi", args=[self.sesi.id])
        for tab in ("", "konflik", "belum", "tak", "selisih", "semua"):
            r = self.client.get(url + (f"?tab={tab}" if tab else ""))
            self.assertEqual(r.status_code, 200, tab)
        self.assertContains(self.client.get(url + "?tab=selisih"), "-6")
        r = self.client.get(reverse("master:tiket_baru") + f"?sesi={self.sesi.id}")
        self.assertContains(r, "Opname Desember")
        r = self.client.post(reverse("master:tiket_baru"), {
            "jenis": "hitung", "untuk": self.gudang2.id, "tujuan": "fitting", "lokasi": self.ga.id,
            "batas": (timezone.localtime() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"), "sesi": self.sesi.id})
        self.assertEqual(Tiket.objects.filter(sesi=self.sesi).count(), 2)
        self.assertRedirects(r, url, fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("master:opname")).status_code, 200)

    def test_satu_sesi_saja(self):
        with self.assertRaises(Tolak):
            svc.buka(self.admin, "lagi", hak=SEMUA_HAK)
        svc.batal(self.admin, self.sesi, hak=SEMUA_HAK)
        svc.buka(self.admin, "lagi", hak=SEMUA_HAK)
