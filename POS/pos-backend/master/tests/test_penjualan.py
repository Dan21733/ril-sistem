"""Uji: Nota, laporan, nota tulis tangan."""
import json
import uuid
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from pos.models import Item, TransactionDetail, Transactions, Worker

from master.layanan import inti as layanan
from master.akun.akses import SEMUA_HAK
from master.layanan.inti import Tolak
from master.tests.dasar import Dasar, PENYIMPANAN_UJI, buat_item


class LaporanTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from datetime import timedelta
        Worker.objects.create(id="ID002", name="Amba", pin="x")
        kemarin = timezone.now() - timedelta(days=1)
        t2 = Transactions.objects.create(
            sync_id=uuid.uuid4(), transaction_id="T02-000001", worker_id="ID002", transaction_time=kemarin,
            item_amount=5000, shipping_amount=1000, total_amount=6000, payment_method="bank_transfer",
            payment_amount=6000, change_amount=0)
        TransactionDetail.objects.create(sync_id=t2.sync_id, item_name="Palu", variant="Besar", quantity=2,
                                         unit_price=2500, discount=0, subtotal=5000)
        cls.t2 = t2

    def _ambil(self, hari=1):
        from datetime import timedelta
        from master.layanan import laporan
        hari_ini = timezone.localdate()
        return laporan.ambil(*laporan.rentang(hari_ini - timedelta(days=hari), hari_ini))

    def test_ringkasan_dan_laba(self):
        from master.layanan import laporan
        nota, baris = self._ambil()
        r = laporan.ringkas(nota)
        # Palu Kecil 2 x 1.500 (modal 800) + Palu Besar 2 x 2.500 (modal 2.000)
        self.assertEqual((r["nota"], r["omzet"], r["ongkir"], r["total"], r["laba"]), (2, 8000, 1000, 9000, 2400))
        self.assertEqual(r["margin"], 30.0)

    def test_laba_memakai_modal_saat_nota_bukan_modal_sekarang(self):
        from master.layanan import laporan
        # Modal naik SETELAH nota dibuat: laba nota lama tidak boleh ikut berubah.
        layanan.ubah_varian(self.admin, self.info("Palu", "Kecil"), "cost_amount", "1400", hak=SEMUA_HAK)
        self.assertEqual(self.info("Palu", "Kecil").modal_rata, 1400)
        nota, _ = self._ambil()
        self.assertEqual(laporan.ringkas(nota)["laba"], 2400)

    def test_kelompok_diurut_nilai_bukan_jumlah(self):
        from master.layanan import laporan
        buat_item("Baut", floor=10, jual=20, modal=5, stok=5000)
        layanan.sinkron_info()
        TransactionDetail.objects.create(sync_id=self.nota.sync_id, item_name="Baut", variant="N/A", quantity=100,
                                         unit_price=20, discount=0, subtotal=2000)
        _, baris = self._ambil()
        self.assertEqual([h["nama"] for h in laporan.kelompok_baris(baris, "barang", "omzet")], ["Palu", "Baut"])
        self.assertEqual([h["nama"] for h in laporan.kelompok_baris(baris, "barang", "qty")], ["Baut", "Palu"])
        self.assertEqual([h["nama"] for h in laporan.kelompok_baris(baris, "varian", "omzet")],
                         ["Palu / Besar", "Palu / Kecil", "Baut"])

    def test_kelompok_kasir_dan_bayar(self):
        from master.layanan import laporan
        nota, _ = self._ambil()
        self.assertEqual({h["nama"]: h["omzet"] for h in laporan.kelompok_nota(nota, "kasir")}, {"Rusdi": 3000, "Amba": 5000})
        self.assertEqual({h["nama"]: h["nota"] for h in laporan.kelompok_nota(nota, "bayar")}, {"Tunai": 1, "Transfer": 1})
        self.assertEqual(len(laporan.kelompok_nota(nota, "hari")), 2)

    def test_modal_kosong_ditandai(self):
        from master.layanan import laporan
        TransactionDetail.objects.create(sync_id=self.nota.sync_id, item_name="Kikir", variant="N/A", quantity=1,
                                         unit_price=500, discount=0, subtotal=500)
        nota, _ = self._ambil()
        self.assertEqual(laporan.ringkas(nota)["tanpa_modal"], 1)

    def test_halaman_dan_hak(self):
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(reverse("master:laporan")).status_code, 403)
        self.assertEqual(self.client.get(reverse("master:nota")).status_code, 403)
        self.client.force_login(self.admin)
        for lap in ["ringkasan", "barang", "kategori", "brand", "kasir", "bayar"]:
            r = self.client.get(reverse("master:laporan") + f"?periode=7&lap={lap}")
            self.assertEqual(r.status_code, 200, lap)
        self.assertIn("Palu", self.client.get(reverse("master:laporan") + "?periode=7&lap=barang&rinci=1").content.decode())
        r = self.client.get(reverse("master:laporan") + "?periode=7&lap=barang&export=1")
        self.assertEqual([c.value for c in load_workbook(BytesIO(r.content)).active[3]][:2], ["Palu", 4])
        isi = self.client.get(reverse("master:nota") + "?periode=7").content.decode()
        self.assertIn("T02-000001", isi)
        self.assertNotIn("T02-000001", self.client.get(reverse("master:nota") + "?periode=hari").content.decode())
        self.assertIn("T02-000001", self.client.get(reverse("master:nota") + "?periode=7&q=besar").content.decode())
        self.assertNotIn("T01-000001", self.client.get(reverse("master:nota") + "?periode=7&kasir=ID002").content.decode())
        panel = self.client.get(reverse("master:nota_panel", args=[self.t2.sync_id])).content.decode()
        self.assertIn("Palu", panel)
        self.assertIn("Ongkir", panel)

    def test_bukti_transfer_perlu_login_dan_berkas(self):
        from pos.models import PaymentProof
        bukti = PaymentProof.objects.create(proof_id=uuid.uuid4(), sync_id=self.t2.sync_id, object_key="payment_proofs/tidak-ada.jpg",
                                            content_type="image/jpeg", byte_size=10, sha256="a" * 64)
        alamat = reverse("master:nota_bukti", args=[bukti.proof_id])
        self.assertEqual(self.client.get(alamat).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(alamat).status_code, 404)


@override_settings(STORAGES=PENYIMPANAN_UJI)
class NotaManualTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.models import Toko
        cls.t1 = Toko.objects.get(stok_di_kasir=True)
        cls.t2 = Toko.objects.create(kode="T2", nama="Toko Dua")

    def _data(self, baris=None, **lain):
        baris = baris or [{"id": self.info("Palu", "Kecil").id, "jumlah": 3, "harga": 1500},
                          {"id": self.info("Palu", "Besar").id, "jumlah": 1, "harga": "2.500"}]
        return {"tanggal": timezone.localdate().isoformat(), "nomor_tulisan": "0451", "pembeli": "Pak Budi",
                "cara_bayar": "cash", "ongkir": "5.000", "baris": json.dumps(baris), **lain}

    def test_simpan_kurangi_stok_dan_bekukan_modal(self):
        from master.layanan import nota_manual
        from master.models import MutasiStok
        n = nota_manual.buat(self.admin, self._data())
        self.assertEqual((n.toko, n.total_barang, n.total, n.nomor[:3]), (self.t1, 7000, 12000, "NM-"))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 7)
        self.assertEqual(MutasiStok.objects.filter(jenis="jual_manual").count(), 2)
        self.assertEqual(n.baris.get(variant="Kecil").modal_satuan, 800)

    def test_toko_tanpa_kasir_tidak_ubah_stok(self):
        from master.layanan import nota_manual
        nota_manual.buat(self.admin, self._data(toko=self.t2.id))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)

    def test_batal_kembalikan_stok_dan_hilang_dari_laporan(self):
        from master.layanan import laporan
        from master.layanan import nota_manual
        n = nota_manual.buat(self.admin, self._data())
        hari = timezone.localdate()
        nota, _ = laporan.ambil(*laporan.rentang(hari, hari), sumber="manual")
        self.assertEqual([x["manual_id"] for x in nota], [n.id])
        with self.assertRaises(Tolak):
            nota_manual.batalkan(self.admin, n, " ")
        nota_manual.batalkan(self.admin, n, "salah input")
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        self.assertEqual(laporan.ambil(*laporan.rentang(hari, hari), sumber="manual")[0], [])
        with self.assertRaises(Tolak):
            nota_manual.batalkan(self.admin, n, "lagi")

    def test_validasi(self):
        from datetime import timedelta
        from master.layanan import nota_manual
        from master.models import NotaManual
        besok = (timezone.localdate() + timedelta(days=1)).isoformat()
        kecil = self.info("Palu", "Kecil").id
        for salah in ({"baris": "[]"}, {"baris": "rusak"}, {"tanggal": besok},
                      {"baris": json.dumps([{"id": kecil, "jumlah": 0, "harga": 1}])},
                      {"baris": json.dumps([{"id": kecil, "jumlah": 1, "harga": 1}, {"id": kecil, "jumlah": 2, "harga": 1}])},
                      {"baris": json.dumps([{"id": 999999, "jumlah": 1, "harga": 1}])}):
            with self.assertRaises(Tolak):
                nota_manual.buat(self.admin, self._data(**salah))
        nota_manual.buat(self.admin, self._data())
        with self.assertRaises(Tolak):
            nota_manual.buat(self.admin, self._data())                       # nomor kertas sama di toko sama
        nota_manual.buat(self.admin, self._data(toko=self.t2.id))            # toko lain: boleh
        self.assertEqual(NotaManual.objects.count(), 2)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 7)

    def test_masuk_laporan_bersama_nota_kasir(self):
        from master.layanan import laporan
        from master.layanan import nota_manual
        nota_manual.buat(self.admin, self._data())
        hari = timezone.localdate()
        nota, baris = laporan.ambil(*laporan.rentang(hari, hari))
        self.assertEqual(len(nota), 2)                                        # 1 kasir + 1 manual
        r = laporan.ringkas(nota)
        # kasir: 3.000 (laba 1.400); manual: 7.000, modal 3x800 + 1x2.000 = 4.400 -> laba 2.600
        self.assertEqual((r["omzet"], r["laba"], r["ongkir"]), (10000, 4000, 5000))
        self.assertEqual({h["nama"] for h in laporan.kelompok_nota(nota, "kasir")}, {"Rusdi", "Nota tulis tangan"})
        self.assertEqual(len(laporan.ambil(*laporan.rentang(hari, hari), toko=self.t2)[0]), 0)   # toko 2: tidak ada nota kasir
        self.assertEqual(len(laporan.ambil(*laporan.rentang(hari, hari), kasir="ID001")[0]), 1)

    def test_perbaiki_keterangan_tanpa_sentuh_stok(self):
        from datetime import timedelta
        from master.layanan import laporan, nota_manual
        from master.models import Log
        n = nota_manual.buat(self.admin, self._data())
        lain = nota_manual.buat(self.admin, self._data(nomor_tulisan="0452"))
        kemarin = timezone.localdate() - timedelta(days=1)
        with self.assertRaises(Tolak):
            nota_manual.ubah(self.admin, n, {"tanggal": kemarin.isoformat()}, " ")          # tanpa alasan
        with self.assertRaises(Tolak):
            nota_manual.ubah(self.admin, n, {"tanggal": (timezone.localdate() + timedelta(days=1)).isoformat()}, "x")
        with self.assertRaises(Tolak):
            nota_manual.ubah(self.admin, n, {"nomor_tulisan": "0452"}, "x")                 # nomor kertas kembar
        kolom = nota_manual.ubah(self.admin, n, {"tanggal": kemarin.isoformat(), "nomor_tulisan": "0451",
                                                 "pembeli": "Bu Sri", "cara_bayar": "bank_transfer"}, "salah input")
        self.assertEqual(kolom, ["tanggal", "pembeli", "cara_bayar"])
        n.refresh_from_db()
        self.assertEqual((n.tanggal, n.pembeli, n.cara_bayar, n.nomor[:3]), (kemarin, "Bu Sri", "bank_transfer", "NM-"))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 4)       # 10 - 3 - 3, tidak berubah
        self.assertEqual(n.baris.get(variant="Kecil").modal_satuan, 800)
        self.assertEqual([x["manual_id"] for x in laporan.ambil(*laporan.rentang(kemarin, kemarin), sumber="manual")[0]], [n.id])
        log = Log.objects.get(kolom="tanggal nota")
        self.assertEqual((log.catatan, log.baru), ("salah input", f"{n.nomor}: {kemarin}"))
        self.assertEqual(nota_manual.ubah(self.admin, n, {"pembeli": "Bu Sri"}, "x"), [])
        nota_manual.batalkan(self.admin, lain, "dobel")
        with self.assertRaises(Tolak):
            nota_manual.ubah(self.admin, lain, {"pembeli": "x"}, "x")

    def test_ganti_tanggal_massal_lewat_layar(self):
        from datetime import timedelta
        from master.layanan import nota_manual
        from master.models import NotaManual
        a = nota_manual.buat(self.admin, self._data())
        b = nota_manual.buat(self.admin, self._data(nomor_tulisan="0452"))
        c = nota_manual.buat(self.admin, self._data(nomor_tulisan="0453"))
        nota_manual.batalkan(self.admin, c, "dobel")
        tgl = (timezone.localdate() - timedelta(days=3)).isoformat()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:nota_manual") + f"?tanggal={timezone.localdate().isoformat()}")
        self.assertContains(r, 'name="pilih"', count=2)
        r = self.client.post(reverse("master:nota_manual"), {"pilih": [a.id, b.id, c.id], "tanggal": tgl, "alasan": "salah tanggal"}, follow=True)
        self.assertContains(r, "Tanggal 2 nota diganti")
        self.assertEqual({str(x.tanggal) for x in NotaManual.objects.filter(pk__in=[a.id, b.id])}, {tgl})
        self.assertNotEqual(str(NotaManual.objects.get(pk=c.id).tanggal), tgl)
        r = self.client.post(reverse("master:nota_manual_detail", args=[a.id]), {
            "aksi": "ubah", "tanggal": tgl, "nomor_tulisan": "0451", "pembeli": "Pak Budi", "cara_bayar": "edc", "alasan": "salah"}, follow=True)
        self.assertContains(r, "Tersimpan: cara bayar")
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 4)
        # tanggal terbawa ke nota berikutnya disorot
        r = self.client.get(reverse("master:nota_manual_baru") + f"?tanggal={tgl}")
        self.assertContains(r, "Tanggal dari nota sebelumnya")
        self.assertContains(r, "Ctrl+Enter")
        self.assertNotContains(self.client.get(reverse("master:nota_manual_baru")), "Tanggal dari nota sebelumnya")

    def test_lewat_layar(self):
        from master.models import NotaManual
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("master:nota_manual_baru")).status_code, 200)
        cari = self.client.get(reverse("master:nota_manual_cari") + "?q=palu").json()["hasil"]
        self.assertEqual({h["harga"] for h in cari}, {1500, 2500})
        r = self.client.post(reverse("master:nota_manual_baru"), {**self._data(), "foto": SimpleUploadedFile("n.jpg", b"\xff\xd8\xffx", content_type="image/jpeg")}, follow=True)
        self.assertContains(r, "tersimpan, total 12.000")
        n = NotaManual.objects.get()
        self.assertEqual(n.foto.count(), 1)
        self.assertContains(self.client.get(reverse("master:nota")), "manual")
        self.assertContains(self.client.get(reverse("master:nota_manual_panel", args=[n.id])), "Pak Budi")
        self.assertEqual(self.client.get(reverse("master:nota_manual_foto", args=[n.foto.get().id])).status_code, 200)
        self.assertContains(self.client.get(reverse("master:laporan") + "?sumber=manual"), "Palu")
        self.assertContains(self.client.get(reverse("master:dashboard")), "15.000")    # 3.000 kasir + 12.000 manual
        r = self.client.post(reverse("master:nota_manual_baru"), self._data(baris=json.dumps([])), follow=True)
        self.assertContains(r, "minimal satu barang")
