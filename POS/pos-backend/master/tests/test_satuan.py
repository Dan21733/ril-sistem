"""Jual per satuan dengan kelipatan (keputusan pemilik 6 Nov 2026)."""
from pos.models import Item

from master.layanan import inti as layanan
from master.layanan import satuan as svc
from master.layanan.inti import Tolak
from master.models import Lokasi, Opname, OpnameBaris, StokLokasi, VarianInfo
from master.tests.dasar import Dasar, buat_item


class SatuanTest(Dasar):
    def setUp(self):
        self.client.force_login(self.admin)
        buat_item("Fiber Pagar", "1m", floor=22000, jual=25000, modal=20000, stok=50)
        buat_item("Fiber Pagar", "1,2m", floor=27000, jual=30000, modal=24000, stok=12)
        layanan.sinkron_info()
        self.v = VarianInfo.objects.get(item_name="Fiber Pagar", variant="1m")
        gudang = Lokasi.objects.create(kode="GSAT", nama="Gudang Satuan", jenis="gudang")
        StokLokasi.objects.create(varian=self.v, lokasi=gudang, jumlah=30)

    def _item(self, varian="1m"):
        return Item.objects.get(item_name="Fiber Pagar", variant=varian)

    def test_format(self):
        self.assertEqual(svc.format_jumlah(15, "m", 10), "1,5 m")
        self.assertEqual(svc.format_jumlah(500, "m", 10), "50 m")
        self.assertEqual(svc.format_jumlah(7, "m", 100), "0,07 m")
        self.assertEqual(svc.format_jumlah(1234, "", 1), "1.234")
        self.assertEqual(svc.label("m", 10), "per m, kelipatan 0,1 m")

    def test_pasang_meter_kelipatan_10_mengonversi_stok_harga_modal(self):
        n = svc.atur(self.admin, "Fiber Pagar", "m", 10)
        self.assertEqual(n, 2)
        i = self._item()
        self.assertEqual((i.in_stock, i.max_price, i.min_price, i.cost_amount), (500, 2500, 2200, 2000))
        self.assertEqual(StokLokasi.objects.get(varian=self.v).jumlah, 300)
        self.v.refresh_from_db()
        self.assertEqual((self.v.satuan, self.v.kelipatan), ("m", 10))
        # 1,5 m x Rp 25.000/m = Rp 37.500 dari angka tersimpan
        self.assertEqual(15 * i.max_price, 37500)
        # kembali ke per buah: dibalik
        svc.atur(self.admin, "Fiber Pagar", "", 1)
        i = self._item()
        self.assertEqual((i.in_stock, i.max_price, i.min_price, i.cost_amount), (50, 25000, 22000, 20000))
        self.assertEqual(StokLokasi.objects.get(varian=self.v).jumlah, 30)

    def test_kembali_ke_utuh_ditolak_kalau_stok_tidak_pas(self):
        svc.atur(self.admin, "Fiber Pagar", "m", 10)
        Item.objects.filter(item_name="Fiber Pagar", variant="1m").update(in_stock=505)  # 50,5 m
        with self.assertRaises(Tolak):
            svc.atur(self.admin, "Fiber Pagar", "m", 1)
        self.assertEqual(self._item().in_stock, 505)

    def test_opname_berjalan_menghalangi(self):
        op = Opname.objects.create(status="draf", dibuat_oleh="uji")
        from django.utils import timezone
        OpnameBaris.objects.create(opname=op, varian=self.v, item_name="Fiber Pagar", variant="1m",
                                   hitung=50, stok_saat_hitung=50, waktu_hitung=timezone.now())
        with self.assertRaises(Tolak) as e:
            svc.atur(self.admin, "Fiber Pagar", "m", 10)
        self.assertIn("opname", str(e.exception))
        self.assertEqual(self._item().in_stock, 50)

    def test_lewat_panel_barang(self):
        from master.models import ItemInfo
        item_id = ItemInfo.objects.get(item_name="Fiber Pagar").id
        r = self.client.post(f"/master/barang/item/{item_id}/", {"aksi": "satuan", "satuan": "m", "kelipatan": "10"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._item().in_stock, 500)
        isi = self.client.get(f"/master/barang/item/{item_id}/").content.decode()
        self.assertIn("dijual per m, kelipatan 0,1 m", isi)

    def test_satuan_tidak_dikenal_ditolak(self):
        with self.assertRaises(Tolak):
            svc.atur(self.admin, "Fiber Pagar", "inci", 10)
        with self.assertRaises(Tolak):
            svc.atur(self.admin, "Fiber Pagar", "m", 7)
