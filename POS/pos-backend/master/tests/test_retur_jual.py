"""Uji: penerimaan barang retur/void dari kasir dan pilihan ditaruh di mana."""
import uuid

from django.urls import reverse
from django.utils import timezone

from pos.models import Item, RefundEvent, RefundEventDetail, TransactionDetail, Transactions

from master.akun.akses import SEMUA_HAK
from master.layanan import lokasi, retur_jual
from master.layanan.inti import Tolak
from master.models import MutasiStok, PenerimaanRetur, StokLokasi
from master.tests.dasar import Dasar


def event(nota, jenis, baris=(), uang=0):
    e = RefundEvent.objects.create(refund_id=uuid.uuid4(), sync_id=nota.sync_id, transaction_id=nota.transaction_id,
                                   event_type=jenis, reason="pembeli tukar", refund_method="cash", refund_amount=uang,
                                   approver_worker_id="ID001", device_code="T01", created_at=timezone.now())
    for nama, varian, qty in baris:
        RefundEventDetail.objects.create(refund_id=e.refund_id, item_name=nama, variant=varian, quantity=qty,
                                         unit_price=1500, subtotal=1500 * qty)
    return e


class ReturJualTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="ga", nama="Gudang A")
        cls.nota2 = Transactions.objects.create(
            sync_id=uuid.uuid4(), transaction_id="T01-000002", worker_id="ID001", transaction_time=timezone.now(),
            item_amount=4500, shipping_amount=0, total_amount=4500, payment_method="cash", payment_amount=4500,
            change_amount=0)
        TransactionDetail.objects.create(sync_id=cls.nota2.sync_id, item_name="Palu", variant="Besar",
                                         quantity=1, unit_price=2500, discount=0, subtotal=2500)
        TransactionDetail.objects.create(sync_id=cls.nota2.sync_id, item_name="Kikir", variant="N/A",
                                         quantity=2, unit_price=1000, discount=0, subtotal=2000)

    def setUp(self):
        self.client.force_login(self.admin)

    def stok(self, nama, varian):
        return Item.objects.get(item_name=nama, variant=varian).in_stock

    def test_retur_menunggu_lalu_diterima_ke_gudang_dengan_rusak(self):
        e = event(self.nota, "return", [("Palu", "Kecil", 2)], uang=3000)
        event(self.nota, "refund", uang=0)  # uang saja: tidak menunggu penerimaan barang
        self.assertEqual(retur_jual.jumlah_menunggu(), 1)
        r = self.client.get(reverse("master:nota"))
        self.assertContains(r, "retur belum diterima")
        self.assertContains(self.client.get(reverse("master:retur")), "Terima barang")
        self.client.post(reverse("master:retur"), {"refund_id": e.refund_id, "lokasi_0": self.ga.id, "rusak_0": "1"})
        self.assertEqual(self.stok("Palu", "Kecil"), 11)
        self.assertEqual(StokLokasi.objects.get(varian=self.info("Palu", "Kecil"), lokasi=self.ga).jumlah, 1)
        m = MutasiStok.objects.get(jenis="retur_jual")
        self.assertEqual((m.jumlah, m.lokasi), (1, self.ga))
        b = PenerimaanRetur.objects.get(refund_id=e.refund_id).baris.get()
        self.assertEqual((b.masuk, b.rusak), (1, 1))
        self.assertEqual(retur_jual.jumlah_menunggu(), 0)
        self.assertContains(self.client.get(reverse("master:nota_panel", args=[self.nota.sync_id])), "Diterima")

    def test_tidak_bisa_diterima_dua_kali(self):
        e = event(self.nota, "return", [("Palu", "Kecil", 1)])
        retur_jual.terima(self.admin, e.refund_id, {}, hak=SEMUA_HAK)
        with self.assertRaises(Tolak):
            retur_jual.terima(self.admin, e.refund_id, {}, hak=SEMUA_HAK)
        self.assertEqual(self.stok("Palu", "Kecil"), 11)

    def test_void_mengembalikan_semua_baris_nota_ke_toko(self):
        e = event(self.nota2, "void", uang=4500)
        p = retur_jual.terima(self.admin, e.refund_id, {}, hak=SEMUA_HAK)
        self.assertEqual(p.baris.count(), 2)
        self.assertEqual((self.stok("Palu", "Besar"), self.stok("Kikir", "N/A")), (11, 12))
        self.assertFalse(StokLokasi.objects.exclude(jumlah=0).exists())

    def test_akun_tanpa_hak_stok_ditolak(self):
        e = event(self.nota, "return", [("Palu", "Kecil", 1)])
        with self.assertRaises(Tolak):
            retur_jual.terima(self.finance, e.refund_id, {}, hak={"master", "laporan"})
        with self.assertRaises(Tolak):
            retur_jual.terima(self.admin, e.refund_id, {"lokasi_0": self.ga.id}, hak={"ubah_stok"})
        self.assertEqual(self.stok("Palu", "Kecil"), 10)

    def test_rusak_tidak_boleh_lebih_dari_jumlah(self):
        e = event(self.nota, "return", [("Palu", "Kecil", 2)])
        with self.assertRaises(Tolak):
            retur_jual.terima(self.admin, e.refund_id, {"rusak_0": "3"}, hak=SEMUA_HAK)
        self.assertFalse(PenerimaanRetur.objects.exists())

    def test_barang_yang_sudah_dihapus_harus_ditandai_rusak(self):
        e = event(self.nota, "return", [("Palu", "Kecil", 2)])
        Item.objects.filter(item_name="Palu", variant="Kecil").update(in_stock=10)
        TransactionDetail.objects.filter(item_name="Palu", variant="Kecil").delete()
        Item.objects.filter(item_name="Palu", variant="Kecil").delete()
        with self.assertRaises(Tolak):
            retur_jual.terima(self.admin, e.refund_id, {}, hak=SEMUA_HAK)
        p = retur_jual.terima(self.admin, e.refund_id, {"rusak_0": "2"}, hak=SEMUA_HAK)
        self.assertEqual(p.baris.get().rusak, 2)


class LaporanBersihTest(Dasar):
    """QA 18 Okt 2026 (T3): refund, retur, dan void mengurangi penjualan dan laba.

    Nota dasar: Palu Kecil 2 x 1.500 = 3.000, modal 800 per buah -> laba 1.400.
    """

    def ringkas_hari_ini(self):
        from master.layanan import laporan
        hari = timezone.localdate()
        mulai, selesai = laporan.rentang(hari, hari)
        nota, _ = laporan.ambil(mulai, selesai)
        return laporan.ringkas(nota, laporan.ambil_kembali(mulai, selesai))

    def test_tanpa_kejadian_sama_dengan_dulu(self):
        r = self.ringkas_hari_ini()
        self.assertEqual((r["omzet"], r["laba"], r["kembali"]), (3000, 1400, 0))

    def test_uang_kembali_saja_mengurangi_penjualan_dan_laba_penuh(self):
        event(self.nota, "refund", uang=1000)
        r = self.ringkas_hari_ini()
        self.assertEqual((r["omzet_kotor"], r["omzet"], r["laba"]), (3000, 2000, 400))

    def test_retur_mengembalikan_modal_barang(self):
        event(self.nota, "return", [("Palu", "Kecil", 1)], uang=1500)
        r = self.ringkas_hari_ini()
        self.assertEqual((r["omzet"], r["modal_kembali"], r["laba"]), (1500, 800, 700))

    def test_retur_rusak_tidak_mengembalikan_modal(self):
        e = event(self.nota, "return", [("Palu", "Kecil", 1)], uang=1500)
        retur_jual.terima(self.admin, e.refund_id, {"rusak_0": "1"}, hak=SEMUA_HAK)
        r = self.ringkas_hari_ini()
        self.assertEqual((r["modal_kembali"], r["laba"]), (0, -100))

    def test_void_seluruh_nota(self):
        event(self.nota, "void", uang=3000)
        r = self.ringkas_hari_ini()
        self.assertEqual((r["omzet"], r["laba"]), (0, 0))

    def test_halaman_laporan_menampilkan_bersih_dan_daftar_kejadian(self):
        event(self.nota, "refund", uang=1000)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:laporan"))
        self.assertContains(r, "Penjualan barang bersih")
        self.assertContains(r, "Refund, retur, dan void")
        self.assertContains(r, "Uang kembali")
