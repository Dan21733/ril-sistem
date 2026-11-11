"""Siapkan barang (11 Nov 2026): nota kasir -> daftar siapkan, rekomendasi karyawan lapangan, rekap."""
import uuid
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from pos.models import NotaPiutang, RefundEvent, TransactionDetail, Transactions

from master.akun.akses import SEMUA_HAK
from master.layanan import lokasi as svc_lokasi
from master.layanan import siapkan as svc
from master.layanan.inti import Tolak
from master.models import KaryawanLapangan, Rak, Siapkan, SiapkanTugas, VarianInfo
from master.tests.dasar import Dasar


class SiapkanTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ga = svc_lokasi.simpan_lokasi(cls.admin, hak=SEMUA_HAK, kode="GA", nama="Gudang A")
        Rak.objects.create(kode="A3", nama="Rak 3", tempat=cls.ga)
        VarianInfo.objects.filter(item_name="Palu", variant="Kecil").update(rak_id="A3")
        cls.andi, cls.budi, cls.cecep = [KaryawanLapangan.objects.create(nama=n) for n in ("Andi", "Budi", "Cecep")]
        cls.ga.tim.set([cls.andi, cls.budi])

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
        # Andi mengambil -> sedang sibuk -> Budi direkomendasikan.
        svc.tugaskan(self.admin, t0.pk, self.andi.pk)
        t1 = self._tugas(sids[1], self.ga)
        self.assertEqual(svc.rekomendasi(t1)[0]["k"], self.budi)
        # Lewati Budi (menolak) -> tim habis -> orang luar tim.
        svc.lewati(self.admin, t1.pk, self.budi.pk, "menolak")
        rek = svc.rekomendasi(t1)
        self.assertEqual((rek[0]["k"], rek[0]["alasan"]), (self.cecep, "tim sedang sibuk"))
        svc.tugaskan(self.admin, t1.pk, self.andi.pk)          # memilih di luar rekomendasi: tercatat
        t1.refresh_from_db()
        self.assertFalse(t1.ikut_rekomendasi)
        self.assertEqual(t1.rekomendasi, self.cecep)
        svc.serahkan(self.admin, t0.pk)
        svc.serahkan(self.admin, t1.pk)
        # Pemerataan: Andi & Budi masing-masing 3 tugas, Cecep 0 -> Cecep walau luar tim.
        for i, k in ((2, self.andi), (3, self.budi), (4, self.budi), (5, self.budi)):
            t = self._tugas(sids[i], self.ga)
            svc.tugaskan(self.admin, t.pk, k.pk)
            svc.serahkan(self.admin, t.pk)
        rek = svc.rekomendasi(self._tugas(sids[6], self.ga))
        self.assertEqual((rek[0]["k"], rek[0]["alasan"]), (self.cecep, "pemerataan"))
        # Tugas kemarin tidak dihitung: beban per hari.
        SiapkanTugas.objects.filter(pengambil__in=[self.andi, self.budi]).update(
            ditugaskan_waktu=timezone.now() - timedelta(days=1))
        rek = svc.rekomendasi(self._tugas(sids[7], self.ga))
        self.assertEqual(rek[0]["alasan"], "tim lokasi")

    def test_serahkan_dan_rekap(self):
        sid = self._nota(("Palu", "Kecil", 2), ("Kikir", "N/A", 1))
        svc.tarik_nota()
        s = Siapkan.objects.get(sync_id=sid)
        ga, lain = self._tugas(sid, self.ga), self._tugas(sid, None)
        svc.tugaskan(self.admin, ga.pk, self.andi.pk)
        svc.serahkan(self.admin, ga.pk)
        s.refresh_from_db()
        self.assertEqual(s.status, "menunggu")
        svc.serahkan(self.admin, lain.pk)                     # dari belum ditugaskan = disiapkan sendiri
        s.refresh_from_db()
        self.assertEqual(s.status, "selesai")
        with self.assertRaises(Tolak):
            svc.serahkan(self.admin, ga.pk)
        r = svc.statistik(timezone.localdate(), timezone.localdate())
        andi = next(x for x in r["karyawan"] if x["k"] == self.andi)
        self.assertEqual((andi["tugas"], andi["selesai"]), (1, 1))
        self.assertEqual(dict(r["pelayan"])["admin"]["sendiri"], 1)

    def test_halaman_gudang_dan_master(self):
        sid = self._nota(("Palu", "Kecil", 2))
        self.client.force_login(self.gudang)
        r = self.client.get(reverse("gudang:siapkan"))
        self.assertContains(r, "T01-000050")
        self.assertContains(r, "rak A3")
        self.assertContains(r, "Ambil: ")
        t = self._tugas(sid, self.ga)
        r = self.client.post(reverse("gudang:siapkan"), {"aksi": "tugaskan", "tugas": t.pk, "karyawan": self.budi.pk})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual((t.pengambil, t.status, t.ditugaskan_oleh), (self.budi, "diambil", "gudang"))
        self.client.post(reverse("gudang:siapkan"), {"aksi": "serahkan_semua", "siapkan": t.siapkan_id})
        self.assertEqual(Siapkan.objects.get(sync_id=sid).status, "selesai")
        # Master: karyawan, tim, rekap.
        self.client.force_login(self.admin)
        url = reverse("master:lapangan")
        self.client.post(url, {"aksi": "tambah", "nama": "Dedi"})
        dedi = KaryawanLapangan.objects.get(nama="Dedi")
        self.client.post(url, {"aksi": "tim", f"tim_{self.ga.id}": [self.andi.id, dedi.id]})
        self.assertEqual(set(self.ga.tim.all()), {self.andi, dedi})
        self.client.post(url, {"aksi": "nonaktif", "id": dedi.id})
        dedi.refresh_from_db()
        self.assertFalse(dedi.aktif)
        self.assertFalse(self.ga.tim.filter(pk=dedi.pk).exists())
        r = self.client.get(url)
        self.assertContains(r, "Budi")
        with self.assertRaises(Tolak):
            svc.simpan_karyawan(self.admin, nama="andi")
