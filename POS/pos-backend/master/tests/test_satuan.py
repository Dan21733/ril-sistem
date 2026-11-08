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


class SatuanTampilTest(Dasar):
    """Tahap 2: semua layar master menampilkan dan menerima angka per satuan."""

    def setUp(self):
        import json
        self.json = json
        self.client.force_login(self.admin)
        buat_item("Fiber Pagar", "1m", floor=22000, jual=25000, modal=20000, stok=50)
        layanan.sinkron_info()
        self.v = VarianInfo.objects.get(item_name="Fiber Pagar", variant="1m")
        svc.atur(self.admin, "Fiber Pagar", "m", 10)
        self.v.refresh_from_db()

    def _item(self):
        return Item.objects.get(item_name="Fiber Pagar", variant="1m")

    def _kirim(self, alamat, data):
        return self.client.post(alamat, self.json.dumps(data), content_type="application/json")

    def test_baca(self):
        self.assertEqual(svc.baca_jumlah("1,5", 10), 15)
        self.assertEqual(svc.baca_jumlah("2", 10), 20)
        self.assertEqual(svc.baca_jumlah("1.5", 10), 15)
        self.assertIsNone(svc.baca_jumlah("1,55", 10))   # tidak dibulatkan diam-diam
        self.assertIsNone(svc.baca_jumlah("abc", 10))
        self.assertEqual(svc.baca_harga("25.000", 10), 2500)

    def test_ubah_harga_sel_diketik_per_meter(self):
        r = self._kirim("/master/barang/ubah/", {"id": self.v.id, "kolom": "max_price", "nilai": "27.000"})
        self.assertEqual(r.json()["nilai"], 27000)
        self.assertEqual(self._item().max_price, 2700)

    def test_stok_diketik_per_meter(self):
        r = self._kirim("/master/stok/ubah/", {"id": self.v.id, "nilai": "12,5", "jenis": "hitung"})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(self._item().in_stock, 125)
        r = self._kirim("/master/stok/ubah/", {"id": self.v.id, "nilai": "12,55", "jenis": "hitung"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._item().in_stock, 125)

    def test_halaman_menampilkan_per_meter(self):
        isi = self.client.get("/master/harga/").content.decode()
        self.assertIn("25.000", isi)
        self.assertIn("per m", isi)
        isi = self.client.get("/master/stok/").content.decode()
        self.assertIn("50 m", isi)

    def test_massal_rupiah_dan_tetapkan_per_meter(self):
        qs = Item.objects.filter(item_name="Fiber Pagar")
        param = {"kolom": "max_price", "cara": "rupiah", "angka": 1000, "bulat": 0, "catatan": ""}
        r = layanan.rencana_massal(qs, param)
        self.assertEqual((r[0]["lama_tampil"], r[0]["baru_tampil"], r[0]["baru"]), (25000, 26000, 2600))
        param = {**param, "cara": "tetapkan", "angka": 30000, "bulat": 1000}
        self.assertEqual(layanan.rencana_massal(qs, param)[0]["baru"], 3000)
        param = {**param, "cara": "persen", "angka": 10, "bulat": 1000}
        self.assertEqual(layanan.rencana_massal(qs, param)[0]["baru_tampil"], 28000)  # 27.500 dibulatkan ke atas

    def test_tambah_varian_ikut_satuan(self):
        from master.models import ItemInfo
        item_id = ItemInfo.objects.get(item_name="Fiber Pagar").id
        self.client.post(f"/master/barang/item/{item_id}/", {"aksi": "tambah_varian", "variant": "1,5m",
                                                             "min_price": "30.000", "max_price": "35.000"})
        baru = VarianInfo.objects.get(item_name="Fiber Pagar", variant="1,5m")
        self.assertEqual((baru.satuan, baru.kelipatan), ("m", 10))
        self.assertEqual(Item.objects.get(item_name="Fiber Pagar", variant="1,5m").max_price, 3500)

    def test_nota_manual_jumlah_berkoma(self):
        from master.layanan import nota_manual
        from django.utils import timezone
        data = {"tanggal": timezone.localdate().isoformat(), "pembeli": "Pak Budi", "cara_bayar": "cash",
                "baris": self.json.dumps([{"id": self.v.id, "jumlah": 1.5, "harga": 25000}])}
        n = nota_manual.buat(self.admin, data)
        b = n.baris.get()
        self.assertEqual((b.jumlah, b.harga, b.subtotal, n.total_barang), (15, 2500, 37500, 37500))
        self.assertEqual(self._item().in_stock, 485)
        isi = self.client.get(f"/master/nota-manual/{n.id}/").content.decode()
        self.assertIn("1,5 m", isi)
        self.assertIn("25.000/m", isi)
        hasil = self.client.get("/master/nota-manual/cari/?q=fiber").json()["hasil"][0]
        self.assertEqual((hasil["harga"], hasil["satuan"], hasil["k"]), (25000, "m", 10))
        with self.assertRaises(Tolak):
            nota_manual.buat(self.admin, {**data, "baris": self.json.dumps([{"id": self.v.id, "jumlah": 1.55, "harga": 25000}])})

    def test_hp_barang_dan_stok_per_meter(self):
        import time
        from django.urls import reverse
        from master.models import ItemInfo
        sesi = self.client.session
        sesi["hp_aktif"] = int(time.time())
        sesi.save()
        item_id = ItemInfo.objects.get(item_name="Fiber Pagar").id
        isi = self.client.get(reverse("hp:barang_detail", args=[item_id])).content.decode()
        self.assertIn("25.000", isi)
        self.assertIn("Harga jual/m", isi)
        self.assertIn("50 m", isi)
        self.assertIn("stok 50 m", self.client.get(reverse("hp:barang") + "?q=fiber").content.decode())
        r = self._kirim(reverse("hp:stok"), {"id": self.v.id, "cara": "kurang", "angka": "2,5", "jenis": "rusak"})
        self.assertEqual(r.json()["stok_teks"], "47,5 m", r.json())
        self.assertEqual(self._item().in_stock, 475)
        r = self._kirim(reverse("hp:stok"), {"id": self.v.id, "cara": "kurang", "angka": "0,25", "jenis": "rusak"})
        self.assertEqual(r.status_code, 400)


class SatuanGudangTest(Dasar):
    """Tahap 3: gudang, PO, buka dus diketik per satuan (9 Nov 2026)."""

    def setUp(self):
        buat_item("Fiber Pagar", "1m", floor=22000, jual=25000, modal=20000, stok=50)
        buat_item("Semen", "50kg", floor=60000, jual=65000, modal=58000, stok=10)
        buat_item("Semen", "eceran", floor=1300, jual=1500, modal=1160, stok=0)
        layanan.sinkron_info()
        svc.atur(self.admin, "Fiber Pagar", "m", 100)
        self.v = VarianInfo.objects.get(item_name="Fiber Pagar", variant="1m")

    def test_baca_qty(self):
        self.assertEqual(svc.baca_qty("0,86", self.v), 86)
        self.assertEqual(svc.baca_qty("12", self.v), 1200)
        with self.assertRaises(Tolak):
            svc.baca_qty("0,865", self.v)
        biasa = VarianInfo.objects.get(item_name="Semen", variant="50kg")
        self.assertEqual(svc.baca_qty("1.000", biasa), 1000)
        self.assertEqual(svc.baca_harga_qty("25.000", self.v), 250)

    def test_opname_hitung_per_meter(self):
        from master.layanan import opname
        opname.simpan_hitungan(self.admin, {f"hitung_{self.v.id}": "47,35"})
        from master.models import OpnameBaris
        self.assertEqual(OpnameBaris.objects.get(varian=self.v).hitung, 4735)
        with self.assertRaises(Tolak):
            opname.simpan_hitungan(self.admin, {f"hitung_{self.v.id}": "1,234"})

    def test_po_jumlah_dan_harga_beli_per_meter(self):
        from master.layanan import pembelian
        from master.models import Supplier
        s = Supplier.objects.create(nama="Distributor Fiber")
        po = pembelian.buat(self.admin, supplier_id=s.id, tanggal_pesan="2026-11-09")
        b = pembelian.tambah_baris(po, varian_id=self.v.id, jumlah="30", harga="18.000")
        self.assertEqual((b.jumlah_pesan, b.harga, b.jumlah_pesan * b.harga), (3000, 180, 540000))  # 30 m x Rp 18.000

    def test_buka_dus_sak_jadi_kg(self):
        from master.layanan import bukadus
        svc.atur(self.admin, "Semen", "kg", 100)     # semen eceran per kg, sak tetap per buah? (satu barang: semua varian)
        sak = VarianInfo.objects.get(item_name="Semen", variant="50kg")
        ecer = VarianInfo.objects.get(item_name="Semen", variant="eceran")
        k = bukadus.buat_konversi(self.admin, dari_id=sak.id, ke_id=ecer.id, isi="50")
        self.assertEqual(k.isi, 5000)
        # Satuan berubah lagi: isi ikut dikonversi.
        svc.atur(self.admin, "Semen", "kg", 10)
        k.refresh_from_db()
        self.assertEqual(k.isi, 500)

    def test_halaman_gudang_tampil_per_meter(self):
        from master.layanan import opname
        opname.simpan_hitungan(self.admin, {f"hitung_{self.v.id}": "47,35"})
        op = opname.ajukan(self.admin)
        self.client.force_login(self.admin)
        isi = self.client.get(f"/master/opname/{op.id}/").content.decode()
        self.assertIn("47,35 m", isi)
