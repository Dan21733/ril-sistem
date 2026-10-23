"""Uji: Stok, buka dus, opname, halaman kepala gudang."""
import json

from django.urls import reverse

from pos.models import Item

from master.layanan import inti as layanan
from master.akun.akses import PERAN, SEMUA_HAK
from master.layanan.inti import Tolak
from master.models import Pembelian, VarianInfo
from master.tests.dasar import Dasar, buat_item


class StokTest(Dasar):
    def test_tetapkan_mencatat_selisih(self):
        info = self.info("Palu", "Kecil")
        stok, selisih = layanan.sesuaikan_stok(self.admin, info, cara="tetapkan", angka="7", jenis="hitung", hak=SEMUA_HAK)
        self.assertEqual((stok, selisih), (7, -3))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 7)
        from master.models import MutasiStok
        m = MutasiStok.objects.get()
        self.assertEqual((m.stok_sebelum, m.jumlah, m.stok_sesudah, m.jenis, m.varian_id), (10, -3, 7, "hitung", info.id))

    def test_sama_dengan_sekarang_tidak_dicatat(self):
        from master.models import MutasiStok
        _, selisih = layanan.sesuaikan_stok(self.admin, self.info("Palu", "Kecil"), cara="tetapkan", angka="10", jenis="hitung", hak=SEMUA_HAK)
        self.assertEqual(selisih, 0)
        self.assertEqual(MutasiStok.objects.count(), 0)

    def test_kurang_boleh_minus(self):
        stok, _ = layanan.sesuaikan_stok(self.admin, self.info("Palu", "Kecil"), cara="kurang", angka="15", jenis="rusak", hak=SEMUA_HAK)
        self.assertEqual(stok, -5)

    def test_ditolak(self):
        info = self.info("Palu", "Kecil")
        with self.assertRaises(Tolak):
            layanan.sesuaikan_stok(self.gudang, info, cara="tetapkan", angka="1", jenis="hitung", hak=PERAN["Gudang"])
        with self.assertRaises(Tolak):
            layanan.sesuaikan_stok(self.admin, info, cara="tetapkan", angka="1", jenis="lain", hak=SEMUA_HAK)
        with self.assertRaises(Tolak):
            layanan.sesuaikan_stok(self.admin, info, cara="tetapkan", angka="-1", jenis="hitung", hak=SEMUA_HAK)
        with self.assertRaises(Tolak):
            layanan.sesuaikan_stok(self.admin, info, cara="tetapkan", angka="1", jenis="ngarang", hak=SEMUA_HAK)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)

    def test_halaman_dan_endpoint(self):
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(reverse("master:stok")).status_code, 200)
        self.assertEqual(self.client.get(reverse("master:stok_riwayat")).status_code, 200)
        self.assertNotIn("Hitungan baru", self.client.get(reverse("master:stok")).content.decode())
        info = self.info("Palu", "Kecil")
        kirim = lambda: self.client.post(reverse("master:stok_ubah"), json.dumps(
            {"id": info.id, "nilai": "4", "jenis": "hitung"}), content_type="application/json")
        self.assertEqual(kirim().status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(kirim().json(), {"ok": True, "stok": 4, "selisih": -6})
        self.assertIn("Palu", self.client.get(reverse("master:stok_riwayat")).content.decode())
        self.assertIn("Kikir", self.client.get(reverse("master:stok") + "?stok=menipis").content.decode() + "Kikir")

    def test_filter_stok(self):
        Item.objects.filter(item_name="Kikir").update(in_stock=0)
        self.client.force_login(self.admin)
        isi = self.client.get(reverse("master:stok") + "?stok=habis").content.decode()
        self.assertIn("Kikir", isi)
        self.assertNotIn("Oracle", isi)


class BukaDusTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        buat_item("Semen", "50 Kg", floor=60000, jual=65000, modal=50000, stok=10)
        buat_item("Semen", "Per Kg", floor=1500, jual=2000, modal=900, stok=20)
        buat_item("Paku", "Dus", floor=300000, jual=320000, modal=285000, stok=4)
        buat_item("Paku", "Per Kg", floor=17000, jual=19000, modal=None, stok=0)
        layanan.sinkron_info()

    def _pasangan(self, a=("Semen", "50 Kg"), b=("Semen", "Per Kg"), isi=50, tidak_tetap=False):
        from master.layanan import bukadus
        return bukadus.buat_konversi(self.admin, dari_id=self.info(*a).id, ke_id=self.info(*b).id, isi=isi,
                                     isi_tidak_tetap=tidak_tetap)

    def test_buka_memindahkan_stok_dan_modal(self):
        from master.layanan import bukadus
        from master.models import MutasiStok
        k = self._pasangan()
        hasil = bukadus.buka(self.admin, k, jumlah=2)
        sak, kg = Item.objects.get(item_name="Semen", variant="50 Kg"), Item.objects.get(item_name="Semen", variant="Per Kg")
        self.assertEqual((sak.in_stock, kg.in_stock, hasil.isi_total), (8, 120, 100))
        # Modal eceran = 50.000 / 50 = 1.000; modal rata = (20 x 900 + 100 x 1.000) / 120 = 983
        self.assertEqual((kg.cost_amount, self.info("Semen", "Per Kg").modal_rata), (1000, 983))
        self.assertEqual((sak.min_price, kg.min_price, kg.max_price), (60000, 1500, 2000))   # harga tidak disentuh
        m = list(MutasiStok.objects.order_by("id").values_list("jenis", "jumlah", "stok_sesudah"))
        self.assertEqual(m, [("buka_dus", -2, 8), ("buka_dus", 100, 120)])

    def test_isi_tidak_tetap(self):
        from master.layanan import bukadus
        k = self._pasangan(("Paku", "Dus"), ("Paku", "Per Kg"), isi=20, tidak_tetap=True)
        hasil = bukadus.buka(self.admin, k, jumlah=1, isi_total="18")
        kg = Item.objects.get(item_name="Paku", variant="Per Kg")
        self.assertEqual((kg.in_stock, hasil.isi_total, kg.cost_amount), (18, 18, 15833))   # 285.000 / 18
        with self.assertRaises(Tolak):
            bukadus.buka(self.admin, k, jumlah=1, isi_total="180")       # salah ketik
        self.assertEqual(Item.objects.get(item_name="Paku", variant="Dus").in_stock, 3)

    def test_isi_tetap_mengabaikan_isian_isi(self):
        from master.layanan import bukadus
        hasil = bukadus.buka(self.admin, self._pasangan(), jumlah=1, isi_total="7")
        self.assertEqual(hasil.isi_total, 50)

    def test_stok_kemasan_boleh_minus(self):
        from master.layanan import bukadus
        bukadus.buka(self.admin, self._pasangan(), jumlah=12)
        self.assertEqual(Item.objects.get(item_name="Semen", variant="50 Kg").in_stock, -2)

    def test_pasangan_ditolak(self):
        from master.layanan import bukadus
        k = self._pasangan()
        with self.assertRaises(Tolak):
            self._pasangan()                                               # kembar
        with self.assertRaises(Tolak):
            self._pasangan(("Semen", "Per Kg"), ("Semen", "50 Kg"))        # kebalikan
        with self.assertRaises(Tolak):
            self._pasangan(("Semen", "50 Kg"), ("Semen", "50 Kg"))         # sama
        with self.assertRaises(Tolak):
            self._pasangan(("Paku", "Dus"), ("Paku", "Per Kg"), isi=1)
        bukadus.ubah_konversi(self.admin, k, isi=40, isi_tidak_tetap=False, aktif=False)
        with self.assertRaises(Tolak):
            bukadus.buka(self.admin, k, jumlah=1)
        with self.assertRaises(Tolak):
            layanan.sesuaikan_stok(self.admin, self.info("Semen", "50 Kg"), cara="tetapkan", angka=1, jenis="buka_dus", hak=SEMUA_HAK)

    def test_lewat_layar_dan_hak(self):
        from master.models import BukaDus, Konversi
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(reverse("master:buka_dus")).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("master:buka_dus"), {"aksi": "tambah", "dari_id": self.info("Semen", "50 Kg").id,
                                                      "ke_id": self.info("Semen", "Per Kg").id, "isi": "50"})
        k = Konversi.objects.get()
        self.client.post(reverse("master:buka_dus"), {"aksi": "buka", "id": k.id, "jumlah": "1"})
        self.assertEqual(BukaDus.objects.get().isi_total, 50)
        isi = self.client.get(reverse("master:buka_dus")).content.decode()
        self.assertIn("Riwayat buka dus", isi)
        self.assertEqual(self.client.get(reverse("master:stok_riwayat") + "?jenis=buka_dus").status_code, 200)


class OpnameTest(Dasar):
    def _hitung(self, **isi):
        from master.layanan import opname
        return opname.simpan_hitungan(self.gudang, {f"hitung_{self.info(*k.split('__')).id}": v for k, v in isi.items()})

    def test_stok_tidak_berubah_sampai_acc(self):
        from master.layanan import opname
        from master.models import MutasiStok
        self._hitung(Palu__Kecil="7")
        op = opname.ajukan(self.gudang, "rak depan")
        self.assertEqual((op.status, op.baris.get().stok_saat_hitung), ("diajukan", 10))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        self.assertEqual(MutasiStok.objects.count(), 0)

    def test_acc_menerapkan_selisih_bukan_angka_hitungan(self):
        from master.layanan import opname
        from master.models import MutasiStok
        self._hitung(Palu__Kecil="7")            # sistem 10, fisik 7: selisih -3
        op = opname.ajukan(self.gudang)
        # Sesudah dihitung, kasir menjual 2. Stok sistem jadi 8; fisik sebenarnya 5.
        Item.objects.filter(item_name="Palu", variant="Kecil").update(in_stock=8)
        b = opname.rincian(op)[0]
        self.assertEqual((b.selisih, b.stok_sekarang, b.stok_jadi, b.nilai_selisih), (-3, 8, 5, -2400))
        opname.acc(self.admin, op, [b.id], hak=SEMUA_HAK)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 5)
        m = MutasiStok.objects.get()
        self.assertEqual((m.jenis, m.jumlah, m.stok_sebelum, m.stok_sesudah, m.nama_pengguna), ("hitung", -3, 8, 5, "admin"))
        self.assertIn("oleh gudang", m.catatan)
        op.refresh_from_db(); self.assertEqual(op.status, "disetujui")
        with self.assertRaises(Tolak):
            opname.acc(self.admin, op, [b.id], hak=SEMUA_HAK)

    def test_acc_hanya_baris_yang_dicentang(self):
        from master.layanan import opname
        self._hitung(Palu__Kecil="7", Palu__Besar="12")
        op = opname.ajukan(self.gudang)
        kecil = op.baris.get(variant="Kecil")
        opname.acc(self.admin, op, [str(kecil.id)], hak=SEMUA_HAK)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 7)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Besar").in_stock, 10)
        self.assertEqual(sorted(op.baris.values_list("variant", "diterapkan")), [("Besar", False), ("Kecil", True)])

    def test_hitung_ulang_menimpa_dan_draf_per_orang(self):
        from master.layanan import opname
        from master.models import Opname
        self._hitung(Palu__Kecil="7")
        self._hitung(Palu__Kecil="9")
        self.assertEqual(Opname.objects.count(), 1)
        self.assertEqual(opname.draf(self.gudang).baris.get().hitung, 9)
        opname.ajukan(self.gudang)
        self._hitung(Palu__Besar="1")
        self.assertEqual(Opname.objects.count(), 2)

    def test_tolak_wajib_alasan_dan_tidak_mengubah_stok(self):
        from master.layanan import opname
        self._hitung(Palu__Kecil="7")
        op = opname.ajukan(self.gudang)
        with self.assertRaises(Tolak):
            opname.tolak(self.admin, op, " ", hak=SEMUA_HAK)
        opname.tolak(self.admin, op, "hitung ulang", hak=SEMUA_HAK)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        self.client.force_login(self.gudang)
        self.assertContains(self.client.get(reverse("gudang:beranda")), "hitung ulang")

    def test_gudang_tidak_bisa_acc_dan_ajukan_kosong_ditolak(self):
        from master.layanan import opname
        with self.assertRaises(Tolak):
            opname.ajukan(self.gudang)
        self._hitung(Palu__Kecil="7")
        op = opname.ajukan(self.gudang)
        with self.assertRaises(Tolak):
            opname.acc(self.gudang, op, [op.baris.get().id], hak=PERAN["Gudang"])
        self.client.force_login(self.gudang)
        self.assertEqual(self.client.post(reverse("master:opname_detail", args=[op.id]), {"aksi": "acc"}).status_code, 403)

    def test_lewat_layar(self):
        from master.models import Opname
        self.client.force_login(self.gudang)
        id_kecil = self.info("Palu", "Kecil").id
        r = self.client.post(reverse("gudang:hitung") + "?q=palu", {"aksi": "simpan", f"hitung_{id_kecil}": "6", "hitung_999999": ""}, follow=True)
        self.assertContains(r, "1 hitungan tersimpan")
        self.assertContains(r, "sudah dihitung: 6")
        self.client.post(reverse("gudang:hitung"), {"aksi": "ajukan", "catatan": "rak A"})
        op = Opname.objects.get()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:dashboard")), "Opname menunggu ACC")
        isi = self.client.get(reverse("master:opname_detail", args=[op.id])).content.decode()
        self.assertIn("rak A", isi)
        r = self.client.post(reverse("master:opname_detail", args=[op.id]), {"aksi": "acc", "pilih": [op.baris.get().id]}, follow=True)
        self.assertContains(r, "Opname di-ACC")
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 6)


class GudangTest(Dasar):
    """Halaman kepala gudang: tidak masuk master, tidak melihat harga/modal/stok sistem."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.models import Supplier
        cls.supplier = Supplier.objects.create(nama="PT Rahasia Jaya")
        # Angka yang mudah dikenali kalau sampai bocor ke halaman gudang.
        Item.objects.filter(item_name="Palu", variant="Kecil").update(
            cost_amount=987654, min_price=876543, max_price=765432, in_stock=4321)
        VarianInfo.objects.filter(item_name="Palu", variant="Kecil").update(modal_rata=987111, rak_id="TK-01")

    RAHASIA = ("987654", "987.654", "876543", "876.543", "765432", "765.432", "4321", "4.321", "987111", "987.111", "654321", "654.321",
               "Rahasia Jaya")

    def _po(self):
        from master.layanan import pembelian as svc
        po = svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-10-01")
        svc.tambah_baris(po, varian_id=self.info("Palu", "Kecil").id, jumlah=10, harga=654321)
        svc.tambah_baris(po, varian_id=self.info("Palu", "Besar").id, jumlah=5, harga=2000)
        return po

    def _bersih(self, respons):
        isi = respons.content.decode()
        for angka in self.RAHASIA:
            self.assertNotIn(angka, isi)
        return isi

    def test_gudang_tidak_bisa_masuk_master(self):
        self.client.force_login(self.gudang)
        for nama in ("dashboard", "barang", "stok", "pembelian", "nota", "laporan", "opname"):
            self.assertRedirects(self.client.get(reverse(f"master:{nama}")), reverse("gudang:beranda"))
        self.assertEqual(self.client.post(reverse("master:stok_ubah"), "{}", content_type="application/json").status_code, 403)

    def test_finance_tidak_bisa_masuk_gudang_admin_bisa(self):
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(reverse("gudang:beranda")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("gudang:beranda")).status_code, 200)

    def test_tidak_ada_harga_modal_atau_stok_di_halaman_gudang(self):
        from master.layanan import bukadus
        po = self._po()
        buat_item("Semen", "Sak", modal=987654, stok=4321)
        buat_item("Semen", "Kg", modal=987654, stok=4321)
        layanan.sinkron_info()
        bukadus.buat_konversi(self.admin, dari_id=self.info("Semen", "Sak").id, ke_id=self.info("Semen", "Kg").id, isi=50)
        self.client.force_login(self.gudang)
        self._bersih(self.client.get(reverse("gudang:beranda")))
        self._bersih(self.client.get(reverse("gudang:datang")))
        isi = self._bersih(self.client.get(reverse("gudang:datang_detail", args=[po.id])))
        self.assertIn("Palu / Kecil", isi)
        self.assertIn("TK-01", isi)
        isi = self._bersih(self.client.get(reverse("gudang:hitung") + "?q=palu"))
        self.assertIn("Palu / Kecil", isi)
        self._bersih(self.client.get(reverse("gudang:hitung") + "?rak=TK-01"))
        self.assertIn("Semen / Sak", self._bersih(self.client.get(reverse("gudang:buka_dus"))))

    def test_gudang_catat_datang_lalu_pemilik_lengkapi_dan_acc(self):
        from master.layanan import pembelian as svc
        po = self._po()
        kecil, besar = po.baris.order_by("id")
        self.client.force_login(self.gudang)
        url = reverse("gudang:datang_detail", args=[po.id])
        # Beda dari pesanan tanpa catatan: ditolak, tidak ada yang berubah.
        r = self.client.post(url, {f"terima_{kecil.id}": "8", f"terima_{besar.id}": "5"})
        self.assertContains(r, "Isi catatannya")
        po.refresh_from_db(); self.assertEqual(po.status, "dibuat")
        # Kosong tidak dianggap nol.
        r = self.client.post(url, {f"terima_{kecil.id}": "8", f"terima_{besar.id}": "", "catatan_terima": "x"})
        self.assertContains(r, "belum diisi")
        r = self.client.post(url, {f"terima_{kecil.id}": "8", f"terima_{besar.id}": "5", "catatan_terima": "kurang 2",
                                   "surat_jalan": "SJ-77", f"harga_{kecil.id}": "1"})
        self.assertRedirects(r, reverse("gudang:datang"))
        po.refresh_from_db(); kecil.refresh_from_db()
        self.assertEqual((po.status, po.diterima_oleh, po.catatan_terima), ("menunggu", "gudang", "Surat jalan SJ-77. kurang 2"))
        # Gudang tidak bisa mengubah harga walau mengirim kolomnya.
        self.assertEqual((kecil.jumlah_terima, kecil.harga), (8, 654321))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 4321)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.admin)
        r = self.client.post(reverse("master:pembelian_detail", args=[po.id]), {
            "aksi": "faktur", "mode_ppn": "tanpa", f"harga_{kecil.id}": "1.000", f"harga_{besar.id}": "2.000",
            "nomor_faktur": "F-1", "ongkir": "900", "ongkir_tagihan": "terpisah"}, follow=True)
        self.assertContains(r, "Data faktur tersimpan")
        po = svc.acc(self.admin, Pembelian.objects.get(pk=po.id))
        self.assertEqual((po.total, po.nomor_faktur, po.ongkir), (18000, "F-1", 900))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 4329)

    def test_gudang_buka_dus(self):
        from master.layanan import bukadus
        from master.models import BukaDus
        buat_item("Semen", "Sak", modal=50000, stok=10)
        buat_item("Semen", "Kg", modal=1000, stok=0)
        layanan.sinkron_info()
        k = bukadus.buat_konversi(self.admin, dari_id=self.info("Semen", "Sak").id, ke_id=self.info("Semen", "Kg").id, isi=50)
        self.client.force_login(self.gudang)
        r = self.client.post(reverse("gudang:buka_dus"), {"id": k.id, "jumlah": "2"}, follow=True)
        self.assertContains(r, "dibuka menjadi 100")
        self.assertEqual(Item.objects.get(item_name="Semen", variant="Kg").in_stock, 100)
        self.assertEqual(BukaDus.objects.get().oleh, "gudang")
        self.assertNotIn("50.000", r.content.decode())
