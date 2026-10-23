"""Uji: Toko, transfer antar toko, cadangan."""
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from pos.models import Item

from master.akun.akses import SEMUA_HAK
from master.layanan.inti import Tolak
from master.models import Pembelian
from master.tests.dasar import Dasar, PENYIMPANAN_UJI


class TokoTest(Dasar):
    """Banyak toko: toko pertama dibuat dari migrasi dan memegang stok kasir; toko lain belum punya kasir."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.models import Supplier, Toko
        cls.t1 = Toko.objects.get(stok_di_kasir=True)
        cls.t2 = Toko.objects.create(kode="T2", nama="Toko Dua", atas_nama="Ibu")
        cls.supplier = Supplier.objects.create(nama="PT Sumber")

    def test_toko_pertama_dari_migrasi(self):
        self.assertEqual((self.t1.kode, self.t1.aktif), ("T1", True))

    def test_tambah_toko_tanpa_batas_dan_kode_unik(self):
        from master.layanan import toko as svc
        from master.models import Toko
        for i in range(3, 6):
            svc.simpan(self.admin, {"kode": f"t{i}", "nama": f"Toko {i}"})
        self.assertEqual(Toko.objects.count(), 5)
        self.assertEqual(Toko.objects.get(nama="Toko 5").kode, "T5")
        with self.assertRaises(Tolak):
            svc.simpan(self.admin, {"kode": "T2", "nama": "Kembar"})

    def test_hanya_satu_toko_pegang_stok_kasir(self):
        from master.layanan import toko as svc
        from master.models import Toko
        svc.jadikan_stok_kasir(self.admin, self.t2)
        self.assertEqual(list(Toko.objects.filter(stok_di_kasir=True)), [self.t2])
        with self.assertRaises(Tolak):
            self.t2.refresh_from_db(); svc.ubah_aktif(self.admin, self.t2)

    def test_po_toko_tanpa_kasir_hanya_hutang(self):
        from master.layanan import pembelian as svc
        from master.models import MutasiStok
        po = svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-10-01", toko=self.t2.id)
        svc.tambah_baris(po, varian_id=self.info("Palu", "Kecil").id, jumlah=5, harga=1000)
        data = {"mode_ppn": "tanpa", "tanggal_terima": "2026-10-02"}
        for b in po.baris.all():
            data[f"terima_{b.id}"], data[f"harga_{b.id}"] = 5, 1000
        svc.catat_datang(self.admin, po, data)
        po = svc.acc(self.admin, Pembelian.objects.get(pk=po.pk))
        self.assertEqual((po.toko, po.total, po.sisa), (self.t2, 5000, 5000))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        self.assertEqual(MutasiStok.objects.count(), 0)
        self.assertEqual(svc.ringkasan_hutang(toko=self.t2)["total"], 5000)
        self.assertEqual(svc.ringkasan_hutang(toko=self.t1)["total"], 0)

    def test_faktur_per_toko(self):
        from master.layanan import faktur
        data = {"supplier": "PT Sumber", "nomor_faktur": "A-1", "tanggal_faktur": "2026-09-01", "total": "1000", "keterangan": "x"}
        faktur.buat(self.admin, {**data, "toko": self.t1.id})
        faktur.buat(self.admin, {**data, "toko": self.t2.id})           # nomor sama, toko beda: boleh
        with self.assertRaises(Tolak):
            faktur.buat(self.admin, {**data, "toko": self.t2.id})

    def _transfer(self, dari, ke, qty=4):
        from master.layanan import transfer
        tr = transfer.buat(self.admin, dari_id=dari.id, ke_id=ke.id, tanggal="2026-10-08")
        transfer.tambah_baris(tr, varian_id=self.info("Palu", "Kecil").id, jumlah=qty)
        return tr

    def test_transfer_keluar_dari_toko_kasir(self):
        from master.layanan import transfer
        from master.models import MutasiStok
        tr = self._transfer(self.t1, self.t2)
        self.assertEqual(tr.nomor, "TR-2610-0001")
        transfer.kirim(self.admin, tr)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 6)
        m = MutasiStok.objects.get()
        self.assertEqual((m.jenis, m.jumlah), ("transfer_keluar", -4))
        self.assertEqual(transfer.saldo(), [])                       # belum diterima, belum masuk saldo
        b = tr.baris.get()
        with self.assertRaises(Tolak):
            transfer.terima(self.admin, tr, {f"terima_{b.id}": "3"})  # beda tanpa catatan
        transfer.terima(self.admin, tr, {f"terima_{b.id}": "3", "catatan_terima": "1 pecah"})
        tr.refresh_from_db()
        self.assertEqual((tr.status, tr.nilai), ("diterima", 2400))   # 3 x modal 800
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 6)   # toko 2 tanpa kasir
        s = transfer.saldo()
        self.assertEqual((s[0]["penghutang"], s[0]["pemberi"], s[0]["jumlah"]), (self.t2, self.t1, 2400))

    def test_transfer_masuk_ke_toko_kasir_dan_saldo_bersih(self):
        from master.layanan import transfer
        a = self._transfer(self.t1, self.t2, 5)
        transfer.kirim(self.admin, a)
        transfer.terima(self.admin, a, {f"terima_{a.baris.get().id}": "5"})            # T2 hutang 4.000 ke T1
        b = self._transfer(self.t2, self.t1, 2)
        transfer.kirim(self.admin, b)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 5)
        transfer.terima(self.admin, b, {f"terima_{b.baris.get().id}": "2"})            # T1 hutang 1.600 ke T2
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 7)
        s = transfer.saldo()
        self.assertEqual((len(s), s[0]["penghutang"], s[0]["jumlah"]), (1, self.t2, 2400))
        with self.assertRaises(Tolak):
            transfer.lunasi(self.admin, pembayar_id=self.t2.id, penerima_id=self.t1.id, tanggal="2026-10-09",
                            jumlah="2.401", cara="tunai")
        transfer.lunasi(self.admin, pembayar_id=self.t2.id, penerima_id=self.t1.id, tanggal="2026-10-09",
                        jumlah="2.400", cara="tunai")
        self.assertEqual(transfer.saldo(), [])

    def test_batal_setelah_kirim_stok_kembali(self):
        from master.layanan import transfer
        tr = self._transfer(self.t1, self.t2)
        transfer.kirim(self.admin, tr)
        with self.assertRaises(Tolak):
            transfer.batal(self.admin, tr, "")
        transfer.batal(self.admin, tr, "salah toko")
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        tr.refresh_from_db()
        with self.assertRaises(Tolak):
            transfer.kirim(self.admin, tr)

    def test_transfer_ke_toko_sendiri_ditolak(self):
        from master.layanan import transfer
        with self.assertRaises(Tolak):
            transfer.buat(self.admin, dari_id=self.t1.id, ke_id=self.t1.id, tanggal="2026-10-08")

    def test_opname_toko_tanpa_kasir_tidak_mengubah_stok(self):
        from master.layanan import opname
        opname.simpan_hitungan(self.gudang, {f"hitung_{self.info('Palu', 'Kecil').id}": "33"}, toko=self.t2)
        op = opname.ajukan(self.gudang, toko=self.t2)
        self.assertEqual((op.toko, op.baris.get().stok_saat_hitung), (self.t2, 0))
        opname.acc(self.admin, op, [op.baris.get().id], hak=SEMUA_HAK)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        op.refresh_from_db(); self.assertEqual(op.status, "disetujui")

    def test_halaman(self):
        from master.models import Transfer
        self.client.force_login(self.admin)
        for nama in ("toko", "transfer", "transfer_baru", "saldo_toko", "hutang", "faktur", "pembelian", "opname"):
            self.assertEqual(self.client.get(reverse(f"master:{nama}")).status_code, 200, nama)
        r = self.client.post(reverse("master:transfer_baru"), {"dari": self.t1.id, "ke": self.t2.id, "tanggal": "2026-10-08"}, follow=True)
        tr = Transfer.objects.get()
        self.client.post(reverse("master:transfer_detail", args=[tr.id]), {"aksi": "tambah_baris", "varian_id": self.info("Palu", "Kecil").id, "jumlah": "2"})
        self.assertEqual(self.client.get(reverse("master:transfer_cetak", args=[tr.id])).status_code, 302)   # draf belum bisa cetak
        self.client.post(reverse("master:transfer_detail", args=[tr.id]), {"aksi": "kirim"})
        isi = self.client.get(reverse("master:transfer_cetak", args=[tr.id])).content.decode()
        self.assertIn("SURAT JALAN", isi)
        self.assertNotIn("800", isi)                                        # surat jalan tanpa harga/modal
        self.assertContains(self.client.get(reverse("master:pembelian_baru")), "Toko Dua")

    def test_gudang_pilih_toko(self):
        from master.layanan import pembelian as svc
        svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-10-01", toko=self.t2.id)
        po = Pembelian.objects.get()
        svc.tambah_baris(po, varian_id=self.info("Palu", "Kecil").id, jumlah=1, harga=1)
        self.client.force_login(self.gudang)
        self.assertNotContains(self.client.get(reverse("gudang:datang")), po.nomor)
        self.assertContains(self.client.get(reverse("gudang:datang") + f"?toko={self.t2.id}"), po.nomor)
        self.assertContains(self.client.get(reverse("gudang:buka_dus")), "belum punya kasir")   # pilihan diingat


@override_settings(STORAGES=PENYIMPANAN_UJI)
class CadanganTest(Dasar):
    def setUp(self):
        import tempfile
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.folder, ignore_errors=True)

    def _isi(self):
        from master.layanan import faktur
        from master.layanan import pembelian as svc
        from master.models import Supplier
        sup = Supplier.objects.create(nama="PT Rahasia")
        f = faktur.buat(self.admin, {"supplier": "PT Rahasia", "nomor_faktur": "F-1", "tanggal_faktur": "2026-09-01",
                                     "total": "500.000", "keterangan": "Semen"},
                        [SimpleUploadedFile("a.jpg", b"\xff\xd8\xffisi-foto", content_type="image/jpeg")])
        faktur.bayar(self.admin, f, tanggal="2026-09-02", jumlah="100.000", cara="tunai")
        po = svc.buat(self.admin, supplier_id=sup.id, tanggal_pesan="2026-09-01")
        svc.tambah_baris(po, varian_id=self.info("Palu", "Kecil").id, jumlah=3, harga=900)
        return f

    def test_cadangan_lalu_pulihkan_ke_database_kosong(self):
        from django.core.files.storage import default_storage
        from django.core.management import call_command
        from master.management.commands._cadangan import pulihkan
        from master import models as m
        f = self._isi()
        kunci = f.foto.get().object_key
        call_command("master_cadangan", ke=self.folder, stdout=StringIO())
        import os
        berkas = [n for n in os.listdir(self.folder) if n.endswith(".zip")]
        self.assertEqual(len(berkas), 1)
        # Ke database yang sudah berisi: ditolak.
        with self.assertRaises(ValueError):
            pulihkan(os.path.join(self.folder, berkas[0]))
        # Kosongkan semua data master, hapus fotonya, lalu pulihkan.
        for model in (m.BayarFaktur, m.FotoFaktur, m.FakturManual, m.PembelianBaris, m.Pembelian, m.Log,
                      m.MutasiStok, m.Supplier, m.VarianInfo, m.ItemInfo, m.Rak):
            model.objects.all().delete()
        default_storage.delete(kunci)
        pulihkan(os.path.join(self.folder, berkas[0]))
        g = m.FakturManual.objects.get()
        self.assertEqual((g.nomor_faktur, g.total, g.terbayar, g.supplier.nama), ("F-1", 500000, 100000, "PT Rahasia"))
        self.assertEqual(m.Pembelian.objects.get().baris.get().varian.item_name, "Palu")
        self.assertEqual(default_storage.open(kunci).read(), b"\xff\xd8\xffisi-foto")
        self.assertTrue(m.Log.objects.filter(pengguna=self.admin).exists())
        # Id baru tidak bentrok dengan id hasil pulihan (penting di PostgreSQL).
        baru = m.Supplier.objects.create(nama="Setelah pulih")
        self.assertGreater(baru.id, g.supplier.id)

    def test_simpan_hanya_n_terakhir(self):
        import os
        from master.management.commands._cadangan import buat
        for i in range(4):
            p, _, _ = buat(self.folder, simpan=2)
            os.utime(p)
            import time; time.sleep(1.05)
        self.assertEqual(len([n for n in os.listdir(self.folder) if n.endswith(".zip")]), 2)

    def test_jika_diatur_tidak_gagal_tanpa_folder(self):
        from django.core.management import call_command
        keluar = StringIO()
        with self.settings():
            os_env = __import__("os").environ
            lama = os_env.pop("MASTER_CADANGAN_KE", None)
            try:
                call_command("master_cadangan", jika_diatur=True, stdout=keluar)
            finally:
                if lama is not None:
                    os_env["MASTER_CADANGAN_KE"] = lama
        self.assertIn("belum diatur", keluar.getvalue())

    def test_kosongkan_data_percobaan(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from master import models as m
        self._isi()
        with self.assertRaises(CommandError):
            call_command("master_kosongkan", stdout=StringIO())
        import os
        os.environ["MASTER_CADANGAN_KE"] = self.folder
        try:
            call_command("master_kosongkan", yakin=True, stdout=StringIO())
        finally:
            del os.environ["MASTER_CADANGAN_KE"]
        self.assertEqual((m.FakturManual.objects.count(), m.Pembelian.objects.count(), m.Log.objects.count()), (0, 0, 0))
        self.assertEqual(m.Supplier.objects.count(), 1)
        self.assertTrue(m.VarianInfo.objects.exists())
        self.assertEqual(len([n for n in os.listdir(self.folder) if n.endswith(".zip")]), 1)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
