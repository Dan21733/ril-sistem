"""DP, piutang pelanggan, ambil nanti / pesan dulu (rancangan 31 Okt 2026)."""
import json
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from pos.models import Item

from master.layanan.inti import Tolak
from master.tests.dasar import Dasar


class PiutangTest(Dasar):
    """Nota contoh: Palu Kecil 3 x 1.500 + Palu Besar 1 x 2.500 = 7.000 barang, ongkir 5.000, total 12.000.
    DP minimal bawaan 50% dari harga barang = 3.500."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.models import Pelanggan, Toko
        cls.t1 = Toko.objects.get(stok_di_kasir=True)
        cls.tempo = Pelanggan.objects.create(nama="CV Maju", telepon="08123", boleh_tempo=True, batas_piutang=20000)

    def _data(self, **lain):
        baris = [{"id": self.info("Palu", "Kecil").id, "jumlah": 3, "harga": 1500},
                 {"id": self.info("Palu", "Besar").id, "jumlah": 1, "harga": 2500}]
        return {"tanggal": timezone.localdate().isoformat(), "pembeli": "Pak Budi", "cara_bayar": "cash",
                "ongkir": "5.000", "baris": json.dumps(baris), **lain}

    def _buat(self, pengguna=None, **lain):
        from master.layanan import nota_manual
        return nota_manual.buat(pengguna or self.admin, self._data(**lain))

    def _stok(self, varian="Kecil"):
        return Item.objects.get(item_name="Palu", variant=varian).in_stock

    def test_lunas_dibawa_tanpa_piutang(self):
        from master.models import Piutang
        self._buat()
        self.assertFalse(Piutang.objects.exists())

    def test_dp_minimal_dan_sisa(self):
        n = self._buat(dibayar="3.500", telepon="0811")
        p = n.piutang
        self.assertEqual((p.total, p.dibayar, p.sisa, p.acc_status, p.nama), (12000, 3500, 8500, "", "Pak Budi"))
        self.assertEqual(p.jatuh_tempo, timezone.localdate() + timedelta(days=14))
        self.assertEqual(p.pembayaran.get().jumlah, 3500)
        self.assertEqual(self._stok(), 7)

    def test_dp_kurang_ditolak_kecuali_pelanggan_tempo(self):
        from master.models import NotaManual
        with self.assertRaisesMessage(Tolak, "DP minimal Rp3.500"):
            self._buat(dibayar="3.000")
        self.assertFalse(NotaManual.objects.exists())
        self.assertEqual(self._stok(), 10)
        p = self._buat(self.finance, dibayar="0", pelanggan=self.tempo.id, pembeli="").piutang
        self.assertEqual((p.jenis_acc, p.acc_status, p.nama, p.telepon), ("invoice", "menunggu", "CV Maju", "08123"))

    def test_dp_per_barang_lebih_tinggi(self):
        from master.models import ItemInfo
        ItemInfo.objects.filter(item_name="Palu").update(min_dp_persen=80)
        with self.assertRaisesMessage(Tolak, "DP minimal Rp5.600"):
            self._buat(dibayar="5.000")

    def test_batas_piutang_tempo(self):
        self._buat(dibayar="0", pelanggan=self.tempo.id)            # sisa 12.000
        with self.assertRaisesMessage(Tolak, "Melewati batas piutang"):
            self._buat(dibayar="0", pelanggan=self.tempo.id)        # 24.000 > 20.000

    def test_belum_lunas_wajib_nama(self):
        with self.assertRaisesMessage(Tolak, "wajib ada nama pembeli"):
            self._buat(dibayar="5000", pembeli="")

    def test_titip_barang_butuh_acc_owner_otomatis(self):
        p = self._buat(status_barang="ambil").piutang
        self.assertEqual((p.jenis_acc, p.acc_status, p.lunas, p.barang_tertunda), ("titip", "disetujui", True, True))
        p2 = self._buat(self.finance, status_barang="ambil").piutang
        self.assertEqual(p2.acc_status, "menunggu")
        from master.layanan import piutang as svc
        with self.assertRaisesMessage(Tolak, "menunggu ACC"):
            svc.serahkan(self.finance, p2)
        svc.acc(self.admin, p2, setuju=True)
        svc.serahkan(self.finance, p2)
        p2.refresh_from_db()
        self.assertIsNotNone(p2.barang_selesai)

    def test_pesan_stok_berkurang_saat_diserahkan(self):
        from master.layanan import piutang as svc
        p = self._buat(status_barang="pesan", dibayar="6000").piutang
        self.assertEqual((self._stok(), p.stok_dikurangi), (10, False))
        with self.assertRaisesMessage(Tolak, "Masih kurang Rp6.000"):
            svc.serahkan(self.admin, p)
        svc.bayar(self.admin, p, jumlah="6.000", cara="bank_transfer")
        svc.serahkan(self.admin, p)
        p.refresh_from_db()
        self.assertEqual((self._stok(), self._stok("Besar"), p.stok_dikurangi, p.lunas), (7, 9, True, True))

    def test_batal_pesanan_belum_diserahkan_tidak_tambah_stok(self):
        from master.layanan import nota_manual
        n = self._buat(status_barang="pesan", dibayar="6000")
        nota_manual.batalkan(self.admin, n, "salah input")
        n.piutang.refresh_from_db()
        self.assertEqual((self._stok(), n.piutang.batal), (10, True))

    def test_bayar_tidak_boleh_lebih_dari_sisa(self):
        from master.layanan import piutang as svc
        p = self._buat(dibayar="4000").piutang
        with self.assertRaisesMessage(Tolak, "Lebih dari sisa"):
            svc.bayar(self.admin, p, jumlah="9000")
        svc.bayar(self.admin, p, jumlah="3000")
        svc.bayar(self.admin, p, jumlah="5000")
        p.refresh_from_db()
        self.assertTrue(p.lunas)
        self.assertEqual(p.pembayaran.count(), 3)

    def test_tolak_wajib_alasan(self):
        from master.layanan import piutang as svc
        p = self._buat(self.finance, status_barang="ambil").piutang
        with self.assertRaisesMessage(Tolak, "alasan"):
            svc.acc(self.admin, p, setuju=False)
        svc.acc(self.admin, p, setuju=False, catatan="tidak kenal")
        p.refresh_from_db()
        self.assertEqual(p.acc_status, "ditolak")

    def test_halaman_master(self):
        self.client.force_login(self.admin)
        p = self._buat(dibayar="4000", telepon="0811").piutang
        r = self.client.get(reverse("master:piutang"))
        self.assertContains(r, p.nomor)
        r = self.client.get(reverse("master:piutang_detail", args=[p.id]))
        self.assertContains(r, "Catat pembayaran")
        self.assertContains(r, "wa.me")
        r = self.client.post(reverse("master:piutang_detail", args=[p.id]), {"aksi": "bayar", "jumlah": "8.000", "cara": "cash"})
        p.refresh_from_db()
        self.assertTrue(p.lunas)
        self.assertContains(self.client.get(reverse("master:piutang_cetak", args=[p.id])), "BUKTI PELUNASAN")
        self.assertContains(self.client.get(reverse("master:nota_manual_detail", args=[p.nota_manual_id])), "Buka piutang")
        r = self.client.post(reverse("master:pelanggan"), {"nama": "Bu Ani", "telepon": "0899", "boleh_tempo": "1", "aktif": "1"})
        self.assertContains(self.client.get(reverse("master:pelanggan")), "Bu Ani")
        self.assertContains(self.client.get(reverse("master:nota_manual_baru")), "DP minimal")

    def test_hp_dan_push(self):
        from master.layanan import push
        from master.tests.test_hp import masuk_hp
        masuk_hp(self.client, self.admin)
        p = self._buat(self.finance, status_barang="ambil").piutang
        self.assertIn(f"piutang:{p.id}", [s[0] for s in push.menunggu()])
        self.assertContains(self.client.get(reverse("hp:beranda")), "Titip barang")
        r = self.client.post(reverse("hp:acc"), json.dumps({"jenis": "piutang", "id": p.id, "aksi": "terima"}),
                             content_type="application/json")
        self.assertTrue(r.json()["ok"], r.json())
        r = self.client.post(reverse("hp:piutang_detail", args=[p.id]), json.dumps({"aksi": "serahkan"}),
                             content_type="application/json")
        self.assertTrue(r.json()["ok"], r.json())
        self.assertContains(self.client.get(reverse("hp:piutang") + "?tab=selesai"), p.nomor)

    def test_item_min_dp_dan_aturan_toko(self):
        from master.models import ItemInfo, Pengaturan
        self.client.force_login(self.admin)
        ii = ItemInfo.objects.get(item_name="Palu")
        self.client.post(reverse("master:item", args=[ii.id]), {"aksi": "min_dp", "min_dp_persen": "70"})
        ii.refresh_from_db()
        self.assertEqual(ii.min_dp_persen, 70)
        self.client.post(reverse("master:toko"), {"aksi": "dp", "dp_minimal_persen": "40", "jatuh_tempo_hari": "7"})
        a = Pengaturan.ambil()
        self.assertEqual((a.dp_minimal_persen, a.jatuh_tempo_hari), (40, 7))
