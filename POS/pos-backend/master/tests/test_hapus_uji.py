"""Uji: hapus nota/karyawan uji coba (superuser saja, tercatat, stok kembali) dan laporan baru."""
import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from pos.models import Item, MutasiKasir, RefundEvent, RefundEventDetail, TransactionDetail, Transactions, Worker

from master.models import Log, MutasiStok
from master.tests.dasar import Dasar


class HapusUjiTest(Dasar):
    def setUp(self):
        self.super = get_user_model().objects.create_superuser("daniel", password="rahasia-123")
        self.client.force_login(self.super)

    def _nota(self, worker="ID001", qty=3, nomor="T01-000099"):
        sid = uuid.uuid4()
        Transactions.objects.create(sync_id=sid, transaction_id=nomor, worker_id=worker, transaction_time=timezone.now(),
                                    item_amount=1500 * qty, shipping_amount=0, total_amount=1500 * qty, payment_method="cash",
                                    payment_amount=1500 * qty, change_amount=0)
        TransactionDetail.objects.create(sync_id=sid, item_name="Palu", variant="Kecil", quantity=qty, unit_price=1500,
                                         discount=0, subtotal=1500 * qty)
        Item.objects.filter(item_name="Palu", variant="Kecil").update(in_stock=10 - qty)
        MutasiKasir.objects.create(sync_id=sid, transaction_id=nomor, worker_id=worker, item_name="Palu", variant="Kecil",
                                   jumlah=-qty, stok_sebelum=10, stok_sesudah=10 - qty)
        return sid

    def test_hanya_superuser(self):
        self.client.force_login(self.admin)              # Super Admin (peran) tapi bukan superuser
        self.assertEqual(self.client.get(reverse("master:hapus_uji")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("master:dashboard")), "Hapus data uji")
        self.client.force_login(self.super)
        self.assertContains(self.client.get(reverse("master:hapus_uji")), "T01-000001")

    def test_hapus_nota_kembalikan_stok_dan_tercatat(self):
        sid = self._nota()
        rid = uuid.uuid4()
        RefundEvent.objects.create(refund_id=rid, sync_id=sid, transaction_id="T01-000099", event_type="void", reason="uji",
                                   refund_method="cash", refund_amount=0, approver_worker_id="ID001", device_code="T01",
                                   created_at=timezone.now())
        RefundEventDetail.objects.create(refund_id=rid, item_name="Palu", variant="Kecil", quantity=1, unit_price=1500, subtotal=1500)
        r = self.client.post(reverse("master:hapus_uji"), {"aksi": "nota", "pilih": [str(sid)], "stok": "1", "alasan": ""}, follow=True)
        self.assertContains(r, "Isi alasannya")
        self.assertTrue(Transactions.objects.filter(sync_id=sid).exists())
        r = self.client.post(reverse("master:hapus_uji"), {"aksi": "nota", "pilih": [str(sid)], "stok": "1", "alasan": "uji coba"}, follow=True)
        self.assertContains(r, "1 nota dihapus")
        self.assertFalse(Transactions.objects.filter(sync_id=sid).exists())
        self.assertFalse(TransactionDetail.objects.filter(sync_id=sid).exists())
        self.assertFalse(RefundEvent.objects.filter(sync_id=sid).exists() or RefundEventDetail.objects.filter(refund_id=rid).exists())
        self.assertFalse(MutasiKasir.objects.filter(sync_id=sid).exists())
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 10)
        self.assertTrue(MutasiStok.objects.filter(jenis="koreksi", jumlah=3).exists())
        log = Log.objects.get(kolom="hapus nota uji")
        self.assertEqual((log.catatan, json.loads(log.lama)["nomor"]), ("uji coba", "T01-000099"))
        self.assertIn("Palu / Kecil x3", log.lama)

    def test_tanpa_kembalikan_stok_dan_bukan_superuser_ditolak(self):
        from master.layanan import hapus_uji
        from master.layanan.inti import Tolak
        sid = self._nota()
        with self.assertRaises(Tolak):
            hapus_uji.hapus_nota(self.admin, [str(sid)], alasan="x")
        hapus_uji.hapus_nota(self.super, [str(sid)], kembalikan_stok=False, alasan="x")
        self.assertEqual(Item.objects.get(item_name="Palu", variant="Kecil").in_stock, 7)

    def test_hapus_karyawan_setelah_notanya_dihapus(self):
        from master.layanan import karyawan as svc_karyawan
        wid, _ = svc_karyawan.tambah(self.super, "Tes")
        sid = self._nota(worker=wid)
        r = self.client.post(reverse("master:hapus_uji"), {"aksi": "karyawan", "id": wid, "alasan": "uji"}, follow=True)
        self.assertContains(r, "masih punya 1 nota")
        self.client.post(reverse("master:hapus_uji"), {"aksi": "nota", "pilih": [str(sid)], "stok": "1", "alasan": "uji"})
        r = self.client.post(reverse("master:hapus_uji"), {"aksi": "karyawan", "id": wid, "alasan": "uji"}, follow=True)
        self.assertContains(r, "Karyawan Tes dihapus")
        self.assertFalse(Worker.objects.filter(pk=wid).exists())
        self.assertTrue(Log.objects.filter(kolom="hapus karyawan uji").exists())


class LaporanBaruTest(Dasar):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_hari_teramai_dan_terlaris_per_kategori(self):
        kemarin = timezone.now() - timedelta(days=1)
        sid = uuid.uuid4()
        Transactions.objects.create(sync_id=sid, transaction_id="T01-000002", worker_id="ID001", transaction_time=kemarin,
                                    item_amount=9000, shipping_amount=0, total_amount=9000, payment_method="cash",
                                    payment_amount=9000, change_amount=0)
        TransactionDetail.objects.create(sync_id=sid, item_name="Palu", variant="Besar", quantity=3, unit_price=3000,
                                         discount=0, subtotal=9000)
        r = self.client.get(reverse("master:laporan") + "?periode=7")
        self.assertContains(r, "Hari paling ramai")
        self.assertContains(r, "teramai")
        teramai = [h for h in r.context["per_minggu"] if h["teramai"]]
        self.assertEqual(len(teramai), 1)
        self.assertEqual(teramai[0]["nama"], kemarin.astimezone(timezone.get_current_timezone()).weekday())
        r = self.client.get(reverse("master:laporan") + "?periode=7&lap=kategori")
        self.assertContains(r, "Barang terlaris di Paku")
        self.assertEqual(r.context["tabel"][0]["teratas"][0]["nama"], "Palu")
