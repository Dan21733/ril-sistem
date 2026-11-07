"""Biaya toko (rancangan pembukuan 7 Nov 2026): uang keluar laci ikut tercatat, ACC, ganti, kategori."""
import json
import time
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from pos import kas, pengaturan
from pos.device_authentication import create_device_token
from pos.models import KasGerak, PosSetting

from master.layanan import biaya as svc
from master.layanan.inti import Tolak
from master.models import Biaya, KategoriBiaya
from master.tests.dasar import PENYIMPANAN_UJI
from master.tests.test_kas import _cek_pin, _KasDasar


def _keluar(toko, jumlah, kategori="Ongkir / bensin", dari="T01", oleh="Rusdi", ket=""):
    g, _ = kas.catat_gerak({"gerak_id": str(uuid.uuid4()), "jenis": "keluar", "jumlah": jumlah, "kategori": kategori,
                            "keterangan": ket}, toko_id=toko.id, oleh=oleh, dari=dari, worker_id="ID001")
    return g


@override_settings(STORAGES=PENYIMPANAN_UJI)
class BiayaTest(_KasDasar):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_kategori_awal_dari_kategori_tablet(self):
        self.assertEqual([k.nama for k in KategoriBiaya.objects.all()], kas.KATEGORI_KELUAR)
        self.assertFalse(KategoriBiaya.objects.get(nama="Diambil pemilik").biaya_usaha)
        self.assertFalse(KategoriBiaya.objects.get(nama="Bayar supplier / barang").biaya_usaha)
        # Tablet membaca daftar dari master.
        svc.kategori_simpan(self.admin, {"nama": "Listrik", "biaya_usaha": "1"})
        self.assertIn("Listrik", kas.kategori_keluar())
        k = KategoriBiaya.objects.get(nama="Makan & minum")
        svc.kategori_simpan(self.admin, {"id": k.id, "nama": "Makan & minum", "aktif": "0"})
        self.assertNotIn("Makan & minum", kas.kategori_keluar())
        with self.assertRaises(kas.KasDitolak):
            _keluar(self.toko, 5000, "Makan & minum")
        with self.assertRaises(Tolak):
            svc.kategori_simpan(self.admin, {"nama": "listrik"})

    def test_uang_keluar_kasir_menunggu_acc_pemilik_langsung_setuju(self):
        g = _keluar(self.toko, 20000, ket="antar Pak Budi")
        _keluar(self.toko, 15000, "Belanja keperluan toko", dari="master", oleh="admin")
        self.assertEqual(svc.jumlah_menunggu(), 1)          # menu: belum ditarik pun sudah terhitung
        svc.tarik_dari_kas()
        b = Biaya.objects.get(gerak_id=g.gerak_id)
        self.assertEqual((b.status, b.sumber, b.jumlah, b.dicatat_oleh), ("menunggu", "laci", 20000, "Rusdi"))
        self.assertEqual(Biaya.objects.get(kategori="Belanja keperluan toko").status, "disetujui")
        svc.tarik_dari_kas()
        self.assertEqual(Biaya.objects.count(), 2)          # tidak dobel
        # Ringkasan hanya menghitung yang disetujui.
        awal, akhir = svc.rentang_bulan("")
        r = svc.ringkas(self.toko, awal, akhir)
        self.assertEqual((r["total_usaha"], r["menunggu"], r["menunggu_rp"]), (15000, 1, 20000))

    def test_tolak_uang_laci_jadi_tanggungan_sampai_diganti(self):
        g = _keluar(self.toko, 20000)
        svc.tarik_dari_kas()
        b = Biaya.objects.get(gerak_id=g.gerak_id)
        with self.assertRaises(Tolak):
            svc.acc(self.admin, b.id, False, "")            # alasan wajib
        svc.acc(self.admin, b.id, False, "bensin pribadi")
        b.refresh_from_db()
        self.assertTrue(b.perlu_diganti)
        # Uangnya tetap keluar dari laci: catatan kas tidak dibatalkan.
        self.assertFalse(KasGerak.objects.get(gerak_id=g.gerak_id).batal)
        awal, akhir = svc.rentang_bulan("")
        r = svc.ringkas(self.toko, awal, akhir)
        self.assertEqual((r["total_usaha"], r["perlu_diganti"], r["perlu_diganti_rp"]), (0, 1, 20000))
        with self.assertRaises(Tolak):
            svc.acc(self.admin, b.id, True)                 # sudah diputuskan
        svc.tandai_diganti(self.admin, b.id)
        b.refresh_from_db()
        self.assertFalse(b.perlu_diganti)

    def test_batal_di_kas_ikut_batal_dan_sebaliknya(self):
        g = _keluar(self.toko, 20000)
        svc.tarik_dari_kas()
        kas.batal_gerak(g.gerak_id, oleh="admin", alasan="salah ketik")
        svc.tarik_dari_kas()
        self.assertEqual(Biaya.objects.get(gerak_id=g.gerak_id).status, "batal")
        g2 = _keluar(self.toko, 30000)
        svc.tarik_dari_kas()
        svc.batal(self.admin, Biaya.objects.get(gerak_id=g2.gerak_id).id, "dobel")
        self.assertTrue(KasGerak.objects.get(gerak_id=g2.gerak_id).batal)

    def test_halaman_master_catat_luar_laci_dengan_foto_dan_acc(self):
        url = reverse("master:biaya")
        svc.kategori_simpan(self.admin, {"nama": "Listrik", "biaya_usaha": "1"})
        foto = SimpleUploadedFile("nota.jpg", b"\xff\xd8\xff\xe0isi", content_type="image/jpeg")
        self.client.post(url, {"aksi": "catat", "kategori": "Listrik", "jumlah": "Rp 350.000", "sumber": "luar_laci",
                               "keterangan": "PLN November", "foto": foto})
        b = Biaya.objects.get(kategori="Listrik")
        self.assertEqual((b.jumlah, b.status, b.sumber), (350000, "disetujui", "luar_laci"))
        self.assertTrue(b.foto_key)
        self.assertEqual(self.client.get(reverse("master:biaya_foto", args=[b.id])).status_code, 200)
        # Dari laci lewat halaman biaya: kas ikut berkurang.
        self.client.post(url, {"aksi": "catat", "kategori": "Ongkir / bensin", "jumlah": "25000", "sumber": "laci"})
        self.assertTrue(KasGerak.objects.filter(jenis="keluar", jumlah=25000, device_code="master").exists())
        self.assertEqual(Biaya.objects.get(jumlah=25000).status, "disetujui")
        g = _keluar(self.toko, 12000)
        isi = self.client.get(url).content.decode()
        self.assertIn("Menunggu ACC", isi)
        self.assertIn("375.000", isi)                       # 350.000 + 25.000
        self.client.post(url, {"aksi": "terima", "id": Biaya.objects.get(gerak_id=g.gerak_id).id})
        self.assertEqual(Biaya.objects.get(gerak_id=g.gerak_id).status, "disetujui")
        # Akun tanpa hak biaya tidak bisa membuka.
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_hp_acc_dan_catat(self):
        sesi = self.client.session
        sesi["hp_aktif"] = int(time.time())
        sesi.save()
        g = _keluar(self.toko, 18000)
        isi = self.client.get(reverse("hp:beranda")).content.decode()
        self.assertIn("Uang keluar", isi)
        b = Biaya.objects.get(gerak_id=g.gerak_id)
        r = self.client.post(reverse("hp:acc"), json.dumps({"jenis": "biaya", "id": b.id, "aksi": "tolak",
                                                             "alasan": "tanpa nota"}), content_type="application/json")
        self.assertIn("perlu mengganti", r.json()["pesan"])
        r = self.client.post(reverse("hp:biaya"), {"kategori": "Lainnya", "jumlah": "5000", "sumber": "luar_laci",
                                                   "keterangan": "fotokopi"})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertContains(self.client.get(reverse("hp:biaya")), "fotokopi")

    def test_migrasi_menyetujui_catatan_lama(self):
        """Catatan lama dimasukkan migrasi sebagai disetujui; di uji, cukup pastikan tarik tidak menimpanya."""
        g = _keluar(self.toko, 9000)
        Biaya.objects.create(toko=self.toko, tanggal=g.waktu.date(), kategori=g.kategori, jumlah=g.jumlah, sumber="laci",
                             gerak_id=g.gerak_id, status="disetujui", acc_oleh="otomatis")
        svc.tarik_dari_kas()
        self.assertEqual(Biaya.objects.get(gerak_id=g.gerak_id).status, "disetujui")


@override_settings(ROOT_URLCONF="config.urls",
                   CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                                       "LOCATION": "biaya-kasir"}})
@patch("pos.transaction_request.verify_worker_pin", side_effect=_cek_pin)
class SaklarTabletTest(_KasDasar):
    def test_kasir_tidak_boleh_uang_keluar(self, _):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Device {create_device_token(self.device2)}")
        keluar = {"pin": "111111", "gerak_id": str(uuid.uuid4()), "jenis": "keluar", "jumlah": 15000,
                  "kategori": "Ongkir / bensin"}
        self.assertEqual(api.get("/api/kas/").data["boleh_keluar"], True)
        PosSetting.objects.update_or_create(kunci="kasir_uang_keluar", defaults={"nilai": "0"})
        pengaturan.lupakan_cache()
        try:
            self.assertEqual(api.get("/api/kas/").data["boleh_keluar"], False)
            r = api.post("/api/kas/gerak/", keluar, format="json")
            self.assertEqual(r.status_code, 400)
            self.assertFalse(KasGerak.objects.exists())
        finally:
            pengaturan.lupakan_cache()
