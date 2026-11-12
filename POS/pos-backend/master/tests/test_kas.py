"""Kas laci toko (rancangan 3 Nov 2026): buka, uang keluar, tutup, ACC."""
import datetime
import uuid
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from pos import kas
from pos.device_authentication import create_device_token
from pos.models import AksiPiutang, Device, DeviceStatus, KasGerak, KasSesi, RefundEvent, Transactions

from master.models import Toko
from master.tests.dasar import Dasar

PIN = {"111111": "ID001"}


def _cek_pin(pin, waktu=None, cache=None):
    from pos.worker_verification import InvalidWorkerCredentials
    if pin not in PIN:
        raise InvalidWorkerCredentials("PIN kasir tidak cocok.")
    return PIN[pin]


class _KasDasar(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.toko = Toko.objects.filter(stok_di_kasir=True).first() or Toko.objects.create(
            kode="KAS1", nama="Toko 1", stok_di_kasir=True)
        # Sisa minimal diuji sendiri (test_sisa_minimal); uji lain bebas mengambil semua uang.
        Toko.objects.filter(pk=cls.toko.pk).update(kas_sisa_minimal=0)
        # Nota Dasar dibuat "sekarang"; geser ke kemarin supaya tidak masuk kas uji.
        Transactions.objects.filter(pk=cls.nota.pk).update(transaction_time=timezone.now() - datetime.timedelta(days=1))
        cls.device = Device.objects.create(id=uuid.uuid4(), device_code="T01")
        cls.device2 = Device.objects.create(id=uuid.uuid4(), device_code="T02")
        DeviceStatus.objects.create(device_id=cls.device.id, nama="Tab depan", boleh_kas=True)
        DeviceStatus.objects.create(device_id=cls.device2.id, nama="Tab belakang")

    def _jual(self, total, cara="cash", bayar=None, waktu=None, worker="ID001"):
        bayar = total if bayar is None else bayar
        return Transactions.objects.create(
            sync_id=uuid.uuid4(), transaction_id=f"T01-{uuid.uuid4().int % 10**6:06d}", worker_id=worker,
            transaction_time=waktu or timezone.now(), item_amount=total, total_amount=total, payment_method=cara,
            payment_amount=bayar, change_amount=bayar - total)


class HitungKasTest(_KasDasar):
    def test_seharusnya_di_laci(self):
        s = kas.buka(self.toko.id, modal=500000, cadangan_lengkap=True, oleh="Daniel", dari="master")
        self._jual(100000, bayar=150000)                    # kembali 50rb -> laci +100rb
        self._jual(80000, cara="edc")
        transfer = self._jual(60000, cara="bank_transfer")
        AksiPiutang.objects.create(aksi_id=uuid.uuid4(), jenis="bayar", sync_id=uuid.uuid4(), jumlah=25000,
                                   cara="cash", worker_id="ID001", waktu=timezone.now())
        RefundEvent.objects.create(sync_id=transfer.sync_id, event_type="refund", reason="rusak", refund_method="cash",
                                   refund_amount=10000, approver_worker_id="ID001", device_code="T01")
        kas.catat_gerak({"gerak_id": str(uuid.uuid4()), "jenis": "keluar", "jumlah": 20000,
                         "kategori": "Ongkir / bensin"}, toko_id=self.toko.id, oleh="Rusdi", dari="T01")
        kas.catat_gerak({"gerak_id": str(uuid.uuid4()), "jenis": "tambah_modal", "jumlah": 200000},
                        toko_id=self.toko.id, oleh="Daniel", dari="master")
        r = kas.hitung(s)
        self.assertEqual(r["tunai_nota"], 100000)
        self.assertEqual((r["edc"], r["transfer"], r["jumlah_transfer"]), (80000, 60000, 1))
        self.assertEqual(r["per_kategori"], {"Ongkir / bensin": 20000})
        self.assertEqual(r["seharusnya"], 500000 + 100000 + 25000 - 10000 - 20000 + 200000)

    def test_tutup_bekukan_angka_dan_modal_besok(self):
        s = kas.buka(self.toko.id, modal=300000, cadangan_lengkap=True, oleh="Daniel", dari="master")
        self._jual(250000)
        s = kas.tutup(s.id, ambil_100=2, ambil_50=1, uang_sisa=290000, cadangan_lengkap=True, oleh="Rusdi",
                      dari="T01")
        self.assertEqual((s.seharusnya, s.dihitung, s.selisih, s.acc_status), (550000, 540000, -10000, "menunggu"))
        # Nota telat sesudah tutup masuk ke kas berikutnya, angka yang dibekukan tidak berubah.
        self._jual(40000)
        s.refresh_from_db()
        self.assertEqual(s.seharusnya, 550000)
        self.assertEqual(kas.modal_saran(self.toko.id), 290000)
        besok = kas.buka(self.toko.id, modal=290000, cadangan_lengkap=True, oleh="Rusdi", dari="T01")
        self.assertEqual(besok.modal_saran, 290000)
        self.assertEqual(kas.hitung(besok)["tunai_nota"], 40000)

    def test_penjualan_sebelum_buka_kas_ikut_kas_berikutnya(self):
        s = kas.buka(self.toko.id, modal=0, cadangan_lengkap=True, oleh="Daniel", dari="master")
        kas.tutup(s.id, ambil_100=0, ambil_50=0, uang_sisa=0, cadangan_lengkap=True, oleh="Daniel", dari="master")
        self._jual(70000)                                   # kas belum dibuka: penjualan tetap jalan
        s2 = kas.buka(self.toko.id, modal=0, cadangan_lengkap=True, oleh="Daniel", dari="master")
        self.assertEqual(kas.hitung(s2)["tunai_nota"], 70000)

    def test_cadangan_dipakai_lalu_diisi(self):
        s = kas.buka(self.toko.id, modal=0, pakai_cadangan=200000, cadangan_lengkap=False, oleh="Rusdi",
                     dari="T01")
        self.assertEqual(kas.cadangan_terpakai(self.toko.id), 200000)
        self.assertEqual(kas.hitung(s)["seharusnya"], 200000)
        s = kas.tutup(s.id, ambil_100=0, ambil_50=0, uang_sisa=0, isi_cadangan=200000, cadangan_lengkap=True,
                      oleh="Rusdi", dari="T01")
        self.assertEqual((s.seharusnya, s.selisih), (0, 0))
        self.assertEqual(kas.cadangan_terpakai(self.toko.id), 0)
        with self.assertRaises(kas.KasDitolak):
            kas.buka(self.toko.id, modal=0, pakai_cadangan=500000, cadangan_lengkap=True, oleh="R", dari="T01")

    def test_sisa_minimal_per_toko(self):
        Toko.objects.filter(pk=self.toko.pk).update(kas_sisa_minimal=400000)
        s = kas.buka(self.toko.id, modal=600000, cadangan_lengkap=True, oleh="Daniel", dari="master")
        with self.assertRaises(kas.KasDitolak):
            kas.tutup(s.id, diambil=300000, uang_sisa=300000, oleh="R", dari="T01")
        s = kas.tutup(s.id, diambil=200000, uang_sisa=400000, oleh="R", dari="T01")
        self.assertEqual((s.diambil, s.dihitung, s.selisih), (200000, 600000, 0))
        # Uang di laci memang di bawah minimal: semuanya ditinggal, boleh.
        s2 = kas.buka(self.toko.id, modal=250000, cadangan_lengkap=True, oleh="Daniel", dari="master")
        self.assertEqual(kas.tutup(s2.id, diambil=0, uang_sisa=250000, oleh="R", dari="T01").status, "ditutup")
        self.assertEqual(kas.sisa_minimal(self.toko.id), 400000)

    def test_satu_kas_buka_per_toko_dan_acc(self):
        s = kas.buka(self.toko.id, modal=0, cadangan_lengkap=True, oleh="Daniel", dari="master")
        with self.assertRaises(kas.KasDitolak):
            kas.buka(self.toko.id, modal=0, cadangan_lengkap=True, oleh="Daniel", dari="master")
        with self.assertRaises(kas.KasDitolak):
            kas.acc(s.id, setuju=True, oleh="Daniel")       # belum ditutup
        kas.tutup(s.id, ambil_100=0, ambil_50=0, uang_sisa=0, cadangan_lengkap=True, oleh="Daniel", dari="master")
        with self.assertRaises(kas.KasDitolak):
            kas.acc(s.id, setuju=False, oleh="Daniel")      # tolak wajib alasan
        s = kas.acc(s.id, setuju=True, oleh="Daniel", catatan="ok")
        self.assertEqual(s.acc_status, "diterima")
        with self.assertRaises(kas.KasDitolak):
            kas.acc(s.id, setuju=True, oleh="Daniel")

    def test_uang_keluar_wajib_kategori_dan_idempoten(self):
        gid = str(uuid.uuid4())
        with self.assertRaises(kas.KasDitolak):
            kas.catat_gerak({"gerak_id": gid, "jenis": "keluar", "jumlah": 5000}, toko_id=self.toko.id, oleh="R",
                            dari="T01")
        with self.assertRaises(kas.KasDitolak):
            kas.catat_gerak({"gerak_id": gid, "jenis": "keluar", "jumlah": 5000, "kategori": "Lainnya"},
                            toko_id=self.toko.id, oleh="R", dari="T01")
        d = {"gerak_id": gid, "jenis": "keluar", "jumlah": 5000, "kategori": "Makan & minum"}
        self.assertTrue(kas.catat_gerak(d, toko_id=self.toko.id, oleh="R", dari="T01")[1])
        self.assertFalse(kas.catat_gerak(d, toko_id=self.toko.id, oleh="R", dari="T01")[1])
        self.assertEqual(KasGerak.objects.count(), 1)

    def test_uang_keluar_telat_tidak_mengubah_kas_yang_sudah_ditutup(self):
        s = kas.buka(self.toko.id, modal=0, cadangan_lengkap=True, oleh="Daniel", dari="master")
        dulu = timezone.now()
        s = kas.tutup(s.id, ambil_100=0, ambil_50=0, uang_sisa=0, cadangan_lengkap=True, oleh="D", dari="master")
        g, _ = kas.catat_gerak({"gerak_id": str(uuid.uuid4()), "jenis": "keluar", "jumlah": 5000,
                                "kategori": "Makan & minum", "waktu": dulu.isoformat()},
                               toko_id=self.toko.id, oleh="R", dari="T01")
        self.assertGreater(g.waktu, s.ditutup_waktu)
        self.assertIn("tercatat telat", g.keterangan)
        with self.assertRaises(kas.KasDitolak):
            kas.batal_gerak(KasGerak.objects.create(
                gerak_id=uuid.uuid4(), toko_id=self.toko.id, jenis="keluar", jumlah=1, kategori="Lainnya",
                waktu=dulu).gerak_id, oleh="D", alasan="salah")


@override_settings(ROOT_URLCONF="config.urls",
                   CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                                       "LOCATION": "kas-kasir"}})
@patch("pos.transaction_request.verify_worker_pin", side_effect=_cek_pin)
class KasTabletTest(_KasDasar):
    def setUp(self):
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Device {create_device_token(self.device)}")
        self.api2 = APIClient()
        self.api2.credentials(HTTP_AUTHORIZATION=f"Device {create_device_token(self.device2)}")

    def test_alur_tablet(self, _):
        # Toko dipegang karyawan: angka kas boleh tampil di tablet.
        Toko.objects.filter(pk=self.toko.pk).update(kas_tampil_kasir=True)
        r = self.api.get("/api/kas/?antrean=0")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["boleh_kas"], r.data["mode_pemilik"], r.data["sesi"]), (True, False, None))
        self.assertEqual(self.api2.get("/api/kas/").data["boleh_kas"], False)
        # Tablet tanpa izin kas tidak bisa membuka.
        self.assertEqual(self.api2.post("/api/kas/buka/", {"pin": "111111", "modal": 0}, format="json").status_code, 403)
        self.assertEqual(self.api.post("/api/kas/buka/", {"pin": "999999", "modal": 0}, format="json").status_code, 400)
        r = self.api.post("/api/kas/buka/", {"pin": "111111", "modal": "300.000", "cadangan_lengkap": True},
                          format="json")
        self.assertEqual(r.status_code, 201, r.data)
        sesi = r.data["sesi"]
        self.assertEqual((sesi["modal_awal"], sesi["dibuka_oleh"]), (300000, "Rusdi"))
        self.assertNotIn("seharusnya", sesi)
        # Uang keluar dari tablet lain boleh; tambah modal hanya dari tablet kas.
        keluar = {"pin": "111111", "gerak_id": str(uuid.uuid4()), "jenis": "keluar", "jumlah": 15000,
                  "kategori": "Makan & minum"}
        self.assertEqual(self.api2.post("/api/kas/gerak/", keluar, format="json").status_code, 201)
        self.assertEqual(self.api2.post("/api/kas/gerak/", keluar, format="json").status_code, 200)
        modal = {"pin": "111111", "gerak_id": str(uuid.uuid4()), "jenis": "tambah_modal", "jumlah": 50000}
        self.assertEqual(self.api2.post("/api/kas/gerak/", modal, format="json").status_code, 400)
        self.assertEqual(self.api.post("/api/kas/gerak/", modal, format="json").status_code, 201)
        self.assertEqual([g["jumlah"] for g in self.api.get("/api/kas/").data["sesi"]["gerak"]], [15000, 50000])
        # Tutup ditahan selama tablet lain melapor antrean.
        self.api2.get("/api/kas/?antrean=2")
        tutup = {"pin": "111111", "sesi_id": sesi["id"], "ambil_100": 3, "ambil_50": 0, "uang_sisa": 35000,
                 "cadangan_lengkap": True, "antrean": 0}
        r = self.api.post("/api/kas/tutup/", tutup, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["tablet"][0]["nama"], "Tab belakang")
        self.assertEqual(self.api.post("/api/kas/tutup/", {**tutup, "antrean": 1}, format="json").status_code, 409)
        self.api2.get("/api/kas/?antrean=0")
        r = self.api.post("/api/kas/tutup/", tutup, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIsNone(r.data["sesi"])
        s = KasSesi.objects.get()
        self.assertEqual((s.seharusnya, s.selisih, s.acc_status), (335000, 0, "menunggu"))
        self.assertEqual(self.api.get("/api/kas/").data["modal_saran"], 35000)

    def test_mode_pemilik_kalau_tidak_ada_tablet_kas(self, _):
        DeviceStatus.objects.update(boleh_kas=False)
        self.assertTrue(self.api.get("/api/kas/").data["mode_pemilik"])


class KasMasterTest(_KasDasar):
    def test_halaman_master_buka_gerak_tutup_acc(self):
        self.client.force_login(self.admin)
        r = self.client.get("/master/kas/")
        self.assertContains(r, "Kas belum dibuka")
        self.client.post("/master/kas/", {"aksi": "buka", "modal": "200.000", "pakai_cadangan": "0", "cadangan_lengkap": "1"})
        s = KasSesi.objects.get()
        self.assertEqual((s.modal_awal, s.dibuka_dari), (200000, "master"))
        self._jual(50000)
        gid = str(uuid.uuid4())
        for _ in range(2):      # kirim dua kali (klik ganda) tetap satu catatan
            self.client.post("/master/kas/", {"aksi": "gerak", "gerak_id": gid, "jenis": "keluar", "jumlah": "10.000",
                                       "kategori": "Diambil pemilik"})
        self.assertEqual(KasGerak.objects.count(), 1)
        r = self.client.get("/master/kas/")
        self.assertContains(r, "240.000")                    # 200rb + 50rb - 10rb
        self.client.post("/master/kas/", {"aksi": "tutup", "sesi_id": str(s.id), "diambil": "200.000",
                                   "uang_sisa": "35.000"})
        s.refresh_from_db()
        self.assertEqual((s.status, s.selisih), ("ditutup", -5000))
        r = self.client.get(f"/master/kas/{s.id}/")
        self.assertContains(r, "menunggu ACC")
        self.client.post(f"/master/kas/{s.id}/", {"aksi": "tolak", "catatan": ""})
        s.refresh_from_db()
        self.assertEqual(s.acc_status, "menunggu")
        self.client.post(f"/master/kas/{s.id}/", {"aksi": "terima", "catatan": "kurang 5rb sudah dijelaskan"})
        s.refresh_from_db()
        self.assertEqual(s.acc_status, "diterima")

    def test_hp_kas_dan_acc_beranda(self):
        from master.layanan import push
        from master.tests.test_hp import masuk_hp
        masuk_hp(self.client, self.admin)
        self.assertContains(self.client.get("/hp/kas/"), "Buka kas")
        r = self.client.post("/hp/kas/", {"aksi": "buka", "modal": 100000, "cadangan_lengkap": True},
                             content_type="application/json")
        self.assertTrue(r.json()["ok"], r.json())
        s = KasSesi.objects.get()
        self.assertContains(self.client.get("/hp/"), "Kas laci")
        r = self.client.post("/hp/kas/", {"aksi": "tutup", "sesi_id": str(s.id), "diambil": 100000,
                                          "uang_sisa": 0}, content_type="application/json")
        self.assertTrue(r.json()["ok"], r.json())
        self.assertIn(f"kas:{s.id}", [m[0] for m in push.menunggu()])
        self.assertContains(self.client.get("/hp/"), "Tutup kas")
        r = self.client.post("/hp/acc/", {"jenis": "kas", "id": str(s.id), "aksi": "terima"},
                             content_type="application/json")
        self.assertTrue(r.json()["ok"], r.json())
        s.refresh_from_db()
        self.assertEqual(s.acc_status, "diterima")

    def test_tablet_izin_kas_dari_halaman_perangkat(self):
        self.client.force_login(self.admin)
        self.client.post("/master/kasir/tablet/", {"aksi": "kas_ya", "id": str(self.device2.id)})
        self.assertTrue(DeviceStatus.objects.get(device_id=self.device2.id).boleh_kas)
        self.assertContains(self.client.get("/master/kasir/tablet/"), "Buka &amp; tutup kas")

    def test_tanpa_hak_kas_ditolak(self):
        self.client.force_login(self.gudang)
        self.assertNotEqual(self.client.get("/master/kas/").status_code, 200)


class KasTampilTabletTest(_KasDasar):
    """Toko yang kasnya dipegang pemilik: tablet tidak menerima angka modal/sisa/tambah modal (12 Nov 2026)."""

    def test_disembunyikan_bawaan_dan_bisa_dinyalakan(self):
        s = kas.buka(self.toko.id, modal=500000, cadangan_lengkap=True, oleh="Daniel", dari="master")
        KasGerak.objects.create(gerak_id=uuid.uuid4(), toko_id=self.toko.id, jenis="tambah_modal", jumlah=100000,
                                oleh="Daniel", waktu=timezone.now())
        KasGerak.objects.create(gerak_id=uuid.uuid4(), toko_id=self.toko.id, jenis="keluar", jumlah=20000,
                                kategori="Bensin", oleh="Rusdi", waktu=timezone.now())
        with patch("pos.kas.toko_kasir", return_value=self.toko.id):
            st = kas.status_tablet(self.device.id)
            self.assertFalse(st["kas_tampil"])
            self.assertIsNone(st["sesi"]["modal_awal"])
            self.assertIsNone(st["modal_saran"])
            self.assertEqual([g["jenis"] for g in st["sesi"]["gerak"]], ["keluar"])
            Toko.objects.filter(pk=self.toko.pk).update(kas_tampil_kasir=True)
            st = kas.status_tablet(self.device.id)
            self.assertEqual(st["sesi"]["modal_awal"], 500000)
            self.assertEqual(len(st["sesi"]["gerak"]), 2)
        self.assertEqual(s.modal_awal, 500000)
