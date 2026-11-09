"""Penugasan & PIC (9 Nov 2026): PIC per lokasi, PO otomatis ke PIC lokasi tujuan, peringatan hitung area sendiri."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from master.akun.akses import SEMUA_HAK
from master.layanan import lokasi as svc_lokasi
from master.layanan import pembelian as svc
from master.layanan import tiket
from master.layanan.inti import Tolak
from master.models import Supplier
from master.tests.dasar import Dasar


class PenugasanTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.supplier = Supplier.objects.create(nama="PT Sumber")
        cls.eva = get_user_model().objects.create_user("eva", password="rahasia-123", first_name="Eva")
        cls.eva.groups.add(Group.objects.get(name="Gudang"))
        cls.ga = svc_lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A", pic=cls.eva.id)

    def test_pic_hanya_karyawan_gudang(self):
        with self.assertRaises(Tolak):
            svc_lokasi.simpan_lokasi(self.admin, hak=SEMUA_HAK, id=self.ga.id, kode="GA", nama="Gudang A", pic=self.finance.id)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:lokasi"))
        self.assertContains(r, 'selected>Eva</option>')

    def test_po_otomatis_ke_pic_lokasi_tujuan(self):
        po = svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-11-01")
        self.assertIsNone(po.penerima)                          # PO pertama: belum tahu tujuannya
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:pembelian")), "belum ditugaskan")
        r = self.client.post(reverse("master:pembelian_detail", args=[po.id]), {"aksi": "penerima", "lokasi": self.ga.id})
        po.refresh_from_db()
        self.assertEqual((po.lokasi, po.penerima), (self.ga, self.eva))
        # PO berikutnya dari supplier yang sama ikut lokasi & PIC itu.
        po2 = svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-11-02")
        self.assertEqual((po2.lokasi, po2.penerima), (self.ga, self.eva))
        # Pemilik bisa mengganti penerima (PIC sedang libur).
        svc.atur_penerima(self.admin, po2, lokasi=self.ga.id, penerima=self.gudang.id)
        self.assertEqual(po2.penerima, self.gudang)
        with self.assertRaises(Tolak):
            svc.atur_penerima(self.admin, po2, lokasi=self.ga.id, penerima=self.finance.id)
        # Hanya penerima yang melihat dan membuka PO-nya.
        svc.tambah_baris(po2, varian_id=self.info("Palu", "Kecil").id, jumlah=2, harga=900)
        self.client.force_login(self.eva)
        self.assertNotContains(self.client.get(reverse("gudang:tiket")), po2.nomor)
        self.assertEqual(self.client.get(reverse("gudang:datang_detail", args=[po2.id])).status_code, 404)
        self.client.force_login(self.gudang)
        self.assertContains(self.client.get(reverse("gudang:tiket")), po2.nomor)

    def test_peringatan_pic_menghitung_lokasinya_sendiri(self):
        t = tiket.buka(self.admin, untuk=self.eva, tujuan="hitung", jenis="hitung", hak=SEMUA_HAK,
                       batas=timezone.now() + timedelta(hours=2), lokasi=self.ga.id)
        self.assertIn("PIC Gudang A", tiket.peringatan_pic(t))
        t2 = tiket.buka(self.admin, untuk=self.gudang, tujuan="hitung", jenis="hitung", hak=SEMUA_HAK,
                        batas=timezone.now() + timedelta(hours=2), lokasi=self.ga.id)
        self.assertEqual(tiket.peringatan_pic(t2), "")
        self.client.force_login(self.admin)
        r = self.client.post(reverse("master:tiket_baru"), {
            "jenis": "hitung", "untuk": self.eva.id, "tujuan": "x", "lokasi": self.ga.id,
            "batas": (timezone.localtime() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")}, follow=True)
        self.assertContains(r, "adalah PIC Gudang A")
