"""Gaji karyawan (G, 12 Nov 2026)."""
import json
from datetime import date, datetime
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from pos.models import Worker

from master.akun.akses import SEMUA_HAK
from master.layanan import gaji as svc
from master.layanan.inti import Tolak
from master.models import BayarGaji, Biaya, GajiKaryawan, KaryawanLapangan
from master.tests.dasar import Dasar
from master.tests.test_hp import masuk_hp


class GajiTest(Dasar):
    def setUp(self):
        from master.layanan.toko import utama as toko_utama
        self.toko = toko_utama()
        self.budi = KaryawanLapangan.objects.create(nama="Budi")

    def _atur(self, kunci, **kw):
        data = {"pokok": "1.500.000", "tunjangan": "", "cara_tunjangan": "tetap", "periode": "bulanan",
                "tanggal_gajian": "25", "aktif": "1", "toko": str(self.toko.id), **kw}
        return svc.simpan(self.admin, kunci, data, hak=SEMUA_HAK)

    def test_daftar_gabungan_dan_atur(self):
        nama = {k["kunci"]: k for k in svc.daftar()}
        self.assertIn("w-ID001", nama)
        self.assertIn(f"l-{self.budi.id}", nama)
        g = self._atur(f"l-{self.budi.id}", periode="mingguan", hari_gajian="5", pokok="600000",
                       tunjangan="20000", cara_tunjangan="per_hari")
        self.assertEqual((g.nama, g.hari_gajian, g.tanggal_gajian), ("Budi", 5, None))
        self.assertEqual(svc.perkiraan(g)["total"], 600000 + 20000 * 6)
        with self.assertRaises(Tolak):
            self._atur("w-ID001", periode="mingguan", hari_gajian="")
        with self.assertRaises(Tolak):
            svc.simpan(self.admin, "w-ID001", {"periode": "bulanan", "tanggal_gajian": "1"}, hak={"laporan"})

    def test_jadwal(self):
        g = self._atur("w-ID001", tanggal_gajian="31")
        self.assertTrue(svc.gajian_pada(g, date(2026, 11, 30)))     # November tidak punya tgl 31
        self.assertFalse(svc.gajian_pada(g, date(2026, 11, 29)))
        m = self._atur(f"l-{self.budi.id}", periode="mingguan", hari_gajian="5")
        self.assertEqual(svc.gajian_terakhir(m, date(2026, 11, 12)), date(2026, 11, 7))   # Sabtu lalu
        t = {x["g"].nama: x for x in svc.jatuh_tempo(date(2026, 11, 12))}
        self.assertEqual(t["Budi"]["lewat"], 5)
        self.assertEqual(t["Rusdi"]["tanggal"], date(2026, 10, 31))

    def test_bayar_jadi_biaya_sekali(self):
        g = self._atur("w-ID001", tanggal_gajian="1")
        hari = timezone.localdate().replace(day=1)
        b = svc.bayar(self.admin, g.id, hari.isoformat(), hak=SEMUA_HAK)
        self.assertEqual(b.jumlah, 1500000)
        biaya = Biaya.objects.get(pk=b.biaya_id)
        self.assertEqual((biaya.kategori, biaya.jumlah, biaya.sumber, biaya.status), ("Gaji", 1500000, "luar_laci", "disetujui"))
        with self.assertRaisesMessage(Tolak, "sudah dibayar"):
            svc.bayar(self.admin, g.id, hari.isoformat(), hak=SEMUA_HAK)
        self.assertNotIn(g.id, [x["g"].id for x in svc.jatuh_tempo()])
        with self.assertRaisesMessage(Tolak, "bukan hari gajian"):
            svc.bayar(self.admin, g.id, hari.replace(day=2).isoformat() if hari.day == 1 else hari.isoformat(), hak=SEMUA_HAK)

    def test_tunjangan_per_hari_masuk(self):
        g = self._atur(f"l-{self.budi.id}", periode="harian", pokok="100000", tunjangan="15000", cara_tunjangan="per_hari")
        b = svc.bayar(self.admin, g.id, timezone.localdate().isoformat(), hak=SEMUA_HAK, hari_masuk="1")
        self.assertEqual((b.pokok, b.tunjangan, b.jumlah), (100000, 15000, 115000))

    def test_push_jam_12(self):
        from master.layanan import push
        self._atur("w-ID001", periode="harian", pokok="100000")
        siang = timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time()).replace(hour=12, minute=5))
        pagi = siang.replace(hour=9)
        with mock.patch("django.utils.timezone.now", return_value=pagi):
            self.assertFalse([s for s in push.menunggu() if s[1] == "gaji"])
        with mock.patch("django.utils.timezone.now", return_value=siang):
            gaji = [s for s in push.menunggu() if s[1] == "gaji"]
        self.assertEqual(len(gaji), 1)
        self.assertIn("Rusdi", gaji[0][3])

    def test_halaman_master_dan_hp(self):
        self._atur("w-ID001", periode="harian", pokok="100000")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:gaji"))
        self.assertContains(r, "Rusdi")
        self.assertContains(r, "Sudah dibayar")
        r = self.client.post(reverse("master:gaji"), {"aksi": "simpan", "kunci": f"l-{self.budi.id}", "periode": "bulanan",
                                                     "tanggal_gajian": "5", "pokok": "2000000", "aktif": "1"})
        self.assertEqual(GajiKaryawan.objects.get(lapangan=self.budi).pokok, 2000000)
        masuk_hp(self.client, self.admin)
        self.assertContains(self.client.get(reverse("hp:beranda")), "Gaji jatuh tempo")
        self.assertContains(self.client.get(reverse("hp:menu")), "Gaji karyawan")
        g = GajiKaryawan.objects.get(worker_id="ID001")
        r = self.client.post(reverse("hp:gaji"), json.dumps({"gaji": g.id, "tanggal": timezone.localdate().isoformat(),
                                                             "sumber": "luar_laci"}), content_type="application/json")
        self.assertEqual(r.json()["ok"], True)
        self.assertEqual(BayarGaji.objects.count(), 1)

    def test_tanpa_hak_gaji_ditolak(self):
        self.client.force_login(self.finance)
        self.assertNotEqual(self.client.get(reverse("master:gaji")).status_code, 200)
