"""Penugasan & PIC (9 Nov 2026): PIC per lokasi, PO otomatis ke PIC lokasi tujuan, peringatan hitung area sendiri."""
import json
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
        self.assertContains(r, '<td>Eva</td>')
        # PIC tidak diubah dari halaman Lokasi (diatur di Tim gudang): simpan tanpa "pic" = tetap.
        self.client.post(reverse("master:lokasi"), {"aksi": "simpan", "id": self.ga.id, "kode": "GA", "nama": "Gudang A2"})
        self.ga.refresh_from_db()
        self.assertEqual((self.ga.nama, self.ga.pic), ("Gudang A2", self.eva))

    def test_pic_dicentang_di_karyawan(self):
        """12 Nov 2026: PIC = karyawan yang dicentang "PIC gudang", bukan semua akun aplikasi gudang."""
        from master.layanan import akun_karyawan
        from master.layanan import karyawan as svc_karyawan
        wid, _ = svc_karyawan.tambah(self.admin, "Joni")
        akun_karyawan.atur_semua(self.admin, wid, ["pos_jual", "gudang"])
        joni = get_user_model().objects.get(username=f"ops-{wid.lower()}")
        self.assertNotIn(joni, svc.penerima_boleh())          # aplikasi gudang saja: bukan PIC
        akun_karyawan.atur_semua(self.admin, wid, ["pos_jual", "pic"])
        joni = get_user_model().objects.get(pk=joni.pk)
        self.assertIn(joni, svc.penerima_boleh())             # PIC ikut menyalakan aplikasi gudang
        self.assertEqual(akun_karyawan.hak_ops_semua()[wid], ["gudang", "pic"])
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:karyawan"))
        self.assertContains(r, 'value="pic"')
        self.assertContains(r, "PIC")
        akun_karyawan.atur_semua(self.admin, wid, ["pos_jual", "gudang"])
        self.assertNotIn(get_user_model().objects.get(pk=joni.pk), svc.penerima_boleh())

    def test_po_penerima_dari_barang_manual_dan_pecah(self):
        from master.models import Rak, VarianInfo
        Rak.objects.create(kode="A1", nama="Rak 1", tempat=self.ga)                    # PIC ikut lokasi: Eva
        Rak.objects.create(kode="B1", nama="Rak B", tempat=self.ga, pic=self.gudang)   # PIC per rak
        VarianInfo.objects.filter(item_name="Palu").update(rak_id="A1")
        VarianInfo.objects.filter(item_name="Kikir").update(rak_id="B1")
        po = svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-11-01")
        self.assertIsNone(po.penerima)                          # belum ada barang
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:pembelian")), "belum ditugaskan")
        svc.tambah_baris(po, varian_id=self.info("Kikir", "N/A").id, jumlah=1, harga=500)
        self.assertEqual(po.penerima, self.gudang)
        svc.tambah_baris(po, varian_id=self.info("Palu", "Kecil").id, jumlah=2, harga=900)
        svc.tambah_baris(po, varian_id=self.info("Palu", "Besar").id, jumlah=2, harga=900)
        self.assertEqual((po.penerima, po.lokasi), (self.eva, self.ga))   # PIC dengan barang terbanyak
        # PIC lain melihat "akan datang ke areamu" tanpa supplier dan harga.
        self.client.force_login(self.gudang)
        r = self.client.get(reverse("gudang:tiket"))
        self.assertContains(r, "Akan datang ke areamu")
        self.assertContains(r, "Kikir")
        self.assertNotContains(r, "PT Sumber")
        self.assertEqual(self.client.get(reverse("gudang:datang_detail", args=[po.id])).status_code, 404)
        # Pemilik mengganti penerima (PIC sedang libur): manual, tidak berubah lagi saat baris berubah.
        svc.atur_penerima(self.admin, po, penerima=str(self.gudang.id))
        self.assertTrue(po.penerima_manual)
        svc.simpan_baris(po, {f"qty_{b.id}": "3" for b in po.baris.all()} | {f"harga_{b.id}": "900" for b in po.baris.all()})
        po.refresh_from_db()
        self.assertEqual(po.penerima, self.gudang)
        with self.assertRaises(Tolak):
            svc.atur_penerima(self.admin, po, penerima=str(self.finance.id))
        svc.atur_penerima(self.admin, po, penerima="")             # belum ditugaskan: tidak tampil di siapa pun
        self.assertIsNone(po.penerima)
        self.client.force_login(self.admin)
        self.client.post(reverse("master:pembelian_detail", args=[po.id]), {"aksi": "penerima", "penerima": "otomatis"})
        po.refresh_from_db()
        self.assertEqual((po.penerima, po.penerima_manual), (self.eva, False))
        # Pecah per PIC: Kikir pindah ke PO baru milik gudang, supplier sama.
        self.client.post(reverse("master:pembelian_detail", args=[po.id]), {"aksi": "pecah"})
        baru = po.supplier.pembelian.exclude(pk=po.pk).get()
        self.assertEqual((baru.penerima, [b.item_name for b in baru.baris.all()]), (self.gudang, ["Kikir"]))
        self.assertEqual(sorted(b.variant for b in po.baris.all()), ["Besar", "Kecil"])
        with self.assertRaises(Tolak):
            svc.pecah_per_pic(self.admin, po)
        # Hanya penerima yang melihat dan membuka PO-nya.
        self.client.force_login(self.eva)
        self.assertContains(self.client.get(reverse("gudang:tiket")), po.nomor)
        self.assertNotContains(self.client.get(reverse("gudang:tiket")), baru.nomor)
        # HP: daftar & detail PO.
        from master.tests.test_hp import masuk_hp
        masuk_hp(self.client, self.admin)
        self.assertContains(self.client.get(reverse("hp:po")), po.nomor)
        r = self.client.post(reverse("hp:po_detail", args=[baru.id]), json.dumps({"aksi": "penerima", "penerima": ""}),
                             content_type="application/json")
        self.assertTrue(r.json()["ok"])
        baru.refresh_from_db()
        self.assertIsNone(baru.penerima)

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
