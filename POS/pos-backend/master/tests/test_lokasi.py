"""Uji: Lokasi stok, alur per lokasi, tiket admin, pengambilan barang, selisih."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.akun.akses import PERAN, SEMUA_HAK
from master.layanan.inti import Tolak
from master.models import ItemInfo, Rak, VarianInfo
from master.tests.dasar import Dasar, buat_item


class LokasiTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.layanan import lokasi
        from master.akun.akses import SEMUA_HAK
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="ga", nama="Gudang A")
        cls.gb = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GB", nama="Gudang B")

    def test_pindah_toko_ke_gudang_dan_antar_gudang(self):
        from master.layanan import lokasi
        from master.models import MutasiLokasi
        info = self.info("Palu", "Kecil")  # total 10
        lokasi.pindah(self.admin, info, dari=self.toko, ke=self.ga, jumlah=6, hak=SEMUA_HAK)
        lokasi.pindah(self.admin, info, dari=self.ga, ke=self.gb, jumlah="2", hak=SEMUA_HAK)
        r = lokasi.rincian(info)
        self.assertEqual((r[self.toko.id], r[self.ga.id], r[self.gb.id]), (4, 4, 2))
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)  # total tetap
        self.assertEqual(MutasiLokasi.objects.count(), 2)
        self.assertEqual(self.ga.kode, "GA")

    def test_tidak_boleh_melebihi_stok_asal(self):
        from master.layanan import lokasi
        info = self.info("Palu", "Kecil")
        with self.assertRaises(Tolak):
            lokasi.pindah(self.admin, info, dari=self.toko, ke=self.ga, jumlah=11, hak=SEMUA_HAK)
        with self.assertRaises(Tolak):
            lokasi.pindah(self.admin, info, dari=self.ga, ke=self.toko, jumlah=1, hak=SEMUA_HAK)
        with self.assertRaises(Tolak):
            lokasi.pindah(self.finance, info, dari=self.toko, ke=self.ga, jumlah=1, hak=PERAN["Finance"])

    def test_toko_minus_kalau_terjual_tanpa_ambil_gudang(self):
        from master.layanan import lokasi
        info = self.info("Palu", "Kecil")
        lokasi.pindah(self.admin, info, dari=self.toko, ke=self.ga, jumlah=8, hak=SEMUA_HAK)
        Item.objects.filter(item_name="Palu", variant="Kecil").update(in_stock=5)  # kasir menjual 5
        self.assertEqual(lokasi.rincian(info)[self.toko.id], -3)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:stok_lokasi") + "?toko=minus")
        self.assertContains(r, "Kecil")
        self.assertContains(r, "Gudang A")

    def test_gudang_berisi_tidak_bisa_dinonaktifkan(self):
        from master.layanan import lokasi
        lokasi.pindah(self.admin, self.info("Kikir"), dari=self.toko, ke=self.ga, jumlah=1, hak=SEMUA_HAK)
        with self.assertRaises(Tolak):
            lokasi.ubah_aktif(self.admin, self.ga, hak=SEMUA_HAK)
        lokasi.ubah_aktif(self.admin, self.gb, hak=SEMUA_HAK)
        self.gb.refresh_from_db()
        self.assertFalse(self.gb.aktif)

    def test_halaman(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:lokasi")), "Gudang A")
        info = self.info("Palu", "Besar")
        r = self.client.post(reverse("master:stok_lokasi_pindah", args=[info.id]),
                             {"dari": self.toko.id, "ke": self.gb.id, "jumlah": "3"})
        self.assertEqual(r.status_code, 302)
        r = self.client.get(reverse("master:stok_lokasi_pindah", args=[info.id]))
        self.assertContains(r, "Riwayat pindah")
        r = self.client.get(reverse("master:stok_lokasi") + f"?lokasi={self.gb.id}")
        self.assertContains(r, "Besar")
        self.assertNotContains(r, "Kecil")
        self.client.post(reverse("master:rak"), {"aksi": "simpan", "kode": "TK-01", "nama": "Rak Hardware", "tempat": self.ga.id})
        self.assertEqual(Rak.objects.get(kode="TK-01").tempat, self.ga)
        self.assertContains(self.client.get(reverse("master:rak")), "Gudang A")
        # Halaman rak per lokasi (13 Nov 2026): satu lokasi sekali tampil, simpan pindah ke tab lokasi barunya.
        r = self.client.post(reverse("master:rak") + f"?lokasi={self.toko.id}",
                             {"aksi": "simpan", "kode": "TK-02", "nama": "Rak Toko Kanan", "tempat": self.toko.id})
        self.assertRedirects(r, reverse("master:rak") + f"?lokasi={self.toko.id}", fetch_redirect_response=False)
        r = self.client.get(reverse("master:rak") + f"?lokasi={self.toko.id}")
        self.assertContains(r, "Rak Toko Kanan")
        self.assertNotContains(r, "Rak Hardware")
        r = self.client.get(reverse("master:rak") + f"?lokasi={self.ga.id}")
        self.assertContains(r, "Rak Hardware")
        self.assertNotContains(r, "Rak Toko Kanan")
        r = self.client.post(reverse("master:rak") + f"?lokasi={self.toko.id}",
                             {"aksi": "simpan", "kode": "TK-02", "nama": "Rak Toko Kanan", "tempat": self.gb.id})
        self.assertRedirects(r, reverse("master:rak") + f"?lokasi={self.gb.id}", fetch_redirect_response=False)
        # Rak bertingkat sekaligus: TK1 x 3 -> TK1-1..TK1-3; tambah jadi 5 hanya membuat TK1-4, TK1-5.
        self.client.post(reverse("master:rak"), {"aksi": "bertingkat", "kode": "tk1", "tingkat": "3",
                                                 "nama": "Rak Toko Kanan", "tempat": self.toko.id})
        self.assertEqual(sorted(Rak.objects.filter(kode__startswith="TK1-").values_list("kode", flat=True)),
                         ["TK1-1", "TK1-2", "TK1-3"])
        r = self.client.post(reverse("master:rak"), {"aksi": "bertingkat", "kode": "TK1", "tingkat": "5",
                                                     "nama": "Rak Toko Kanan", "tempat": self.toko.id}, follow=True)
        self.assertContains(r, "2 rak dibuat: TK1-4 sampai TK1-5. 3 sudah ada, dilewati.")
        self.assertEqual(Rak.objects.get(kode="TK1-5").tempat, self.toko)
        self.client.post(reverse("master:rak"), {"aksi": "bertingkat", "kode": "X", "tingkat": "31", "nama": "x"})
        self.assertFalse(Rak.objects.filter(kode__startswith="X-").exists())

    def test_jalan_ke_pindah_dari_daftar_barang_dan_stok_kosong(self):
        self.client.force_login(self.admin)
        info = self.info("Palu", "Kecil")
        url = reverse("master:stok_lokasi_pindah", args=[info.id])
        r = self.client.get(reverse("master:item", args=[ItemInfo.objects.get(item_name="Palu").id]))
        self.assertContains(r, url)                                   # angka stok di panel barang bisa diklik
        r = self.client.get(reverse("master:stok_lokasi") + "?q=palu")
        self.assertContains(r, url)
        Item.objects.filter(item_name="Palu", variant="Kecil").update(in_stock=0)
        r = self.client.get(url)
        self.assertContains(r, "masih 0")
        self.assertNotContains(r, "<h3>Pindahkan</h3>", html=False)

    def test_sampah_dengan_stok_gudang_tidak_bisa_hapus_permanen(self):
        from master.layanan import lokasi
        from master.layanan import sampah
        from master.models import Sampah
        info = self.info("Kikir")
        lokasi.pindah(self.admin, info, dari=self.toko, ke=self.ga, jumlah=2, hak=SEMUA_HAK)
        sampah.buang(self.admin, info, hak=SEMUA_HAK)
        with self.assertRaises(Tolak) as e:
            sampah.hapus_permanen(self.admin, Sampah.objects.get(varian=info), hak=SEMUA_HAK, password="rahasia-123")
        self.assertIn("gudang", str(e.exception))


class LokasiAlurTest(Dasar):
    """Tahap 2: barang datang, opname, buka dus, dan hitung acak per lokasi."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.layanan import lokasi
        from master.models import Supplier
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A")
        cls.supplier = Supplier.objects.create(nama="PT A")
        Group.objects.get_or_create(name="Gudang")

    def _gudang(self):
        from master.models import StokLokasi
        return {(s.varian.variant, s.lokasi.kode): s.jumlah for s in StokLokasi.objects.select_related("varian", "lokasi")}

    def test_barang_datang_ke_gudang(self):
        from master.layanan import pembelian as svc
        from master.models import MutasiStok
        po = svc.buat(self.admin, supplier_id=self.supplier.id, tanggal_pesan="2026-10-01")
        svc.tambah_baris(po, varian_id=self.info("Palu", "Kecil").id, jumlah=6, harga=900)
        svc.tambah_baris(po, varian_id=self.info("Palu", "Besar").id, jumlah=2, harga=2000)
        kecil, besar = po.baris.order_by("id")
        self.client.force_login(self.gudang)
        # Belum ditugaskan: tidak muncul dan tidak bisa dibuka (9 Nov 2026).
        self.assertEqual(self.client.get(reverse("gudang:datang_detail", args=[po.id])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("gudang:tiket")), po.nomor)
        svc.atur_penerima(self.admin, po, penerima=str(self.gudang.id))
        self.assertContains(self.client.get(reverse("gudang:tiket")), po.nomor)
        r = self.client.get(reverse("gudang:datang_detail", args=[po.id]))
        self.assertContains(r, ">Gudang A</option>")
        self.client.post(reverse("gudang:datang_detail", args=[po.id]), {
            f"terima_{kecil.id}": "6", f"terima_{besar.id}": "2", "tanggal_terima": "2026-10-02",
            f"lokasi_{kecil.id}": self.ga.id, f"lokasi_{besar.id}": self.toko.id})
        po.refresh_from_db()
        self.assertEqual(po.status, "menunggu")
        self.assertEqual(self._gudang(), {})  # stok baru berubah saat ACC
        svc.acc(self.admin, po)
        self.assertEqual(self._gudang(), {("Kecil", "GA"): 6})
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 16)
        self.assertEqual(MutasiStok.objects.get(jenis="beli", variant="Kecil").lokasi, self.ga)
        self.assertIsNone(MutasiStok.objects.get(jenis="beli", variant="Besar").lokasi)

    def test_opname_di_gudang_mengubah_gudang_dan_total(self):
        from master.layanan import lokasi
        from master.layanan import opname
        info = self.info("Palu", "Kecil")  # total 10
        lokasi.pindah(self.admin, info, dari=self.toko, ke=self.ga, jumlah=7, hak=SEMUA_HAK)
        opname.simpan_hitungan(self.gudang, {f"hitung_{info.id}": "5"}, lokasi=self.ga)
        op = opname.ajukan(self.gudang, lokasi=self.ga)
        b = op.baris.get()
        self.assertEqual((op.lokasi, b.stok_saat_hitung, b.selisih), (self.ga, 7, -2))
        opname.acc(self.admin, op, [b.id], hak=SEMUA_HAK)
        self.assertEqual(self._gudang(), {("Kecil", "GA"): 5})
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 8)
        self.assertEqual(lokasi.rincian(info)[self.toko.id], 3)  # toko tidak tersentuh

    def test_opname_toko_memakai_stok_toko(self):
        from master.layanan import lokasi
        from master.layanan import opname
        info = self.info("Palu", "Kecil")
        lokasi.pindah(self.admin, info, dari=self.toko, ke=self.ga, jumlah=7, hak=SEMUA_HAK)
        opname.simpan_hitungan(self.gudang, {f"hitung_{info.id}": "3"})
        b = opname.ajukan(self.gudang).baris.get()
        self.assertEqual((b.stok_saat_hitung, b.selisih), (3, 0))

    def test_halaman_hitung_per_lokasi_tidak_bocor(self):
        from master.layanan import lokasi
        info = self.info("Palu", "Kecil")
        Rak.objects.filter(kode="TK-01").update(tempat=self.ga)
        VarianInfo.objects.filter(pk=info.pk).update(rak_id="TK-01")
        lokasi.pindah(self.admin, info, dari=self.toko, ke=self.ga, jumlah=7, hak=SEMUA_HAK)
        from datetime import timedelta
        from master.layanan import tiket
        t = tiket.buka(self.admin, untuk=self.gudang, tujuan="hitung gudang A", jenis="hitung", hak=SEMUA_HAK,
                       batas=timezone.now() + timedelta(hours=1), lokasi=self.ga.id, rak=["TK-01"])
        self.client.force_login(self.gudang)
        url = reverse("gudang:tiket_hitung", args=[t.id])
        r = self.client.get(url)
        self.assertContains(r, "di Gudang A")
        self.assertContains(r, "Kecil")
        self.assertNotContains(r, '"in_stock"')  # stok sistem tidak dikirim ke tablet
        self.assertNotContains(r, "Sistem")
        besar = self.info("Palu", "Besar")
        r = self.client.post(url, json.dumps({"entri": [
            {"kunci": "a", "varian": info.id, "rak": "TK-01", "jumlah": "7"},
            {"kunci": "b", "varian": besar.id, "rak": "TK-01", "jumlah": "2"}]}), content_type="application/json")
        d = r.json()
        self.assertEqual(d["ok"], ["a"])
        self.assertIn("di luar lingkup", d["gagal"][0]["pesan"])     # di luar rak yang ditugaskan
        from master.models import Opname
        op = Opname.objects.get(status="draf")
        self.assertEqual((op.lokasi, op.tiket_id, op.baris.count()), (self.ga, t.id, 1))
        self.assertEqual(op.baris.get().stok_saat_hitung, 7)

    def test_stok_ditutup_di_kasir_selama_tugas_hitung(self):
        from datetime import timedelta
        from master.layanan import tiket
        from master.models import Tiket
        from pos import data_master
        buat_item("Cat Avian", "Putih", kategori="Cat", stok=5)
        layanan.sinkron_info()
        VarianInfo.objects.filter(item_name="Palu", variant="Kecil").update(rak_id="TK-01")

        def stok():
            return {(i.item_name, i.variant): i.in_stock for i in data_master.tutup_stok(list(Item.objects.all()))}
        self.assertEqual(stok()[("Palu", "Kecil")], 10)                          # tanpa tugas: tampil
        t = tiket.buka(self.admin, untuk=self.gudang, tujuan="hitung rak", jenis="hitung", hak=SEMUA_HAK,
                       batas=timezone.now() + timedelta(hours=1), lokasi=self.ga.id, rak=["TK-01"])
        s = stok()
        self.assertIsNone(s[("Palu", "Kecil")])                                 # sedang dihitung
        self.assertEqual((s[("Palu", "Besar")], s[("Cat Avian", "Putih")]), (10, 5))
        # lewat batas: tampil lagi
        self.assertEqual({(i.item_name, i.variant): i.in_stock for i in data_master.tutup_stok(
            list(Item.objects.all()), sekarang=timezone.now() + timedelta(hours=2))}[("Palu", "Kecil")], 10)
        Tiket.objects.filter(pk=t.pk).update(status="dikirim")                  # sudah dikirim: tampil lagi
        self.assertEqual(stok()[("Palu", "Kecil")], 10)
        tiket.buka(self.admin, untuk=self.gudang, tujuan="hitung cat", jenis="hitung", hak=SEMUA_HAK,
                   batas=timezone.now() + timedelta(hours=1), lokasi=self.ga.id, kategori=["Cat"])
        s = stok()
        self.assertIsNone(s[("Cat Avian", "Putih")])
        self.assertEqual(s[("Palu", "Kecil")], 10)

    def test_buka_dus_dari_gudang_ke_toko(self):
        from master.layanan import bukadus
        from master.layanan import lokasi
        buat_item("Semen", "50 Kg", stok=10)
        buat_item("Semen", "Per Kg", stok=0)
        layanan.sinkron_info()
        dus, kg = self.info("Semen", "50 Kg"), self.info("Semen", "Per Kg")
        lokasi.pindah(self.admin, dus, dari=self.toko, ke=self.ga, jumlah=8, hak=SEMUA_HAK)
        k = bukadus.buat_konversi(self.admin, dari_id=dus.id, ke_id=kg.id, isi=50)
        bukadus.buka(self.gudang, k, jumlah=2, lokasi_dari=self.ga, lokasi_ke=self.toko)
        self.assertEqual(self._gudang(), {("50 Kg", "GA"): 6})
        self.assertEqual(lokasi.rincian(kg)[self.toko.id], 100)
        self.assertEqual(lokasi.rincian(dus)[self.toko.id], 2)

    def test_hitung_acak(self):
        from master.layanan import hitung_acak
        from master.layanan import lokasi
        info = self.info("Palu", "Kecil")
        lokasi.pindah(self.admin, info, dari=self.toko, ke=self.ga, jumlah=7, hak=SEMUA_HAK)
        h = hitung_acak.mulai(self.admin, lokasi=self.ga, jumlah=5, hak=SEMUA_HAK)
        b = h.baris.get()  # hanya satu barang yang punya stok di Gudang A
        self.assertEqual(b.varian, info)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:hitung_acak_detail", args=[h.id]))
        self.assertNotContains(r, ">7<")  # angka sistem disembunyikan selama menghitung
        with self.assertRaises(Tolak):
            hitung_acak.selesai(self.admin, h, {})
        hitung_acak.selesai(self.admin, h, {f"hitung_{b.id}": "5"})
        b.refresh_from_db()
        self.assertEqual((b.stok_sistem, b.selisih), (7, -2))
        hitung_acak.terapkan(self.admin, h, [b.id], hak=SEMUA_HAK)
        self.assertEqual(self._gudang(), {("Kecil", "GA"): 5})
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 8)
        r = self.client.get(reverse("master:hitung_acak"))
        self.assertContains(r, "Gudang A")
        with self.assertRaises(Tolak):
            hitung_acak.mulai(self.finance, lokasi=self.ga, hak=PERAN["Finance"])


class LokasiKeluarTest(Dasar):
    """Transfer, nota manual, retur, dan ubah stok manual per lokasi."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.layanan import lokasi
        from master.models import Toko
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A")
        cls.t1 = Toko.objects.get(stok_di_kasir=True)
        cls.t2 = Toko.objects.create(kode="T2", nama="Toko Dua")

    def setUp(self):
        from master.layanan import lokasi
        self.info_k = self.info("Palu", "Kecil")  # total 10
        lokasi.pindah(self.admin, self.info_k, dari=self.toko, ke=self.ga, jumlah=8, hak=SEMUA_HAK)

    def _ga(self):
        from master.models import StokLokasi
        return StokLokasi.objects.get(varian=self.info_k, lokasi=self.ga).jumlah

    def test_transfer_keluar_dari_gudang_dan_batal(self):
        from master.layanan import transfer
        tr = transfer.buat(self.admin, dari_id=self.t1.id, ke_id=self.t2.id, tanggal="2026-10-09")
        b = transfer.tambah_baris(tr, varian_id=self.info_k.id, jumlah=3)
        transfer.simpan_baris(tr, {f"qty_{b.id}": "3", f"lokasi_{b.id}": str(self.ga.id)})
        transfer.kirim(self.admin, tr)
        from master.layanan import ambil
        from master.layanan import lokasi
        p = tr.permintaan.get()
        self.assertEqual((self._ga(), p.jumlah, p.status), (8, 3, "menunggu"))  # gudang belum berkurang
        self.assertEqual(lokasi.rincian(self.info_k)[self.toko.id], 2)  # toko tidak jadi minus palsu
        ambil.konfirmasi(self.gudang, p)
        self.assertEqual(self._ga(), 5)
        transfer.batal(self.admin, tr, "salah kirim")
        self.assertEqual(self._ga(), 8)  # kembali ke gudang tempat diambil
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)

    def test_transfer_masuk_ke_gudang(self):
        from master.layanan import transfer
        tr = transfer.buat(self.admin, dari_id=self.t2.id, ke_id=self.t1.id, tanggal="2026-10-09")
        b = transfer.tambah_baris(tr, varian_id=self.info_k.id, jumlah=4)
        transfer.kirim(self.admin, tr)
        transfer.terima(self.admin, tr, {f"terima_{b.id}": "4", f"lokasi_{b.id}": str(self.ga.id)})
        self.assertEqual(self._ga(), 12)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("master:transfer_detail", args=[tr.id])).status_code, 200)

    def test_nota_manual_dari_gudang(self):
        from master.layanan import nota_manual
        n = nota_manual.buat(self.admin, {
            "tanggal": timezone.localdate().isoformat(), "cara_bayar": "cash", "lokasi": str(self.ga.id),
            "baris": json.dumps([{"id": self.info_k.id, "jumlah": 2, "harga": 1500}])})
        self.assertEqual(self._ga(), 8)  # menunggu diambil
        nota_manual.batalkan(self.admin, n, "salah")
        self.assertEqual(n.permintaan.get().status, "batal")
        self.assertEqual(self._ga(), 8)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:nota_manual_baru")), "Barang diambil dari")

    def test_retur_dari_lokasi_baris(self):
        from master.layanan import pembelian as svc
        from master.models import Supplier
        po = svc.buat(self.admin, supplier_id=Supplier.objects.create(nama="PT A").id, tanggal_pesan="2026-10-01")
        svc.tambah_baris(po, varian_id=self.info_k.id, jumlah=5, harga=900)
        b = po.baris.get()
        svc.catat_datang(self.admin, po, {"mode_ppn": "tanpa", "tanggal_terima": "2026-10-02", f"terima_{b.id}": "5",
                                          f"harga_{b.id}": "900", f"lokasi_{b.id}": str(self.ga.id)})
        svc.acc(self.admin, po)
        self.assertEqual(self._ga(), 13)
        svc.retur(self.admin, po, {"alasan": "pecah", "tanggal": "2026-10-03", f"retur_{b.id}": "2"})
        self.assertEqual(self._ga(), 11)

    def test_ubah_stok_per_lokasi(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("master:stok_ubah"), json.dumps(
            {"id": self.info_k.id, "nilai": "6", "jenis": "hitung", "lokasi": str(self.ga.id)}), content_type="application/json")
        self.assertEqual(r.json(), {"ok": True, "stok": 8, "selisih": -2})
        self.assertEqual(self._ga(), 6)
        self.assertContains(self.client.get(reverse("master:stok")), "Gudang A saja")


class TiketTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from datetime import timedelta
        from master.layanan import lokasi
        from master.models import HakAkun
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A")
        cls.gb = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GB", nama="Gudang B")
        cls.adm = get_user_model().objects.create_user("susi", password="rahasia-123")
        HakAkun.objects.create(user=cls.adm, hak=["gudang", "ops_lihat_stok", "ops_tiket_pindah", "ops_tiket_rak"])
        lokasi.pindah(cls.admin, cls.info(cls, "Palu", "Kecil"), dari=cls.toko, ke=cls.ga, jumlah=6, hak=SEMUA_HAK)
        lokasi.pindah(cls.admin, cls.info(cls, "Palu", "Besar"), dari=cls.toko, ke=cls.ga, jumlah=4, hak=SEMUA_HAK)
        VarianInfo.objects.filter(item_name="Palu").update(rak_id="TK-01")
        cls.batas = timezone.now() + timedelta(hours=2)

    def _tiket(self):
        from master.layanan import tiket
        return tiket.buka(self.admin, untuk=self.adm, tujuan="rotasi palu", batas=self.batas, hak=SEMUA_HAK)

    def test_di_luar_tiket_tidak_bisa_input(self):
        from datetime import timedelta
        from master.layanan import tiket
        t = self._tiket()
        lain = get_user_model().objects.create_user("lain", password="rahasia-123")
        with self.assertRaises(Tolak):
            tiket.tambah_pindah(lain, t, varian_id=self.info("Palu", "Kecil").id, dari=self.ga.id, ke=self.gb.id, jumlah=1)
        Tiket = t.__class__
        Tiket.objects.filter(pk=t.pk).update(batas=timezone.now() - timedelta(minutes=1))
        t.refresh_from_db()
        with self.assertRaises(Tolak):
            tiket.tambah_pindah(self.adm, t, varian_id=self.info("Palu", "Kecil").id, dari=self.ga.id, ke=self.gb.id, jumlah=1)
        with self.assertRaises(Tolak):
            tiket.buka(self.admin, untuk=self.finance, tujuan="x", batas=self.batas, hak=SEMUA_HAK)  # finance tak punya hak tiket

    def test_massal_lalu_acc_per_barang(self):
        from master.layanan import lokasi
        from master.layanan import tiket
        t = self._tiket()
        n = tiket.tambah_massal(self.adm, t, dari=self.ga.id, ke=self.gb.id, rak="TK-01", ikut_rak=True)
        self.assertEqual(n, 2)
        self.assertEqual(lokasi.rincian(self.info("Palu", "Kecil"))[self.ga.id], 6)  # belum berubah
        tiket.kirim(self.adm, t)
        with self.assertRaises(Tolak):
            tiket.tambah_rak(self.adm, t, varian_id=self.info("Kikir").id, rak="TK-01")  # sudah dikirim
        kecil = t.baris.get(jenis="pindah", varian=self.info("Palu", "Kecil"))
        besar = t.baris.get(jenis="pindah", varian=self.info("Palu", "Besar"))
        rak = t.baris.get(jenis="rak_lokasi")
        from django.http import QueryDict
        data = QueryDict(mutable=True)
        data.setlist("pilih", [str(kecil.id), str(rak.id)])
        data[f"jumlah_{kecil.id}"] = "5"  # pemilik mengubah jumlah
        ok, gagal = tiket.terapkan(self.admin, t, data, hak=SEMUA_HAK)
        self.assertEqual((ok, gagal), (2, 0))
        r = lokasi.rincian(self.info("Palu", "Kecil"))
        self.assertEqual((r[self.ga.id], r[self.gb.id]), (1, 5))
        self.assertEqual(lokasi.rincian(self.info("Palu", "Besar"))[self.ga.id], 4)  # tidak dicentang
        self.assertEqual(Rak.objects.get(kode="TK-01").tempat, self.gb)
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)  # total tetap
        t.refresh_from_db()
        self.assertEqual(t.status, "selesai")

    def test_halaman_admin_dan_pemilik(self):
        t = self._tiket()
        self.client.force_login(self.adm)
        r = self.client.get(reverse("gudang:beranda"))
        self.assertNotContains(r, "Hitung stok")
        self.assertContains(r, "Kerjakan")
        self.assertContains(r, t.tujuan)
        r = self.client.get(reverse("gudang:tiket_isi", args=[t.id]) + "?q=palu")
        self.assertContains(r, "Usulkan pindah")
        self.assertNotContains(r, "Gudang A 6")  # 9 Nov 2026: jumlah stok tidak pernah tampil di aplikasi gudang
        for angka in ("765.432", "800", "1.500"):
            self.assertNotContains(r, f">{angka}<")
        self.client.post(reverse("gudang:tiket_isi", args=[t.id]), {
            "aksi": "pindah", "varian_id": self.info("Palu", "Kecil").id, "dari": self.ga.id, "ke": self.toko.id, "jumlah": "2"})
        self.client.post(reverse("gudang:tiket_isi", args=[t.id]), {"aksi": "kirim"})
        t.refresh_from_db()
        self.assertEqual(t.status, "dikirim")
        self.assertEqual(self.client.get(reverse("master:tiket")).status_code, 302)  # bukan master
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:tiket_detail", args=[t.id]))
        self.assertContains(r, "ACC yang dicentang")
        b = t.baris.get()
        self.client.post(reverse("master:tiket_detail", args=[t.id]), {"aksi": "terapkan", "pilih": [b.id], f"jumlah_{b.id}": "2"})
        from master.layanan import lokasi
        self.assertEqual(lokasi.rincian(self.info("Palu", "Kecil"))[self.ga.id], 4)
        self.assertContains(self.client.get(reverse("master:tiket")), "Selesai")


class AmbilSelisihTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.layanan import lokasi
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A")

    def setUp(self):
        from master.layanan import lokasi
        self.kecil = self.info("Palu", "Kecil")  # total 10
        lokasi.pindah(self.admin, self.kecil, dari=self.toko, ke=self.ga, jumlah=8, hak=SEMUA_HAK)

    def _nota(self, jumlah=3):
        from master.layanan import nota_manual
        return nota_manual.buat(self.admin, {
            "tanggal": timezone.localdate().isoformat(), "cara_bayar": "cash", "lokasi": str(self.ga.id),
            "baris": json.dumps([{"id": self.kecil.id, "jumlah": jumlah, "harga": 1500}])})

    def test_kepala_gudang_konfirmasi(self):
        from master.layanan import lokasi
        n = self._nota()
        p = n.permintaan.get()
        self.client.force_login(self.gudang)
        r = self.client.get(reverse("gudang:beranda"))
        self.assertContains(r, "barang menunggu diambilkan")
        r = self.client.get(reverse("gudang:ambil"))
        self.assertContains(r, n.nomor)
        for angka in ("1.500", "800"):
            self.assertNotContains(r, angka)  # tanpa harga/modal
        # Jumlah beda tanpa catatan ditolak
        self.client.post(reverse("gudang:ambil"), {"referensi": n.nomor, f"diambil_{p.id}": "4", f"lokasi_{p.id}": self.ga.id})
        p.refresh_from_db()
        self.assertEqual(p.status, "menunggu")
        self.client.post(reverse("gudang:ambil"), {"referensi": n.nomor, f"diambil_{p.id}": "4", f"lokasi_{p.id}": self.ga.id,
                                                   f"catatan_{p.id}": "pembeli minta tambah 1"})
        p.refresh_from_db()
        self.assertEqual((p.status, p.diambil_jumlah, p.diambil_oleh), ("diambil", 4, "gudang"))
        r = lokasi.rincian(self.kecil)
        self.assertEqual((r[self.ga.id], r[self.toko.id]), (4, 3))  # 1 unit keluar tanpa nota -> toko terlihat lebih
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:ambil") + "?beda=1")
        self.assertContains(r, "pembeli minta tambah 1")
        r = self.client.get(reverse("master:laporan_selisih"))
        self.assertContains(r, "Ambil barang")
        self.assertContains(r, "Gudang A")

    def test_pemilik_konfirmasi(self):
        n = self._nota(2)
        p = n.permintaan.get()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:ambil")), n.nomor)
        self.client.post(reverse("master:ambil"), {"referensi": n.nomor, f"diambil_{p.id}": "2", f"lokasi_{p.id}": self.ga.id})
        p.refresh_from_db()
        self.assertTrue(p.oleh_pemilik)
        from master.models import StokLokasi
        self.assertEqual(StokLokasi.objects.get(varian=self.kecil, lokasi=self.ga).jumlah, 6)

    def test_laporan_selisih_menggabungkan_sumber(self):
        from master.layanan import hitung_acak
        from master.layanan import opname
        from master.layanan import selisih
        opname.simpan_hitungan(self.gudang, {f"hitung_{self.kecil.id}": "6"}, lokasi=self.ga)
        op = opname.ajukan(self.gudang, lokasi=self.ga)
        opname.acc(self.admin, op, [op.baris.get().id], hak=SEMUA_HAK)
        h = hitung_acak.mulai(self.admin, lokasi=self.ga, jumlah=1, hak=SEMUA_HAK)
        hitung_acak.selesai(self.admin, h, {f"hitung_{h.baris.get().id}": "5"})
        hari = timezone.localdate()
        r = selisih.ringkas(hari, hari)
        self.assertEqual(r["jumlah"], 2)
        ga = next(x for x in r["lokasi"] if x["nama"] == "Gudang A")
        self.assertEqual((ga["kejadian"], ga["kurang"]), (2, 3))
        self.assertEqual(ga["nilai_kurang"], 3 * 800)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:laporan_selisih")), "Cek stok acak #")


class HitungPosisiTest(Dasar):
    """Tugas hitung biasa mencatat rak tiap hitungan; ACC memperbarui posisi rak (13 Nov 2026)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from master.layanan import lokasi
        cls.toko = lokasi.lokasi_toko()
        cls.ga = lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A")
        Group.objects.get_or_create(name="Gudang")
        Rak.objects.create(kode="GA-1", nama="GA-1", tempat=cls.ga)
        Rak.objects.create(kode="GA-2", nama="GA-2", tempat=cls.ga)

    def setUp(self):
        from master.layanan import lokasi
        self.kecil = self.info("Palu", "Kecil")              # total 10
        VarianInfo.objects.filter(pk=self.kecil.pk).update(rak_id="GA-1")
        lokasi.pindah(self.admin, self.kecil, dari=self.toko, ke=self.ga, jumlah=7, hak=SEMUA_HAK)

    def _tugas(self, **kw):
        from datetime import timedelta
        from master.layanan import tiket
        return tiket.buka(self.admin, untuk=self.gudang, tujuan="hitung gudang A", jenis="hitung", hak=SEMUA_HAK,
                          batas=timezone.now() + timedelta(hours=1), lokasi=self.ga.id, **kw)

    def _kirim(self, t, entri):
        self.client.force_login(self.gudang)
        r = self.client.post(reverse("gudang:tiket_hitung", args=[t.id]), json.dumps({"entri": entri}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_satu_barang_dua_rak_dijumlah_dan_bisa_dikoreksi(self):
        from master.layanan import opname
        t = self._tugas()
        self.assertTrue(t.saring["posisi"])
        d = self._kirim(t, [{"kunci": "1", "varian": self.kecil.id, "rak": "GA-1", "jumlah": "3"},
                            {"kunci": "2", "varian": self.kecil.id, "rak": "ga-9", "jumlah": "5"}])
        self.assertEqual(d["gagal"], [])
        b = t.opname.baris.get()
        self.assertEqual((b.hitung, b.stok_saat_hitung), (8, 7))
        self.assertEqual(sorted((r.kode, r.jumlah, r.rak_id) for r in b.per_rak.all()),
                         [("GA-1", 3, "GA-1"), ("GA-9", 5, None)])
        # Koreksi: ketik ulang menimpa, hapus mengurangi.
        self._kirim(t, [{"kunci": "3", "varian": self.kecil.id, "rak": "GA-1", "jumlah": "4"}])
        self._kirim(t, [{"kunci": "4", "varian": self.kecil.id, "rak": "GA-9", "hapus": True}])
        b.refresh_from_db()
        self.assertEqual(b.hitung, 4)
        self.assertEqual(opname.hitungan_tablet(t), [{"varian": self.kecil.id, "rak": "GA-1", "jumlah": 4}])
        self._kirim(t, [{"kunci": "5", "varian": self.kecil.id, "rak": "GA-1", "hapus": True}])
        self.assertFalse(t.opname.baris.exists())

    def test_rak_lokasi_lain_ditolak(self):
        Rak.objects.filter(kode="TK-01").update(tempat=self.toko)
        d = self._kirim(self._tugas(), [{"kunci": "1", "varian": self.kecil.id, "rak": "TK-01", "jumlah": "3"}])
        self.assertIn("lokasi lain", d["gagal"][0]["pesan"])

    def test_acc_memindah_rak_dan_membuat_rak_baru(self):
        from master.layanan import opname
        from master.layanan.lokasi import rincian
        from master.models import VarianRak
        VarianRak.objects.create(varian=self.kecil, rak_id="GA-2")          # rak tambahan lama, tidak ditemukan lagi
        VarianRak.objects.create(varian=self.kecil, rak_id="TK-01")         # di lokasi lain: tidak disentuh
        t = self._tugas()
        self._kirim(t, [{"kunci": "1", "varian": self.kecil.id, "rak": "GA-9", "jumlah": "6"},
                        {"kunci": "2", "varian": self.kecil.id, "rak": "", "jumlah": "1"}])
        op = opname.kirim_tugas(self.gudang, t, dihitung_oleh="Andi")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:opname_detail", args=[op.id]))
        self.assertContains(r, "Posisi rak ikut diperbarui saat ACC")
        self.assertContains(r, "GA-9</b> <span")
        opname.acc(self.admin, op, [b.id for b in op.baris.all()], hak=SEMUA_HAK)
        self.kecil.refresh_from_db()
        self.assertEqual(rincian(self.kecil)[self.ga.id], 7)                 # 6 + 1 = 7, tidak ada selisih
        self.assertEqual(Rak.objects.get(kode="GA-9").tempat, self.ga)       # rak baru dibuat di lokasi tugas
        self.assertEqual(self.kecil.rak_id, "GA-9")                          # GA-1 kosong -> pindah
        self.assertEqual(set(VarianRak.objects.filter(varian=self.kecil).values_list("rak_id", flat=True)), {"TK-01"})

    def test_rak_utama_tetap_kalau_masih_ada(self):
        from master.layanan import opname
        from master.models import VarianRak
        t = self._tugas()
        self._kirim(t, [{"kunci": "1", "varian": self.kecil.id, "rak": "GA-1", "jumlah": "2"},
                        {"kunci": "2", "varian": self.kecil.id, "rak": "GA-2", "jumlah": "5"}])
        op = opname.kirim_tugas(self.gudang, t, dihitung_oleh="Andi")
        opname.acc(self.admin, op, [b.id for b in op.baris.all()], hak=SEMUA_HAK)
        self.kecil.refresh_from_db()
        self.assertEqual(self.kecil.rak_id, "GA-1")
        self.assertEqual(list(VarianRak.objects.filter(varian=self.kecil).values_list("rak_id", flat=True)), ["GA-2"])

    def test_tanpa_posisi_rak_hanya_dicatat(self):
        from master.layanan import opname
        t = self._tugas(posisi=False)
        self._kirim(t, [{"kunci": "1", "varian": self.kecil.id, "rak": "GA-9", "jumlah": "7"}])
        op = opname.kirim_tugas(self.gudang, t, dihitung_oleh="Andi")
        opname.acc(self.admin, op, [b.id for b in op.baris.all()], hak=SEMUA_HAK)
        self.kecil.refresh_from_db()
        self.assertEqual(self.kecil.rak_id, "GA-1")
        self.assertFalse(Rak.objects.filter(kode="GA-9").exists())

    def test_form_tugas_menyimpan_pilihan_posisi(self):
        from master.models import Tiket
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:tiket_baru"))
        self.assertContains(r, "Perbarui posisi rak barang")
