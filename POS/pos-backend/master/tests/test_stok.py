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

    def _tugas(self, **lain):
        from datetime import timedelta
        from django.utils import timezone
        from master.layanan import tiket as svc_tiket
        from master.models import Lokasi
        return svc_tiket.buka(self.admin, untuk=self.gudang, tujuan="hitung palu", jenis="hitung", hak=SEMUA_HAK,
                              batas=timezone.now() + timedelta(hours=2),
                              lokasi=lain.pop("lokasi", Lokasi.objects.get(jenis="toko").id), **lain)

    def test_hitung_hanya_lewat_tugas(self):
        self.client.force_login(self.gudang)
        r = self.client.get(reverse("gudang:hitung"), follow=True)
        self.assertContains(r, "lewat tugas dari pemilik")
        self.assertEqual(self.client.post(reverse("gudang:hitung"), {"aksi": "simpan"}).status_code, 302)
        from master.models import Opname
        self.assertFalse(Opname.objects.exists())

    def test_buka_tugas_hitung_perlu_hak_dan_lokasi(self):
        from datetime import timedelta
        from django.utils import timezone
        from master.layanan import tiket as svc_tiket
        with self.assertRaises(Tolak):
            self._tugas(lokasi=None)
        with self.assertRaises(Tolak):   # tanpa hak opname
            svc_tiket.buka(self.gudang, untuk=self.gudang, tujuan="x", jenis="hitung", hak=PERAN["Gudang"] | {"tiket"},
                           batas=timezone.now() + timedelta(hours=1), lokasi=1)
        with self.assertRaises(Tolak):   # akun tanpa hak hitung
            svc_tiket.buka(self.admin, untuk=self.finance, tujuan="x", jenis="hitung", hak=SEMUA_HAK,
                           batas=timezone.now() + timedelta(hours=1), lokasi=1)

    def test_lewat_tugas_sampai_acc_menutup_tugas(self):
        from master.models import Opname
        Item.objects.filter(item_name="Palu").update(category="Perkakas")
        t = self._tugas(kategori=["Perkakas"])
        self.client.force_login(self.gudang)
        r = self.client.get(reverse("gudang:tiket"))
        self.assertContains(r, "hitung palu")
        self.assertContains(r, "kategori Perkakas")
        url = reverse("gudang:tiket_hitung", args=[t.id])
        id_kecil = self.info("Palu", "Kecil").id
        r = self.client.get(url)
        self.assertContains(r, "Palu / Kecil")
        self.assertNotContains(r, "in_stock")
        r = self.client.post(url, {"aksi": "simpan", f"hitung_{id_kecil}": "6", "hitung_999999": ""}, follow=True)
        self.assertContains(r, "1 hitungan tersimpan")
        self.assertContains(r, "sudah dihitung: 6")
        self.assertNotContains(r, ">10<")     # stok sistem tidak tampil
        self.client.post(url, {"aksi": "kirim", "catatan": "rak A"})
        t.refresh_from_db()
        self.assertEqual(t.status, "dikirim")
        op = Opname.objects.get()
        self.assertEqual((op.tiket_id, op.status), (t.id, "diajukan"))
        # setelah dikirim tidak bisa diubah
        self.client.post(url, {"aksi": "simpan", f"hitung_{id_kecil}": "1"})
        self.assertEqual(op.baris.get().hitung, 6)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:dashboard")), "Opname menunggu ACC")
        self.assertIn("rak A", self.client.get(reverse("master:opname_detail", args=[op.id])).content.decode())
        # halaman tiket di master meneruskan ke opname
        self.assertRedirects(self.client.get(reverse("master:tiket_detail", args=[t.id])),
                             reverse("master:opname_detail", args=[op.id]))
        r = self.client.post(reverse("master:opname_detail", args=[op.id]), {"aksi": "acc", "pilih": [op.baris.get().id]}, follow=True)
        self.assertContains(r, "Opname di-ACC")
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 6)
        t.refresh_from_db()
        self.assertEqual(t.status, "selesai")

    def test_barang_di_luar_lingkup_ditolak_dan_tolak_menutup_tugas(self):
        from master.layanan import opname
        from master.models import Rak
        Rak.objects.get_or_create(kode="TK-09", defaults={"nama": "Rak sembilan"})
        VarianInfo.objects.filter(item_name="Palu", variant="Kecil").update(rak_id="TK-09")
        t = self._tugas(rak=["TK-09"])
        besar = self.info("Palu", "Besar").id
        with self.assertRaises(Tolak):
            opname.simpan_hitungan(self.gudang, {f"hitung_{besar}": "3"}, tiket=t,
                                   boleh=set(opname.lingkup_tugas(t).values_list("id", flat=True)))
        opname.simpan_hitungan(self.gudang, {f"hitung_{self.info('Palu', 'Kecil').id}": "8"}, tiket=t,
                               toko=opname.t_toko(t), lokasi=opname.lokasi_tugas(t),
                               boleh=set(opname.lingkup_tugas(t).values_list("id", flat=True)))
        op = opname.kirim_tugas(self.gudang, t)
        opname.tolak(self.admin, op, "hitung ulang besok", hak=SEMUA_HAK)
        t.refresh_from_db()
        self.assertEqual((t.status, t.catatan_putusan), ("ditolak", "hitung ulang besok"))
        self.client.force_login(self.gudang)
        r = self.client.get(reverse("gudang:tiket"))
        self.assertContains(r, "Sebelumnya")
        self.assertContains(r, "hitung ulang besok")
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)

    def test_lewat_batas_diteruskan_pemilik(self):
        from datetime import timedelta
        from django.utils import timezone
        from master.layanan import opname
        from master.models import Tiket
        t = self._tugas()
        opname.simpan_hitungan(self.gudang, {f"hitung_{self.info('Palu', 'Kecil').id}": "8"}, tiket=t,
                               toko=opname.t_toko(t), lokasi=opname.lokasi_tugas(t))
        Tiket.objects.filter(pk=t.id).update(batas=timezone.now() - timedelta(minutes=1))
        t.refresh_from_db()
        with self.assertRaises(Tolak):
            opname.kirim_tugas(self.gudang, t)
        with self.assertRaises(Tolak):
            opname.teruskan_tugas(self.gudang, t, hak=PERAN["Gudang"])
        op = opname.teruskan_tugas(self.admin, t, hak=SEMUA_HAK)
        self.assertEqual(op.status, "diajukan")
        opname.acc(self.admin, op, [op.baris.get().id], hak=SEMUA_HAK)
        t.refresh_from_db()
        self.assertEqual(t.status, "selesai")


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
        from datetime import timedelta
        from django.utils import timezone
        from master.layanan import tiket as svc_tiket
        from master.models import Lokasi
        t = svc_tiket.buka(self.admin, untuk=self.gudang, tujuan="hitung", jenis="hitung", hak=SEMUA_HAK,
                           batas=timezone.now() + timedelta(hours=1), lokasi=Lokasi.objects.get(jenis="toko").id)
        self.client.force_login(self.gudang)
        isi = self._bersih(self.client.get(reverse("gudang:beranda")))
        self.assertIn(po.nomor, isi)
        isi = self._bersih(self.client.get(reverse("gudang:datang_detail", args=[po.id])))
        self.assertIn("Palu / Kecil", isi)
        self.assertIn("TK-01", isi)
        self.assertNotIn('value="10"', isi)       # jumlah pesanan tidak ditampilkan (terima buta)
        isi = self._bersih(self.client.get(reverse("gudang:tiket_hitung", args=[t.id]) + "?q=palu"))
        self.assertIn("Palu / Kecil", isi)
        self._bersih(self.client.get(reverse("gudang:tiket_hitung", args=[t.id]) + "?rak=TK-01"))
        self.assertIn("Semen / Sak", self._bersih(self.client.get(reverse("gudang:buka_dus"))))

    def test_gudang_catat_datang_lalu_pemilik_lengkapi_dan_acc(self):
        from master.layanan import pembelian as svc
        po = self._po()
        kecil, besar = po.baris.order_by("id")
        self.client.force_login(self.gudang)
        url = reverse("gudang:datang_detail", args=[po.id])
        # Kosong tidak dianggap nol.
        r = self.client.post(url, {f"terima_{kecil.id}": "8", f"terima_{besar.id}": ""})
        self.assertContains(r, "belum diisi")
        po.refresh_from_db(); self.assertEqual(po.status, "dibuat")
        # Beda dari pesanan: diminta hitung ulang tanpa diberi tahu angka seharusnya.
        r = self.client.post(url, {f"terima_{kecil.id}": "8", f"terima_{besar.id}": "5", "surat_jalan": "SJ-77",
                                   f"harga_{kecil.id}": "1"}, follow=True)
        self.assertContains(r, "dihitung ulang")
        isi = self._bersih(r)
        self.assertIn("Palu / Kecil", isi)
        self.assertNotIn("Palu / Besar", isi)      # yang sudah cocok tidak dihitung ulang
        po.refresh_from_db()
        self.assertEqual((po.status, po.hitung_ulang), ("dibuat", [kecil.id]))
        # Hitung ulang: catatan wajib.
        r = self.client.post(url, {f"terima_{kecil.id}": "9"})
        self.assertContains(r, "Isi catatan")
        r = self.client.post(url, {f"terima_{kecil.id}": "9", "catatan_terima": "1 dus sobek"})
        self.assertRedirects(r, reverse("gudang:tiket"))
        po.refresh_from_db(); kecil.refresh_from_db()
        self.assertEqual((po.status, po.diterima_oleh, po.hitung_ulang), ("menunggu", "gudang", []))
        self.assertEqual(po.catatan_terima, "Surat jalan SJ-77. Hitung ulang: 1 dus sobek")
        # Gudang tidak bisa mengubah harga walau mengirim kolomnya.
        self.assertEqual((kecil.jumlah_hitung1, kecil.jumlah_terima, kecil.harga), (8, 9, 654321))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 4321)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:pembelian_detail", args=[po.id]))
        self.assertContains(r, "Hitungan 1")
        r = self.client.post(reverse("master:pembelian_detail", args=[po.id]), {
            "aksi": "faktur", "mode_ppn": "tanpa", f"harga_{kecil.id}": "1.000", f"harga_{besar.id}": "2.000",
            "nomor_faktur": "F-1", "ongkir": "900", "ongkir_tagihan": "terpisah"}, follow=True)
        self.assertContains(r, "Data faktur tersimpan")
        # Datang 9 dari 10: pemilik wajib memilih sisanya ditunggu atau batal.
        r = self.client.post(reverse("master:pembelian_detail", args=[po.id]), {"aksi": "acc"}, follow=True)
        self.assertContains(r, "Pilih: sisanya ditunggu")
        r = self.client.post(reverse("master:pembelian_detail", args=[po.id]), {"aksi": "acc", "sisa": "tunggu"}, follow=True)
        po = Pembelian.objects.get(pk=po.id)
        self.assertEqual((po.status, po.total, po.nomor_faktur, po.ongkir), ("diterima", 19000, "F-1", 900))
        lanjut = po.lanjutan.get()
        self.assertEqual((lanjut.status, lanjut.supplier_id, lanjut.catatan), ("dibuat", self.supplier.id, f"Sisa dari {po.nomor}"))
        self.assertEqual([(b.item_name, b.variant, b.jumlah_pesan, b.harga) for b in lanjut.baris.all()],
                         [("Palu", "Kecil", 1, 1000)])
        self.assertContains(r, lanjut.nomor)
        # PO lanjutan muncul di tugas gudang
        self.client.force_login(self.gudang)
        self.assertContains(self.client.get(reverse("gudang:tiket")), lanjut.nomor)

    def test_barang_di_luar_pesanan(self):
        from master.layanan import pembelian as svc
        po = self._po()
        kecil, besar = po.baris.order_by("id")
        buat_item("Semen", "Sak", modal=0, stok=0)   # belum pernah dibeli: harga 0, pemilik diingatkan
        layanan.sinkron_info()
        info = self.info("Semen", "Sak")
        self.client.force_login(self.gudang)
        url = reverse("gudang:datang_detail", args=[po.id])
        self.assertContains(self.client.get(url + "?q=semen"), "Semen / Sak")
        self.client.post(url, {"aksi": "tambah_lain", "varian_id": info.id, "jumlah": "3"})
        lain = po.baris.get(di_luar_pesanan=True)
        self.assertEqual((lain.jumlah_pesan, lain.jumlah_terima), (0, 3))
        r = self.client.post(url, {"aksi": "tambah_lain", "varian_id": self.info("Palu", "Kecil").id, "jumlah": "1"}, follow=True)
        self.assertContains(r, "sudah ada di daftar pesanan")
        # barang di luar pesanan tidak memicu hitung ulang
        r = self.client.post(url, {f"terima_{kecil.id}": "10", f"terima_{besar.id}": "5", f"terima_{lain.id}": "3"})
        self.assertRedirects(r, reverse("gudang:tiket"))
        po.refresh_from_db()
        self.assertEqual(po.status, "menunggu")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:pembelian_detail", args=[po.id]))
        self.assertContains(r, "tidak dipesan")
        self.assertContains(r, "Harga masih 0")
        po = svc.acc(self.admin, po)
        self.assertEqual(Item.objects.get(item_name="Semen", variant="Sak").in_stock, 3)
        self.assertFalse(po.lanjutan.exists())


class TiketHitungMasterTest(Dasar):
    def test_pemilik_buka_tugas_hitung_dari_halaman_tiket(self):
        from master.models import Lokasi, Tiket
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:tiket_baru"))
        self.assertContains(r, 'value="hitung"')
        r = self.client.post(reverse("master:tiket"), {
            "aksi": "buka", "jenis": "hitung", "untuk": self.gudang.id, "tujuan": "hitung rak depan",
            "batas": "2099-01-01T10:00", "lokasi": Lokasi.objects.get(jenis="toko").id, "rak": ["TK-01"]}, follow=True)
        t = Tiket.objects.get()
        self.assertEqual((t.jenis, t.saring["rak"], t.saring["lokasi"]), ("hitung", ["TK-01"], Lokasi.objects.get(jenis="toko").id))
        r = self.client.get(reverse("master:tiket_detail", args=[t.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "hitung rak depan")
