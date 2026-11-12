"""Siapkan barang (11 Nov 2026): nota kasir -> daftar siapkan, rekomendasi karyawan lapangan, rekap."""
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from pos.models import NotaPiutang, RefundEvent, TransactionDetail, Transactions

from master.akun.akses import SEMUA_HAK
from master.layanan import karyawan as svc_karyawan
from master.layanan import lokasi as svc_lokasi
from master.layanan import siapkan as svc
from master.layanan.inti import Tolak
from master.models import KaryawanLapangan, Rak, Siapkan, SiapkanTugas, VarianInfo
from master.tests.dasar import Dasar


class SiapkanTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ga = svc_lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A", pic=cls.gudang.id)
        Rak.objects.create(kode="A3", nama="Rak 3", tempat=cls.ga)
        VarianInfo.objects.filter(item_name="Palu", variant="Kecil").update(rak_id="A3")
        cls.andi, cls.budi, cls.cecep = [KaryawanLapangan.objects.create(nama=n) for n in ("Andi", "Budi", "Cecep")]
        KaryawanLapangan.objects.filter(pk__in=[cls.andi.pk, cls.budi.pk]).update(ketua=cls.gudang)
        cls.sari, cls.pin = svc_karyawan.tambah(cls.admin, "Sari")     # pelayan ber-PIN

    def setUp(self):
        cache.clear()

    def _pin(self):
        return {"pin": self.pin, "kunci_pin": "uji"}

    def _nota(self, *baris, nomor="T01-000050", worker="ID001"):
        sid = uuid.uuid4()
        Transactions.objects.create(sync_id=sid, transaction_id=nomor, worker_id=worker, transaction_time=timezone.now(),
                                    item_amount=1, shipping_amount=0, total_amount=1, payment_method="cash",
                                    payment_amount=1, change_amount=0, cust_name="Pak Joko")
        for nama, varian, qty in baris:
            TransactionDetail.objects.create(sync_id=sid, item_name=nama, variant=varian, quantity=qty, unit_price=1,
                                             discount=0, subtotal=qty)
        return sid

    def _tugas(self, sid, lokasi):
        return SiapkanTugas.objects.get(siapkan__sync_id=sid, lokasi=lokasi)

    def test_nota_masuk_otomatis_dikelompokkan_per_lokasi(self):
        sid = self._nota(("Palu", "Kecil", 2), ("Kikir", "N/A", 1))
        svc.tarik_nota()
        svc.tarik_nota()                                      # tidak dobel
        s = Siapkan.objects.get(sync_id=sid)
        self.assertEqual((s.nomor, s.kasir_nama, s.pelanggan), ("T01-000050", "Rusdi", "Pak Joko"))
        lokasi = sorted((t.lokasi.kode if t.lokasi else "-", [b.item_name for b in t.baris.all()]) for t in s.tugas.all())
        self.assertEqual(lokasi, [("-", ["Kikir"]), ("GA", ["Palu"])])
        # Nota dasar (dibuat sebelum fitur dipakai, dalam 30 menit) juga masuk; nota kemarin lusa tidak.
        Transactions.objects.filter(sync_id=self.nota.sync_id).update(transaction_time=timezone.now() - timedelta(days=3))
        Siapkan.objects.filter(sync_id=self.nota.sync_id).delete()
        svc.tarik_nota()
        self.assertFalse(Siapkan.objects.filter(sync_id=self.nota.sync_id).exists())

    def test_dp_ambil_nanti_tidak_masuk_dan_void_dibatalkan(self):
        nanti = self._nota(("Palu", "Kecil", 1), nomor="T01-000051")
        NotaPiutang.objects.create(sync_id=nanti, dibayar=1, cara="cash", status_barang="ambil")
        antar = self._nota(("Palu", "Kecil", 1), nomor="T01-000052")
        NotaPiutang.objects.create(sync_id=antar, dibayar=1, cara="cash", status_barang="antar")
        void = self._nota(("Palu", "Kecil", 1), nomor="T01-000053")
        svc.tarik_nota()
        self.assertFalse(Siapkan.objects.filter(sync_id=nanti).exists())
        self.assertEqual(Siapkan.objects.get(sync_id=antar).cara, "antar")
        RefundEvent.objects.create(sync_id=void, transaction_id="T01-000053", event_type="void", reason="salah",
                                   refund_method="cash", refund_amount=1, approver_worker_id="ID001", device_code="T01")
        svc.tarik_nota()
        self.assertEqual(Siapkan.objects.get(sync_id=void).status, "batal")

    def test_rekomendasi_tim_beban_dan_pemerataan(self):
        svc.tarik_nota()
        sids = [self._nota(("Palu", "Kecil", 1), nomor=f"T01-0001{i:02d}") for i in range(8)]
        svc.tarik_nota()
        t0 = self._tugas(sids[0], self.ga)
        rek = svc.rekomendasi(t0)
        self.assertIn(rek[0]["k"], (self.andi, self.budi))     # tim lokasi didahulukan
        self.assertEqual(rek[0]["alasan"], "tim lokasi")
        self.assertEqual(t0.pic, self.gudang)                   # kelompok = lokasi + PIC efektif rak
        # Andi mengambil -> sedang sibuk -> Budi direkomendasikan.
        svc.tugaskan(t0.pk, self.andi.pk)
        t1 = self._tugas(sids[1], self.ga)
        self.assertEqual(svc.rekomendasi(t1)[0]["k"], self.budi)
        # Lewati Budi (menolak) -> tim habis -> orang luar tim.
        with self.assertRaises(Tolak):                          # menolak memberatkan orang: wajib PIN
            svc.lewati(t1.pk, self.budi.pk, "menolak")
        svc.lewati(t1.pk, self.budi.pk, "menolak", **self._pin())
        rek = svc.rekomendasi(t1)
        self.assertEqual((rek[0]["k"], rek[0]["alasan"]), (self.cecep, "tim sedang sibuk"))
        svc.tugaskan(t1.pk, self.andi.pk)          # memilih di luar rekomendasi: tercatat
        t1.refresh_from_db()
        self.assertFalse(t1.ikut_rekomendasi)
        self.assertEqual(t1.rekomendasi, self.cecep)
        svc.serahkan(t0.pk, **self._pin())
        svc.serahkan(t1.pk, **self._pin())
        # Pemerataan: Andi & Budi masing-masing 3 tugas, Cecep 0 -> Cecep walau luar tim.
        for i, k in ((2, self.andi), (3, self.budi), (4, self.budi), (5, self.budi)):
            t = self._tugas(sids[i], self.ga)
            svc.tugaskan(t.pk, k.pk)
            svc.serahkan(t.pk, **self._pin())
        rek = svc.rekomendasi(self._tugas(sids[6], self.ga))
        self.assertEqual((rek[0]["k"], rek[0]["alasan"]), (self.cecep, "pemerataan"))
        # Tugas kemarin tidak dihitung: beban per hari.
        SiapkanTugas.objects.filter(pengambil__in=[self.andi, self.budi]).update(
            ditugaskan_waktu=timezone.now() - timedelta(days=1))
        rek = svc.rekomendasi(self._tugas(sids[7], self.ga))
        self.assertEqual(rek[0]["alasan"], "tim lokasi")

    def test_serahkan_pin_diambil_alih_dan_rekap(self):
        sid = self._nota(("Palu", "Kecil", 2), ("Kikir", "N/A", 1))
        svc.tarik_nota()
        s = Siapkan.objects.get(sync_id=sid)
        ga, lain = self._tugas(sid, self.ga), self._tugas(sid, None)
        self.assertEqual(svc.rekomendasi(lain)[0]["alasan"], "tugas paling sedikit")  # tanpa rak, kasir tanpa tim
        svc.tugaskan(ga.pk, self.andi.pk, perangkat="Tablet A")
        with self.assertRaises(Tolak):
            svc.serahkan(ga.pk, pin="123", kunci_pin="uji")
        svc.serahkan(ga.pk, **self._pin())
        ga.refresh_from_db()
        # Nota dibuat Rusdi, diserahkan Sari: tercatat diambil alih.
        self.assertEqual((ga.selesai_oleh, ga.diambil_alih, ga.perangkat, ga.ditugaskan_oleh),
                         ("Sari", True, "Tablet A", "Rusdi"))
        s.refresh_from_db()
        self.assertEqual(s.status, "menunggu")
        svc.serahkan(lain.pk, **self._pin())                 # dari belum ditugaskan = disiapkan sendiri
        s.refresh_from_db()
        self.assertEqual((s.status, s.selesai_oleh), ("selesai", "Sari"))
        with self.assertRaises(Tolak):
            svc.serahkan(ga.pk, **self._pin())
        # Nota milik Sari sendiri: bukan diambil alih.
        sendiri = self._nota(("Palu", "Kecil", 1), nomor="T01-000060", worker=self.sari)
        svc.tarik_nota()
        t = self._tugas(sendiri, self.ga)
        svc.serahkan(t.pk, **self._pin())
        t.refresh_from_db()
        self.assertFalse(t.diambil_alih)
        r = svc.statistik(timezone.localdate(), timezone.localdate())
        andi = next(x for x in r["karyawan"] if x["k"] == self.andi)
        self.assertEqual((andi["tugas"], andi["selesai"]), (1, 1))
        self.assertEqual(dict(r["pelayan"])["Sari"]["sendiri"], 2)
        self.assertEqual([g["pic"] for g in r["per_tim"]], ["gudang", ""])

    def test_rak_langsung_dan_pic_per_rak(self):
        Rak.objects.create(kode="T1", nama="Display", tempat=self.toko_lokasi(), langsung=True)
        VarianInfo.objects.filter(item_name="Palu", variant="Besar").update(rak_id="T1")
        sid = self._nota(("Palu", "Besar", 1), nomor="T01-000070")
        svc.tarik_nota()
        s = Siapkan.objects.get(sync_id=sid)
        self.assertEqual((s.status, s.tugas.count()), ("selesai", 0))  # semua di rak langsung: tidak disiapkan
        self.assertNotIn(s, svc.selesai_hari_ini())
        # Nota borongan (>= siapkan_min_baris) tetap disiapkan walau rak langsung.
        from pos import pengaturan
        pengaturan.simpan(siapkan_min_baris=2)
        sid2 = self._nota(("Palu", "Besar", 1), ("Kikir", "N/A", 1), nomor="T01-000071")
        svc.tarik_nota()
        self.assertEqual(Siapkan.objects.get(sync_id=sid2).tugas.count(), 2)
        # PIC per rak: satu lokasi dua PIC -> dua kelompok.
        Rak.objects.create(kode="A4", nama="Rak 4", tempat=self.ga, pic=self.admin)
        VarianInfo.objects.filter(item_name="Palu", variant="Besar").update(rak_id="A4")
        sid3 = self._nota(("Palu", "Kecil", 1), ("Palu", "Besar", 1), nomor="T01-000072")
        svc.tarik_nota()
        pic = sorted(t.pic.username for t in Siapkan.objects.get(sync_id=sid3).tugas.all())
        self.assertEqual(pic, ["admin", "gudang"])

    def toko_lokasi(self):
        return svc_lokasi.lokasi_toko()

    def test_halaman_gudang_tanpa_login_dan_master(self):
        sid = self._nota(("Palu", "Kecil", 2))
        url = reverse("gudang:siapkan")
        r = self.client.get(url)                             # tanpa login (gerbang perangkat dimatikan di uji)
        self.assertContains(r, "T01-000050")
        self.assertContains(r, "rak A3")
        self.assertContains(r, "Rekomendasi: <b>")
        self.assertContains(r, "Masuk tim")
        self.assertNotContains(r, "tugas hari ini")
        self.assertContains(r, "PIC gudang")
        t = self._tugas(sid, self.ga)
        r = self.client.post(url, {"aksi": "tugaskan", "tugas": t.pk, "karyawan": self.budi.pk}, HTTP_X_SIAPKAN="1")
        self.assertTrue(r.json()["ok"])
        t.refresh_from_db()
        self.assertEqual((t.pengambil, t.status, t.ditugaskan_oleh), (self.budi, "diambil", "Rusdi"))
        r = self.client.post(url, {"aksi": "serahkan_semua", "siapkan": t.siapkan_id, "pin": "000000"}, HTTP_X_SIAPKAN="1")
        self.assertFalse(r.json()["ok"])
        r = self.client.post(url, {"aksi": "serahkan_semua", "siapkan": t.siapkan_id, "pin": self.pin}, HTTP_X_SIAPKAN="1")
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(Siapkan.objects.get(sync_id=sid).status, "selesai")
        self.assertIn("html", self.client.get(url, HTTP_X_SIAPKAN="1").json())
        # Master: Tim gudang (kartu per PIC), anak buah, lokasi PIC, rekap.
        self.client.force_login(self.admin)
        url = reverse("master:lapangan")
        self.client.post(url, {"aksi": "tambah", "nama": "Dedi", "ketua": self.gudang.id})
        dedi = KaryawanLapangan.objects.get(nama="Dedi")
        self.assertEqual(dedi.ketua, self.gudang)
        self.client.post(url, {"aksi": "ubah", "id": dedi.id, "nama": "Dedi", "ketua": ""})
        dedi.refresh_from_db()
        self.assertIsNone(dedi.ketua)
        self.client.post(url, {"aksi": "pegang", "pic": self.gudang.id, "lokasi": []})
        self.ga.refresh_from_db()
        self.assertIsNone(self.ga.pic)
        self.client.post(url, {"aksi": "pegang", "pic": self.gudang.id, "lokasi": [self.ga.id]})
        self.ga.refresh_from_db()
        self.assertEqual(self.ga.pic, self.gudang)
        # Tabel (12 Nov 2026): satu lokasi satu PIC lewat dropdown; baris karyawan: tim + nyetir + sopir utama.
        self.client.post(url, {"aksi": "pic_lokasi", "lokasi": self.ga.id, "pic": ""})
        self.ga.refresh_from_db()
        self.assertIsNone(self.ga.pic)
        self.client.post(url, {"aksi": "pic_lokasi", "lokasi": self.ga.id, "pic": self.gudang.id})
        self.ga.refresh_from_db()
        self.assertEqual(self.ga.pic, self.gudang)
        self.client.post(url, {"aksi": "baris", "id": dedi.id, "ketua": self.gudang.id, "sopir_utama": "1"})
        dedi.refresh_from_db()
        self.assertEqual((dedi.ketua, dedi.bisa_nyetir, dedi.sopir_utama), (self.gudang, True, True))
        self.client.post(url, {"aksi": "baris", "id": dedi.id, "ketua": ""})
        dedi.refresh_from_db()
        self.assertEqual((dedi.ketua, dedi.bisa_nyetir, dedi.sopir_utama), (None, False, False))
        self.client.post(url, {"aksi": "nonaktif", "id": dedi.id})
        dedi.refresh_from_db()
        self.assertFalse(dedi.aktif)
        r = self.client.get(url)
        self.assertContains(r, "Lokasi dan PIC")
        self.assertContains(r, 'href="?tab=rekap">Rekap')
        self.assertContains(r, "Budi")
        self.assertContains(self.client.get(url + "?tab=rekap"), "Tim gudang")
        with self.assertRaises(Tolak):
            svc.simpan_karyawan(self.admin, nama="andi")

    # ---- Tim per tugas (12 Nov 2026) ----
    def test_tim_beberapa_orang_beban_dibagi(self):
        from master.models import SiapkanAnggota
        sid = self._nota(("Palu", "Kecil", 30))
        svc.tarik_nota()
        t = self._tugas(sid, self.ga)
        with self.assertRaisesMessage(Tolak, "minimal satu orang"):
            svc.mulai(t.pk)
        svc.tambah_anggota(t.pk, self.andi.pk, "antar")       # nota dibawa: peran selalu muat
        svc.tambah_anggota(t.pk, self.budi.pk)
        svc.tambah_anggota(t.pk, self.cecep.pk)
        self.assertNotIn(self.andi.pk, [p["k"].pk for p in svc.rekomendasi(t)])   # sudah di tim: tidak ditawarkan
        svc.hapus_anggota(t.pk, self.cecep.pk)
        t = svc.mulai(t.pk)
        self.assertEqual((t.status, t.pengambil), ("diambil", self.andi))
        self.assertEqual(list(SiapkanAnggota.objects.filter(tugas=t).values_list("peran", flat=True)), ["muat", "muat"])
        beban, _, sibuk = svc.beban_hari_ini()
        self.assertEqual((beban[self.andi.pk], beban[self.budi.pk]), (0.5, 0.5))
        self.assertEqual(sibuk, {self.andi.pk, self.budi.pk})
        with self.assertRaisesMessage(Tolak, "sudah jalan"):
            svc.tambah_anggota(t.pk, self.cecep.pk)
        svc.serahkan(t.pk, **self._pin())
        hari = timezone.localdate()
        rekap = {r["k"].nama: (r["tugas"], r["bagian"]) for r in svc.statistik(hari, hari)["karyawan"]}
        self.assertEqual((rekap["Andi"], rekap["Budi"]), ((1, 0.5), (1, 0.5)))

    def test_nota_antar_sopir_direkomendasikan_dulu(self):
        from master.models import SiapkanAnggota
        KaryawanLapangan.objects.filter(pk=self.cecep.pk).update(bisa_nyetir=True, sopir_utama=True)
        KaryawanLapangan.objects.filter(pk=self.budi.pk).update(bisa_nyetir=True)
        sid = self._nota(("Palu", "Kecil", 5))
        NotaPiutang.objects.create(sync_id=sid, dibayar=1, cara="cash", status_barang="antar")
        svc.tarik_nota()
        t = self._tugas(sid, self.ga)
        rek = svc.rekomendasi(t)
        self.assertEqual((rek[0]["k"], rek[0]["alasan"]), (self.cecep, "sopir"))   # sopir utama, walau bukan tim lokasi
        # Sopir utama tidak ada -> yang bisa nyetir berikutnya.
        svc.lewati(t.pk, self.cecep.pk, "tidak_ada")
        self.assertEqual(svc.rekomendasi(t)[0]["k"], self.budi)
        with self.assertRaisesMessage(Tolak, "belum ditandai bisa nyetir"):
            svc.tambah_anggota(t.pk, self.andi.pk, "sopir")
        svc.tambah_anggota(t.pk, self.budi.pk, "sopir")
        self.assertFalse(svc.butuh_sopir(t))
        self.assertNotEqual(svc.rekomendasi(t)[0]["alasan"], "sopir")
        svc.tambah_anggota(t.pk, self.andi.pk, "antar")
        svc.mulai(t.pk)
        self.assertEqual(dict(SiapkanAnggota.objects.filter(tugas=t).values_list("karyawan__nama", "peran")),
                         {"Budi": "sopir", "Andi": "antar"})
        hari = timezone.localdate()
        self.assertEqual({r["k"].nama: r["antar"] for r in svc.statistik(hari, hari)["karyawan"]}["Andi"], 1)

    def test_halaman_tim_tambah_dan_mulai(self):
        sid = self._nota(("Palu", "Kecil", 30))
        url = reverse("gudang:siapkan")
        self.client.get(url)
        t = self._tugas(sid, self.ga)
        for k in (self.andi, self.budi):
            r = self.client.post(url, {"aksi": "tambah", "tugas": t.pk, "karyawan": k.pk}, HTTP_X_SIAPKAN="1")
            self.assertTrue(r.json()["ok"], r.json())
        self.assertContains(self.client.get(url), "Mulai (2 orang)")
        r = self.client.post(url, {"aksi": "mulai", "tugas": t.pk}, HTTP_X_SIAPKAN="1")
        self.assertIn("2 orang", r.json()["pesan"])
        self.assertContains(self.client.get(url), "Andi, Budi")


@override_settings(OPS_WAJIB_PERANGKAT=True)
class SiapkanGerbangTest(Dasar):
    def test_perangkat_belum_disetujui_tidak_bisa_membuka(self):
        r = self.client.get(reverse("gudang:siapkan"))
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(r, "Siapkan barang", status_code=403)
