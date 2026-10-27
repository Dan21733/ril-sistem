"""Uji menu Kasir: karyawan & PIN, ganti PIN terjadwal, tablet kasir."""
import datetime
import uuid
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from pos import pengaturan as aturan
from pos.models import Device, DeviceStatus, Worker, WorkerPin
from pos.worker_verification import PinOutsideValidity, InvalidWorkerCredentials, verify_worker_pin

from master.layanan import karyawan as svc
from master.layanan.inti import Tolak
from master.models import Log, PinTampil
from master.tests.dasar import Dasar


class KaryawanTest(Dasar):
    def setUp(self):
        aturan.lupakan_cache()

    def test_pin_lemah(self):
        for pin in ["111111", "121212", "123456", "654321", "123123", "199812", "251298"]:
            self.assertTrue(svc.pin_lemah(pin), pin)
        for pin in ["482915", "730264"]:
            self.assertFalse(svc.pin_lemah(pin), pin)

    def test_tambah_karyawan_membuat_pin_yang_langsung_berlaku(self):
        wid, pin = svc.tambah(self.admin, "  Amba  ")
        self.assertEqual(wid, "ID002")
        self.assertEqual(Worker.objects.get(id=wid).name, "Amba")
        self.assertEqual(len(pin), 6)
        self.assertFalse(svc.pin_lemah(pin))
        self.assertEqual(verify_worker_pin(pin), wid)
        self.assertTrue(PinTampil.objects.filter(worker_id=wid, pin=pin).exists())
        self.assertTrue(Log.objects.filter(objek="karyawan", baru__contains="Amba").exists())

    def test_reset_pin_hanya_menampilkan_pin_terbaru(self):
        wid, lama = svc.tambah(self.admin, "Eva")
        baru = svc.reset_pin(self.admin, wid)
        self.assertEqual(list(PinTampil.objects.filter(worker_id=wid).values_list("pin", flat=True)), [baru])
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:karyawan"))
        self.assertContains(r, baru)
        self.assertNotContains(r, f"<b>{lama}</b>")
        # data lama yang sudah terlanjur dobel: yang tampil hanya yang terbaru
        PinTampil.objects.create(worker_pin_id=999999, worker_id=wid, pin=lama,
                                 berlaku_mulai=timezone.now() - datetime.timedelta(days=1),
                                 hapus_setelah=timezone.now() + datetime.timedelta(days=1))
        self.assertEqual([t.pin for t in svc.pin_untuk_dibagikan() if t.worker_id == wid], [baru])
        self.assertNotContains(self.client.get(reverse("master:karyawan")), f"<b>{lama}</b>")

    def test_nomor_wa_dirapikan(self):
        for masuk in ["0812-3456 7890", "+62 812 3456 7890", "6281234567890", "81234567890"]:
            self.assertEqual(svc.rapikan_wa(masuk), "6281234567890", masuk)
        self.assertEqual(svc.rapikan_wa("  "), "")
        for salah in ["12345", "021", "4412345678901"]:
            with self.assertRaises(Tolak):
                svc.rapikan_wa(salah)
        self.assertEqual(svc.tampil_wa("6281234567890"), "0812-3456-7890")

    def test_kirim_pin_lewat_wa(self):
        from urllib.parse import unquote
        from master.models import KontakKaryawan
        self.client.force_login(self.admin)
        r = self.client.post(reverse("master:karyawan"), {"aksi": "tambah", "nama": "Amba", "wa": "0812 3456 7890"}, follow=True)
        wid = Worker.objects.get(name="Amba").id
        self.assertEqual(KontakKaryawan.objects.get(worker_id=wid).wa, "6281234567890")
        pin = PinTampil.objects.get(worker_id=wid).pin
        isi = unquote(r.content.decode())
        self.assertIn("https://wa.me/6281234567890?text=", isi)
        self.assertIn(f"Halo Amba, PIN kamu untuk kasir dan aplikasi toko: {pin}", isi)
        self.assertIn("WA 0812-3456-7890", isi)
        # nomor salah ditolak dan nama tidak ikut berubah
        r = self.client.post(reverse("master:karyawan"), {"aksi": "nama", "id": wid, "nama": "Amba B", "wa": "123"}, follow=True)
        self.assertContains(r, "Nomor WA tidak dikenali")
        self.assertEqual(Worker.objects.get(id=wid).name, "Amba")
        # dikosongkan = dihapus; tombol tetap ada tapi memilih kontak
        self.client.post(reverse("master:karyawan"), {"aksi": "nama", "id": wid, "nama": "Amba", "wa": ""})
        self.assertFalse(KontakKaryawan.objects.filter(worker_id=wid).exists())
        r = self.client.get(reverse("master:karyawan"))
        self.assertContains(r, "Kirim WA (pilih kontak)")
        self.assertContains(r, "https://wa.me/?text=")
        # tambah dengan nomor salah tidak membuat karyawan
        self.client.post(reverse("master:karyawan"), {"aksi": "tambah", "nama": "Cici", "wa": "999"})
        self.assertFalse(Worker.objects.filter(name="Cici").exists())

    def test_pesan_pin_menyebut_waktu_berlaku(self):
        nanti = timezone.now() + datetime.timedelta(days=1)
        self.assertIn("Berlaku mulai", svc.pesan_pin("Budi", "482915", nanti))
        self.assertIn("Sudah berlaku sekarang", svc.pesan_pin("Budi", "482915", timezone.now() - datetime.timedelta(minutes=1)))

    def test_nama_kembar_ditolak(self):
        with self.assertRaises(Tolak):
            svc.tambah(self.admin, "rusdi")

    def test_karyawan_lama_diberi_riwayat_dan_pinnya_tetap_jalan(self):
        svc.pastikan_riwayat()
        baris = WorkerPin.objects.get(worker_id="ID001")
        self.assertIsNone(baris.berlaku_mulai)
        self.assertEqual(baris.pin_hash, "x")  # PIN lama disalin apa adanya
        self.assertTrue(next(d for d in svc.daftar() if d["id"] == "ID001")["aktif"])

    def test_pin_baru_tidak_pernah_sama_dengan_pin_yang_masih_dipakai(self):
        _, pin_amba = svc.tambah(self.admin, "Amba")
        angka = int(pin_amba)
        with patch("master.layanan.karyawan.secrets.randbelow", side_effect=[angka, 482915]):
            self.assertEqual(svc.pin_acak(), "482915")

    def test_reset_pin_pin_lama_hanya_untuk_nota_sebelumnya(self):
        wid, lama = svc.tambah(self.admin, "Amba")
        sebelum = timezone.now()
        baru = svc.reset_pin(self.admin, wid)
        self.assertNotEqual(lama, baru)
        self.assertEqual(verify_worker_pin(baru), wid)
        with self.assertRaises(PinOutsideValidity):
            verify_worker_pin(lama)
        self.assertEqual(verify_worker_pin(lama, waktu=sebelum - datetime.timedelta(seconds=1)), wid)

    def test_nonaktif_lalu_aktif_lagi(self):
        wid, pin = svc.tambah(self.admin, "Amba")
        svc.nonaktifkan(self.admin, wid)
        with self.assertRaises(InvalidWorkerCredentials):
            verify_worker_pin(pin)
        self.assertFalse(next(d for d in svc.daftar() if d["id"] == wid)["aktif"])
        self.assertFalse(PinTampil.objects.filter(worker_id=wid).exists())
        pin2 = svc.aktifkan(self.admin, wid)
        self.assertEqual(verify_worker_pin(pin2), wid)
        self.assertTrue(next(d for d in svc.daftar() if d["id"] == wid)["aktif"])

    def test_pin_terbaca_dihapus_setelah_masanya(self):
        svc.tambah(self.admin, "Amba")
        nanti = timezone.now() + datetime.timedelta(days=svc.PIN_TAMPIL_HARI, minutes=1)
        self.assertEqual(svc.bersihkan_pin_tampil(nanti), 1)


class RotasiTest(Dasar):
    def setUp(self):
        aturan.lupakan_cache()
        self.wid, self.pin = svc.tambah(self.admin, "Amba")
        besok = timezone.localtime() + datetime.timedelta(days=1)
        self.data = {"rotasi_aktif": "1", "rotasi_interval_hari": "14", "rotasi_hari": str(besok.weekday()),
                     "rotasi_jam": "21:00", "rotasi_siapkan_hari": "2", "pin_tenggang_hari": "14"}

    def test_pin_berikutnya_disiapkan_dan_berlaku_tepat_waktu(self):
        svc.simpan_aturan_pin(self.admin, self.data)  # jadwal besok 21:00, siapkan 2 hari -> langsung dibuat
        berikut = svc.rotasi_berikut()
        self.assertIsNotNone(berikut)
        siap = WorkerPin.objects.get(worker_id=self.wid, berlaku_mulai=berikut)
        tampil = PinTampil.objects.get(worker_pin_id=siap.id)
        # Sekarang PIN lama masih berlaku, PIN baru belum.
        self.assertEqual(verify_worker_pin(self.pin), self.wid)
        with self.assertRaises(PinOutsideValidity):
            verify_worker_pin(tampil.pin)
        # Karyawan lama (Rusdi) ikut mendapat PIN berikutnya.
        self.assertTrue(WorkerPin.objects.filter(worker_id="ID001", berlaku_mulai=berikut).exists())
        # Setelah jadwal lewat: PIN baru berlaku, nota lama dengan PIN lama tetap diterima.
        lewat = berikut + datetime.timedelta(minutes=5)
        with patch("pos.worker_verification.timezone.now", return_value=lewat):
            self.assertEqual(verify_worker_pin(tampil.pin), self.wid)
            with self.assertRaises(PinOutsideValidity):
                verify_worker_pin(self.pin)
            self.assertEqual(verify_worker_pin(self.pin, waktu=berikut - datetime.timedelta(hours=1)), self.wid)
        # Penjadwal memajukan jadwal 14 hari, tanpa membuat PIN dobel.
        self.assertEqual(svc.jalankan_rotasi(lewat), 0)
        self.assertEqual(svc.rotasi_berikut(), berikut + datetime.timedelta(days=14))

    def test_ulang_tidak_membuat_pin_dobel(self):
        svc.simpan_aturan_pin(self.admin, self.data)
        jumlah = WorkerPin.objects.count()
        self.assertEqual(svc.jalankan_rotasi(), 0)
        self.assertEqual(WorkerPin.objects.count(), jumlah)

    def test_ganti_jadwal_membatalkan_pin_yang_sudah_disiapkan(self):
        svc.simpan_aturan_pin(self.admin, self.data)
        lama = svc.rotasi_berikut()
        self.assertTrue(WorkerPin.objects.filter(berlaku_mulai=lama).exists())
        self.data["rotasi_jam"] = "22:30"
        svc.simpan_aturan_pin(self.admin, self.data)
        self.assertFalse(WorkerPin.objects.filter(berlaku_mulai=lama).exists())
        self.assertEqual(verify_worker_pin(self.pin), self.wid)
        self.assertEqual(timezone.localtime(svc.rotasi_berikut()).strftime("%H:%M"), "22:30")

    def test_matikan_rotasi(self):
        svc.simpan_aturan_pin(self.admin, self.data)
        self.data["rotasi_aktif"] = ""
        svc.simpan_aturan_pin(self.admin, self.data)
        self.assertIsNone(svc.rotasi_berikut())
        self.assertFalse(WorkerPin.objects.filter(berlaku_mulai__gt=timezone.now()).exists())

    def test_karyawan_nonaktif_tidak_diberi_pin(self):
        svc.nonaktifkan(self.admin, self.wid)
        svc.simpan_aturan_pin(self.admin, self.data)
        self.assertFalse(WorkerPin.objects.filter(worker_id=self.wid, berlaku_mulai=svc.rotasi_berikut()).exists())

    def test_isian_salah_ditolak(self):
        self.data["rotasi_interval_hari"] = "0"
        with self.assertRaises(Tolak):
            svc.simpan_aturan_pin(self.admin, self.data)

    def test_perintah_penjadwal(self):
        svc.simpan_aturan_pin(self.admin, self.data)
        call_command("master_jadwal", stdout=StringIO())
        self.assertTrue(svc.penjadwal_hidup())


class HalamanKasirTest(Dasar):
    def setUp(self):
        aturan.lupakan_cache()
        self.client.force_login(self.admin)
        self.dev = Device.objects.create(id=uuid.uuid4(), device_code="T01")

    def test_halaman_terbuka(self):
        svc.tambah(self.admin, "Amba")
        for nama in ("karyawan", "karyawan_slip", "tablet", "aturan_kasir"):
            r = self.client.get(reverse(f"master:{nama}"))
            self.assertEqual(r.status_code, 200, nama)
        isi = self.client.get(reverse("master:karyawan")).content.decode()
        self.assertIn("PIN baru untuk dibagikan", isi)

    def test_finance_tidak_boleh(self):
        self.client.force_login(self.finance)
        r = self.client.get(reverse("master:karyawan"))
        self.assertNotEqual(r.status_code, 200)

    def test_tambah_lewat_halaman(self):
        self.client.post(reverse("master:karyawan"), {"aksi": "tambah", "nama": "Budi"})
        self.assertTrue(Worker.objects.filter(name="Budi").exists())

    def test_nonaktifkan_tablet_dan_tutup_pendaftaran(self):
        self.client.post(reverse("master:tablet"), {"aksi": "nonaktif", "id": str(self.dev.id)})
        self.assertFalse(DeviceStatus.objects.get(device_id=self.dev.id).aktif)
        self.client.post(reverse("master:tablet"), {"aksi": "nama", "id": str(self.dev.id), "nama": "Tab depan"})
        self.assertEqual(DeviceStatus.objects.get(device_id=self.dev.id).nama, "Tab depan")
        self.client.post(reverse("master:tablet"), {"aksi": "pendaftaran", "buka": "0"})
        aturan.lupakan_cache()
        self.assertEqual(aturan.baca("pendaftaran_tablet"), "tutup")
        isi = self.client.get(reverse("master:tablet")).content.decode()
        self.assertIn("TERTUTUP", isi)
        self.assertIn("nonaktif", isi)


class NotaTinjauTest(Dasar):
    def setUp(self):
        from pos.models import NotaTinjau

        aturan.lupakan_cache()
        self.client.force_login(self.admin)
        self.sid = uuid.uuid4()
        self.nota = NotaTinjau.objects.create(
            sync_id=self.sid, transaction_id="T01-000050", device_code="T01", alasan="pin: PIN kasir tidak cocok.",
            payload={"sync_id": str(self.sid), "transaction_id": "T01-000050",
                     "transaction_time": (timezone.now() - datetime.timedelta(days=2)).isoformat(),
                     "item_amount": 1000, "shipping_amount": 0, "total_amount": 1000, "payment_method": "cash",
                     "payment_amount": 1000, "change_amount": 0, "cust_name": "Budi", "phone_no": "",
                     "items": [{"item_name": "Palu", "variant": "Kecil", "quantity": 2, "unit_price": 500,
                                "discount": 0, "subtotal": 1000}]})

    def test_terima_masuk_penjualan_dengan_waktu_asli_dan_potong_stok(self):
        from pos.models import Item, Transactions
        from master.layanan import nota_tinjau as t

        stok = Item.objects.get(item_name="Palu", variant="Kecil").in_stock
        t.terima(self.admin, self.sid, "ID001")
        tr = Transactions.objects.get(sync_id=self.sid)
        self.assertEqual((tr.transaction_id, tr.worker_id), ("T01-000050", "ID001"))
        self.assertLess(tr.transaction_time, timezone.now() - datetime.timedelta(days=1))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, stok - 2)
        self.nota.refresh_from_db()
        self.assertEqual(self.nota.status, "diterima")
        with self.assertRaises(Tolak):
            t.terima(self.admin, self.sid, "ID001")

    def test_terima_wajib_pilih_kasir(self):
        from master.layanan import nota_tinjau as t

        with self.assertRaises(Tolak):
            t.terima(self.admin, self.sid, "")

    def test_barang_hilang_tidak_bisa_diterima(self):
        from master.layanan import nota_tinjau as t

        self.nota.payload["items"][0]["item_name"] = "Barang Lenyap"
        self.nota.save()
        with self.assertRaises(Tolak) as ctx:
            t.terima(self.admin, self.sid, "ID001")
        self.assertIn("Barang dihapus", str(ctx.exception))
        self.assertTrue(t.daftar()[0]["barang_hilang"])

    def test_tolak_wajib_alasan_dan_tidak_masuk_penjualan(self):
        from pos.models import Transactions
        from master.layanan import nota_tinjau as t

        with self.assertRaises(Tolak):
            t.tolak(self.admin, self.sid, " ")
        t.tolak(self.admin, self.sid, "nota palsu")
        self.assertFalse(Transactions.objects.filter(sync_id=self.sid).exists())
        self.nota.refresh_from_db()
        self.assertEqual((self.nota.status, self.nota.catatan), ("ditolak", "nota palsu"))

    def test_halaman_dan_angka_di_menu(self):
        isi = self.client.get(reverse("master:nota_tinjau")).content.decode()
        self.assertIn("T01-000050", isi)
        self.assertIn("di bawah floor", isi)
        self.assertIn('class="angka-menu">1<', isi)
        self.client.post(reverse("master:nota_tinjau"), {"aksi": "terima", "sync_id": str(self.sid), "worker_id": "ID001"})
        self.nota.refresh_from_db()
        self.assertEqual(self.nota.status, "diterima")


class RefundPersenTest(Dasar):
    def setUp(self):
        aturan.lupakan_cache()

    def _refund(self, jumlah):
        from pos.refund_service import persist_refund

        return persist_refund({"refund_id": uuid.uuid4(), "sync_id": self.nota.sync_id, "event_type": "refund",
                               "reason": "uji", "refund_amount": jumlah, "items": []}, "ID001", "T01")

    def test_batas_persen_dari_total_nota(self):
        from pos.refund_service import RefundValidationError

        aturan.simpan(refund_maks_persen="90")
        with self.assertRaises(RefundValidationError) as ctx:
            self._refund(2800)  # total nota 3000, 90% = 2700
        self.assertIn("90%", str(ctx.exception))
        self._refund(2700)

    def test_bawaan_seratus_persen(self):
        self._refund(3000)

    def test_disimpan_dari_aturan_kasir(self):
        svc.simpan_aturan_pin(self.admin, {"rotasi_interval_hari": "14", "rotasi_hari": "5", "rotasi_jam": "21:00",
                                           "rotasi_siapkan_hari": "2", "pin_tenggang_hari": "14",
                                           "refund_maks_persen": "80"})
        aturan.lupakan_cache()
        self.assertEqual(aturan.baca("refund_maks_persen"), "80")


class PenyetujuTest(Dasar):
    def setUp(self):
        aturan.lupakan_cache()
        self.client.force_login(self.admin)

    def test_tandai_dan_cabut_penyetuju(self):
        self.client.post(reverse("master:karyawan"), {"aksi": "penyetuju", "id": "ID001"})
        aturan.lupakan_cache()
        self.assertEqual(aturan.penyetuju_floor(), {"ID001"})
        self.assertIn("penyetuju", self.client.get(reverse("master:karyawan")).content.decode())
        self.client.post(reverse("master:karyawan"), {"aksi": "bukan_penyetuju", "id": "ID001"})
        aturan.lupakan_cache()
        self.assertEqual(aturan.penyetuju_floor(), set())

    def test_panel_nota_menyebut_penyetuju(self):
        from pos.models import NotaPersetujuan

        NotaPersetujuan.objects.create(sync_id=self.nota.sync_id, approver_worker_id="ID001")
        isi = self.client.get(reverse("master:nota_panel", args=[self.nota.sync_id])).content.decode()
        self.assertIn("disetujui dengan PIN <b>Rusdi</b>", isi)
