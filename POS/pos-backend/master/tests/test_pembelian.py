"""Uji: Supplier, pembelian (PO), retur, ongkir, faktur tanpa PO."""
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from pos.models import Item

from master.layanan.inti import Tolak
from master.models import Log
from master.tests.dasar import Dasar, PENYIMPANAN_UJI


class SupplierTest(Dasar):
    def test_tambah_ubah_dan_hak(self):
        from master.models import Supplier
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(reverse("master:supplier")).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("master:supplier"), {"aksi": "tambah", "nama": "PT Sumber Jaya", "pkp": "1", "npwp": "01.234"})
        s = Supplier.objects.get()
        self.assertEqual((s.nama, s.pkp, s.npwp), ("PT Sumber Jaya", True, "01.234"))
        self.client.post(reverse("master:supplier"), {"aksi": "tambah", "nama": "pt sumber jaya"})
        self.assertEqual(Supplier.objects.count(), 1)
        self.client.post(reverse("master:supplier"), {"aksi": "simpan", "id": s.id, "nama": "PT Sumber Jaya Abadi", "pkp": "0"})
        s.refresh_from_db()
        self.assertEqual((s.nama, s.pkp), ("PT Sumber Jaya Abadi", False))
        self.assertEqual(self.client.get(reverse("master:supplier") + "?buka=%d" % s.id).status_code, 200)


class PembelianTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from datetime import date
        from master.models import Supplier, TarifPPN
        cls.supplier = Supplier.objects.create(nama="PT Sumber", pkp=True)
        TarifPPN.objects.create(mulai=date(2025, 1, 1), persen="11")

    def _po(self, baris=(("Palu", "Kecil", 10, 1110),)):
        from master.layanan import pembelian as svc
        po = svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-10-01")
        for nama, varian, qty, harga in baris:
            svc.tambah_baris(po, varian_id=self.info(nama, varian).id, jumlah=qty, harga=harga)
        return po

    def _datang(self, po, mode="termasuk", terima=None, catatan="", **lain):
        from master.layanan import pembelian as svc
        data = {"mode_ppn": mode, "tanggal_terima": "2026-10-02", "catatan_terima": catatan, **lain}
        for b in po.baris.all():
            data[f"terima_{b.id}"] = (terima or {}).get(b.item_name, b.jumlah_pesan)
            data[f"harga_{b.id}"] = b.harga
        svc.catat_datang(self.admin, po, data)
        po.refresh_from_db()
        return po

    def test_pisah_pajak(self):
        from master.layanan import pembelian as svc
        self.assertEqual(svc.pisah_pajak(111000, "termasuk", 11), (100000, 11000))
        self.assertEqual(svc.pisah_pajak(100000, "terpisah", 11), (100000, 11000))
        self.assertEqual(svc.pisah_pajak(100000, "tanpa", 11), (100000, 0))
        dpp, ppn = svc.pisah_pajak(100000, "termasuk", 11)
        self.assertEqual(dpp + ppn, 100000)

    def test_nomor_berurutan(self):
        a, b = self._po(), self._po()
        self.assertEqual((a.nomor, b.nomor), ("PO-2610-0001", "PO-2610-0002"))

    def test_sebelum_acc_tidak_ada_yang_berubah(self):
        from master.models import MutasiStok
        po = self._datang(self._po())
        self.assertEqual(po.status, "menunggu")
        barang = Item.objects.get(item_name="Palu", variant="Kecil")
        self.assertEqual((barang.in_stock, barang.cost_amount), (10, 800))
        self.assertEqual(MutasiStok.objects.count(), 0)
        self.assertEqual(po.total, 0)

    def test_acc_mengubah_stok_modal_dan_hutang(self):
        from master.layanan import pembelian as svc
        from master.models import MutasiStok
        po = svc.acc(self.admin, self._datang(self._po()))
        barang = Item.objects.get(item_name="Palu", variant="Kecil")
        # Belum PKP: PPN ikut modal, jadi modal terakhir = harga faktur 1.110.
        self.assertEqual((barang.in_stock, barang.cost_amount), (20, 1110))
        # Modal rata: (10 x 800 + 10 x 1110) / 20 = 955
        self.assertEqual(self.info("Palu", "Kecil").modal_rata, 955)
        self.assertEqual((po.status, po.total_dpp, po.total_ppn, po.total, po.sisa), ("diterima", 10000, 1100, 11100, 11100))
        m = MutasiStok.objects.get()
        self.assertEqual((m.jenis, m.jumlah, m.stok_sebelum, m.stok_sesudah, m.catatan), ("beli", 10, 10, 20, po.nomor))
        self.assertTrue(Log.objects.filter(kolom="cost_amount", sumber="pembelian", rahasia=True).exists())

    def test_acc_dua_kali_ditolak(self):
        from master.layanan import pembelian as svc
        po = svc.acc(self.admin, self._datang(self._po()))
        with self.assertRaises(Tolak):
            svc.acc(self.admin, po)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 20)

    def test_toko_pkp_ppn_tidak_masuk_modal(self):
        from datetime import date
        from master.layanan import pembelian as svc
        from master.models import Toko
        Toko.objects.filter(stok_di_kasir=True).update(pkp=True, pkp_mulai=date(2026, 1, 1))
        po = svc.acc(self.admin, self._datang(self._po()))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").cost_amount, 1000)
        self.assertEqual((po.toko_pkp, po.total), (True, 11100))

    def test_ppn_terpisah_dan_tanpa(self):
        from master.layanan import pembelian as svc
        po = svc.acc(self.admin, self._datang(self._po(), mode="terpisah"))
        self.assertEqual((po.total_dpp, po.total_ppn, po.total), (11100, 1221, 12321))
        po2 = svc.acc(self.admin, self._datang(self._po(), mode="tanpa"))
        self.assertEqual((po2.total_ppn, po2.total, po2.tarif_ppn), (0, 11100, 0))

    def test_datang_kurang_wajib_catatan(self):
        from master.layanan import pembelian as svc
        po = self._po()
        with self.assertRaises(Tolak):
            self._datang(po, terima={"Palu": 6})
        po.refresh_from_db()
        self.assertEqual(po.status, "dibuat")
        po = svc.acc(self.admin, self._datang(po, terima={"Palu": 6}, catatan="kurang 4, menyusul"))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 16)
        self.assertEqual(po.total, 6660)

    def test_stok_minus_dianggap_nol_untuk_modal_rata(self):
        from master.layanan import pembelian as svc
        Item.objects.filter(item_name="Palu", variant="Kecil").update(in_stock=-5)
        svc.acc(self.admin, self._datang(self._po()))
        self.assertEqual(self.info("Palu", "Kecil").modal_rata, 1110)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 5)

    def test_tanpa_tarif_ppn_ditolak(self):
        from master.models import TarifPPN
        TarifPPN.objects.all().delete()
        po = self._po()
        with self.assertRaises(Tolak):
            self._datang(po, mode="termasuk")
        self._datang(po, mode="tanpa")

    def test_bayar_cicil_dan_lunas(self):
        from master.layanan import pembelian as svc
        po = svc.acc(self.admin, self._datang(self._po()))
        with self.assertRaises(Tolak):
            svc.bayar(self.admin, po, tanggal="2026-10-03", jumlah="99.999.999", cara="transfer")
        po = svc.bayar(self.admin, po, tanggal="2026-10-03", jumlah="5.000", cara="tunai")
        self.assertEqual((po.terbayar, po.sisa, po.lunas), (5000, 6100, False))
        po = svc.bayar(self.admin, po, tanggal="2026-10-10", jumlah="6100", cara="transfer")
        self.assertEqual((po.sisa, po.lunas), (0, True))
        with self.assertRaises(Tolak):
            svc.bayar(self.admin, po, tanggal="2026-10-11", jumlah="1", cara="transfer")

    def test_bayar_sebelum_acc_ditolak_dan_batal(self):
        from master.layanan import pembelian as svc
        po = self._datang(self._po())
        with self.assertRaises(Tolak):
            svc.bayar(self.admin, po, tanggal="2026-10-03", jumlah="1", cara="transfer")
        svc.batal(self.admin, po, "salah supplier")
        po.refresh_from_db()
        self.assertEqual(po.status, "batal")
        with self.assertRaises(Tolak):
            svc.acc(self.admin, po)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)

    def test_barang_kembar_dan_edit_setelah_datang_ditolak(self):
        from master.layanan import pembelian as svc
        po = self._po()
        with self.assertRaises(Tolak):
            svc.tambah_baris(po, varian_id=self.info("Palu", "Kecil").id, jumlah=1, harga=1)
        self._datang(po)
        with self.assertRaises(Tolak):
            svc.tambah_baris(po, varian_id=self.info("Kikir").id, jumlah=1, harga=1)
        svc.kembalikan(self.admin, po)
        po.refresh_from_db()
        svc.tambah_baris(po, varian_id=self.info("Kikir").id, jumlah=1, harga=500)

    def test_alur_lewat_layar_dan_hak(self):
        from master.models import Pembelian
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(reverse("master:pembelian")).status_code, 403)
        self.assertEqual(self.client.get(reverse("master:pengaturan")).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("master:pembelian_baru"), {"supplier": self.supplier.id, "tanggal_pesan": "2026-10-01"})
        po = Pembelian.objects.get()
        alamat = reverse("master:pembelian_detail", args=[po.id])
        hasil = self.client.get(reverse("master:pembelian_cari") + "?q=palu").json()["hasil"]
        self.assertEqual([h["varian"] for h in hasil], ["Besar", "Kecil"])
        self.assertEqual(hasil[1]["harga"], 800)
        self.client.post(alamat, {"aksi": "tambah_baris", "varian_id": hasil[1]["id"], "jumlah": "10", "harga": "1.110"})
        b = po.baris.get()
        self.assertEqual(self.client.get(alamat).status_code, 200)
        self.client.post(alamat, {"aksi": "datang", "mode_ppn": "termasuk", "tanggal_terima": "2026-10-02",
                                  f"terima_{b.id}": "10", f"harga_{b.id}": "1.110", "jatuh_tempo": "2026-10-20"})
        self.assertIn("Menunggu ACC", self.client.get(alamat).content.decode())
        self.client.post(alamat, {"aksi": "acc"})
        self.client.post(alamat, {"aksi": "bayar", "tanggal": "2026-10-03", "jumlah": "11.100", "cara": "transfer"})
        po.refresh_from_db()
        self.assertTrue(po.lunas)
        for a in [alamat, reverse("master:pembelian"), reverse("master:pembelian") + "?status=hutang",
                  reverse("master:pengaturan"), reverse("master:dashboard"), reverse("master:stok_riwayat")]:
            self.assertEqual(self.client.get(a).status_code, 200, a)

    def test_pengaturan_tarif_dan_pkp_per_toko(self):
        from master.models import TarifPPN, Toko
        self.client.force_login(self.admin)
        self.client.post(reverse("master:pengaturan"), {"aksi": "tarif", "mulai": "2027-01-01", "persen": "12"})
        self.assertEqual(TarifPPN.objects.count(), 2)
        t = Toko.objects.get(stok_di_kasir=True)
        isi = {"aksi": "simpan", "id": t.id, "kode": t.kode, "nama": t.nama, "pkp": "1"}
        self.client.post(reverse("master:toko"), isi)
        t.refresh_from_db(); self.assertFalse(t.pkp)          # tanpa tanggal mulai ditolak
        self.client.post(reverse("master:toko"), {**isi, "pkp_mulai": "2027-01-01"})
        t.refresh_from_db(); self.assertTrue(t.pkp)


class ReturOngkirTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.models import Supplier, TarifPPN
        cls.supplier = Supplier.objects.create(nama="PT Sumber", pkp=True)
        TarifPPN.objects.create(mulai=date(2025, 1, 1), persen="11")

    _po = PembelianTest._po
    _datang = PembelianTest._datang

    def _acc(self, baris=(("Palu", "Kecil", 10, 1000),), mode="tanpa", **lain):
        from master.layanan import pembelian as svc
        return svc.acc(self.admin, self._datang(self._po(baris), mode=mode, **lain))

    def test_ongkir_dibagi_menurut_nilai_dan_masuk_modal(self):
        # Kecil 10 x 1.000 = 10.000, Besar 10 x 3.000 = 30.000. Ongkir 4.000 dibagi 1.000 : 3.000.
        po = self._acc((("Palu", "Kecil", 10, 1000), ("Palu", "Besar", 10, 3000)), ongkir="4.000")
        kecil, besar = po.baris.order_by("id")
        self.assertEqual((kecil.ongkir, besar.ongkir), (1000, 3000))
        self.assertEqual((kecil.modal_satuan, besar.modal_satuan), (1100, 3300))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").cost_amount, 1100)
        # Ditagih supplier: ikut hutang.
        self.assertEqual((po.total, po.sisa), (44000, 44000))

    def test_ongkir_ekspedisi_masuk_modal_tapi_bukan_hutang(self):
        po = self._acc(ongkir="500", ongkir_tagihan="terpisah")
        self.assertEqual(po.baris.get().modal_satuan, 1050)
        self.assertEqual(po.total, 10000)

    def test_ongkir_pembulatan_tidak_hilang(self):
        po = self._acc((("Palu", "Kecil", 3, 1000), ("Palu", "Besar", 3, 1000), ("Kikir", "N/A", 3, 1000)), ongkir="1.000")
        self.assertEqual(sum(b.ongkir for b in po.baris.all()), 1000)

    def test_tanpa_ongkir_sama_seperti_dulu(self):
        po = self._acc()
        self.assertEqual((po.ongkir, po.baris.get().modal_satuan, po.total), (0, 1000, 10000))

    def test_retur_mengurangi_stok_hutang_dan_mengembalikan_modal_rata(self):
        from master.layanan import pembelian as svc
        from master.models import MutasiStok
        po = self._acc()
        info = self.info("Palu", "Kecil")
        self.assertEqual(info.modal_rata, 900)          # (10 x 800 + 10 x 1.000) / 20
        b = po.baris.get()
        svc.retur(self.admin, po, {f"retur_{b.id}": "4", "tanggal": "2026-10-03", "alasan": "pecah"})
        po.refresh_from_db(); info.refresh_from_db()
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 16)
        self.assertEqual((po.diretur, po.sisa), (4000, 6000))
        self.assertEqual(info.modal_rata, 875)          # (20 x 900 - 4 x 1.000) / 16
        m = MutasiStok.objects.filter(jenis="retur_beli").get()
        self.assertEqual((m.jumlah, m.stok_sebelum, m.stok_sesudah), (-4, 20, 16))
        # Retur semua sisanya: modal rata kembali ke sebelum pembelian.
        svc.retur(self.admin, po, {f"retur_{b.id}": "6", "tanggal": "2026-10-03", "alasan": "sisanya"})
        po.refresh_from_db(); info.refresh_from_db()
        self.assertEqual((po.diretur, po.sisa, info.modal_rata), (10000, 0, 800))

    def test_retur_tidak_boleh_melebihi_yang_diterima(self):
        from master.layanan import pembelian as svc
        from master.models import Retur
        po = self._acc()
        b = po.baris.get()
        svc.retur(self.admin, po, {f"retur_{b.id}": "7", "tanggal": "2026-10-03", "alasan": "x"})
        with self.assertRaises(Tolak):
            svc.retur(self.admin, po, {f"retur_{b.id}": "4", "tanggal": "2026-10-03", "alasan": "x"})
        self.assertEqual(Retur.objects.count(), 1)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 13)

    def test_retur_wajib_alasan_dan_jumlah(self):
        from master.layanan import pembelian as svc
        from master.models import Retur
        po = self._acc()
        b = po.baris.get()
        with self.assertRaises(Tolak):
            svc.retur(self.admin, po, {f"retur_{b.id}": "1", "tanggal": "2026-10-03", "alasan": " "})
        with self.assertRaises(Tolak):
            svc.retur(self.admin, po, {"tanggal": "2026-10-03", "alasan": "x"})
        self.assertEqual(Retur.objects.count(), 0)

    def test_retur_sebelum_acc_ditolak(self):
        from master.layanan import pembelian as svc
        po = self._datang(self._po(), mode="tanpa")
        with self.assertRaises(Tolak):
            svc.retur(self.admin, po, {f"retur_{po.baris.get().id}": "1", "tanggal": "2026-10-03", "alasan": "x"})

    def test_retur_dengan_ppn_nilainya_pas(self):
        from master.layanan import pembelian as svc
        po = self._acc((("Palu", "Kecil", 3, 1110),), mode="termasuk")
        b = po.baris.get()
        for _ in range(3):
            svc.retur(self.admin, po, {f"retur_{b.id}": "1", "tanggal": "2026-10-03", "alasan": "x"})
        po.refresh_from_db()
        self.assertEqual((po.diretur, po.sisa), (po.total, 0))

    def test_retur_setelah_lunas_lalu_uang_kembali(self):
        from master.layanan import pembelian as svc
        po = self._acc()
        svc.bayar(self.admin, po, tanggal="2026-10-03", jumlah="10.000", cara="transfer")
        b = po.baris.get()
        svc.retur(self.admin, po, {f"retur_{b.id}": "2", "tanggal": "2026-10-04", "alasan": "cacat"})
        po.refresh_from_db()
        self.assertEqual((po.sisa, po.lebih_bayar), (-2000, 2000))
        with self.assertRaises(Tolak):
            svc.uang_kembali(self.admin, po, tanggal="2026-10-05", jumlah="2.001", cara="tunai")
        po = svc.uang_kembali(self.admin, po, tanggal="2026-10-05", jumlah="2.000", cara="tunai")
        self.assertEqual((po.terbayar, po.sisa, po.lebih_bayar), (8000, 0, 0))

    def test_retur_saat_stok_sudah_habis_tidak_mengacak_modal(self):
        from master.layanan import pembelian as svc
        po = self._acc()
        Item.objects.filter(item_name="Palu", variant="Kecil").update(in_stock=2)
        b = po.baris.get()
        svc.retur(self.admin, po, {f"retur_{b.id}": "5", "tanggal": "2026-10-03", "alasan": "x"})
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, -3)
        self.assertEqual(self.info("Palu", "Kecil").modal_rata, 900)

    def test_halaman_retur(self):
        po = self._acc(ongkir="500")
        self.client.force_login(self.admin)
        b = po.baris.get()
        r = self.client.post(f"/master/pembelian/{po.id}/", {"aksi": "retur", f"retur_{b.id}": "1", "tanggal": "2026-10-03", "alasan": "pecah"}, follow=True)
        self.assertContains(r, "Retur dicatat")
        self.assertContains(r, "pecah")
        self.assertContains(r, "Ongkir")


@override_settings(STORAGES=PENYIMPANAN_UJI)
class FakturManualTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.models import Supplier
        cls.supplier = Supplier.objects.create(nama="PT Sumber")

    def _data(self, **lain):
        return {"supplier": "PT Sumber", "nomor_faktur": "INV-001", "tanggal_faktur": "2026-09-01",
                "jatuh_tempo": "2026-10-01", "total": "1.250.000", "keterangan": "Semen 20 sak\nPaku 2 dus", **lain}

    def _foto(self, nama="faktur.jpg", jenis="image/jpeg", isi=b"\xff\xd8\xff gambar"):
        return SimpleUploadedFile(nama, isi, content_type=jenis)

    def test_buat_hanya_hutang_stok_dan_modal_tidak_berubah(self):
        from master.layanan import faktur
        from master.models import MutasiStok
        sebelum = list(Item.objects.order_by("item_name", "variant").values_list("in_stock", "cost_amount"))
        f = faktur.buat(self.admin, self._data(), [self._foto()])
        self.assertEqual((f.total, f.sisa, f.supplier, f.foto.count()), (1250000, 1250000, self.supplier, 1))
        self.assertEqual(list(Item.objects.order_by("item_name", "variant").values_list("in_stock", "cost_amount")), sebelum)
        self.assertEqual(MutasiStok.objects.count(), 0)
        self.assertTrue(Log.objects.filter(objek="faktur_manual").exists())

    def test_supplier_baru_dibuat_otomatis(self):
        from master.layanan import faktur
        from master.models import Supplier
        f = faktur.buat(self.admin, self._data(supplier="  toko baru jaya "))
        self.assertEqual(f.supplier.nama, "toko baru jaya")
        self.assertEqual(Supplier.objects.count(), 2)
        faktur.buat(self.admin, self._data(supplier="PT SUMBER", nomor_faktur="X-2"))
        self.assertEqual(Supplier.objects.count(), 2)

    def test_isian_wajib_dan_tolak_ganda(self):
        from master.layanan import faktur
        from master.models import FakturManual
        for salah in ({"nomor_faktur": " "}, {"keterangan": ""}, {"total": "0"}, {"tanggal_faktur": ""},
                      {"jatuh_tempo": "2026-08-01"}, {"supplier": ""}):
            with self.assertRaises(Tolak):
                faktur.buat(self.admin, self._data(**salah))
        faktur.buat(self.admin, self._data())
        with self.assertRaises(Tolak):
            faktur.buat(self.admin, self._data(nomor_faktur="inv-001"))
        self.assertEqual(FakturManual.objects.count(), 1)

    def test_tolak_kalau_faktur_sudah_ada_di_po(self):
        from master.layanan import faktur
        from master.layanan import pembelian as svc
        po = svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-09-01")
        po.nomor_faktur = "INV-001"
        po.save()
        with self.assertRaisesMessage(Tolak, po.nomor):
            faktur.buat(self.admin, self._data())

    def test_berkas_dibatasi(self):
        from master.layanan import faktur
        with self.assertRaises(Tolak):
            faktur.buat(self.admin, self._data(), [self._foto("a.exe", "application/octet-stream")])
        with self.assertRaises(Tolak):
            faktur.buat(self.admin, self._data(), [self._foto(f"{i}.jpg") for i in range(7)])

    def test_faktur_lama_langsung_lunas(self):
        from master.layanan import faktur
        f = faktur.buat(self.admin, self._data(sudah_lunas="1", tanggal_lunas="2026-09-05", cara_lunas="tunai"))
        b = f.pembayaran.get()
        self.assertEqual((f.sisa, b.jumlah, str(b.tanggal), b.cara), (0, 1250000, "2026-09-05", "tunai"))
        self.assertEqual(f.pembayaran.count(), 1)

    def test_cicil_lalu_lunas_dan_masuk_ringkasan_hutang(self):
        from datetime import date
        from master.layanan import faktur
        from master.layanan import pembelian as svc
        f = faktur.buat(self.admin, self._data())
        r = svc.ringkasan_hutang(date(2026, 10, 5))
        self.assertEqual((r["total"], r["jumlah"], len(r["lewat"])), (1250000, 1, 1))
        faktur.bayar(self.admin, f, tanggal="2026-10-02", jumlah="250.000", cara="transfer")
        with self.assertRaises(Tolak):
            faktur.bayar(self.admin, f, tanggal="2026-10-02", jumlah="1.000.001", cara="transfer")
        f = faktur.bayar(self.admin, f, tanggal="2026-10-03", jumlah="1.000.000", cara="tunai")
        self.assertTrue(f.lunas)
        self.assertEqual(svc.ringkasan_hutang()["jumlah"], 0)

    def test_ubah_dan_hapus(self):
        from master.layanan import faktur
        from master.models import FakturManual
        f = faktur.buat(self.admin, self._data())
        faktur.bayar(self.admin, f, tanggal="2026-10-02", jumlah="500.000", cara="transfer")
        f.refresh_from_db()
        with self.assertRaises(Tolak):
            faktur.ubah(self.admin, f, self._data(total="400.000"))
        self.assertTrue(faktur.ubah(self.admin, f, self._data(total="1.300.000", catatan="dipesan ibu")))
        f.refresh_from_db()
        self.assertEqual((f.total, f.sisa, f.catatan), (1300000, 800000, "dipesan ibu"))
        with self.assertRaises(Tolak):
            faktur.hapus(self.admin, f)
        g = faktur.buat(self.admin, self._data(nomor_faktur="SALAH"), [self._foto()])
        faktur.hapus(self.admin, g)
        self.assertEqual(FakturManual.objects.count(), 1)

    def test_lewat_layar(self):
        from master.models import FakturManual
        self.client.force_login(self.admin)
        r = self.client.post(reverse("master:faktur_baru"), {**self._data(), "foto": self._foto()}, follow=True)
        self.assertContains(r, "Faktur INV-001 tersimpan")
        f = FakturManual.objects.get()
        self.assertContains(r, "Semen 20 sak")
        foto = f.foto.get()
        isi = self.client.get(reverse("master:faktur_foto", args=[foto.id]))
        self.assertEqual((isi.status_code, isi["Content-Type"]), (200, "image/jpeg"))
        self.assertContains(self.client.get(reverse("master:faktur") + "?q=semen"), "INV-001")
        self.assertContains(self.client.get(reverse("master:hutang")), "Di luar PO")
        self.assertContains(self.client.get(reverse("master:dashboard")), "1.250.000")
        self.client.post(reverse("master:faktur_detail", args=[f.id]), {"aksi": "bayar", "tanggal": "2026-10-02",
                                                                         "jumlah": "1.250.000", "cara": "transfer"})
        self.assertContains(self.client.get(reverse("master:hutang")), "Semua lunas")

    def test_gudang_dan_finance_tidak_bisa_membuka(self):
        from master.layanan import faktur
        f = faktur.buat(self.admin, self._data(), [self._foto()])
        self.client.force_login(self.finance)
        for url in (reverse("master:faktur"), reverse("master:faktur_detail", args=[f.id]),
                    reverse("master:faktur_foto", args=[f.foto.get().id]), reverse("master:hutang")):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.gudang)
        self.assertRedirects(self.client.get(reverse("master:faktur")), reverse("gudang:beranda"))
