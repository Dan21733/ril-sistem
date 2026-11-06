"""Cek bukti transfer di HP (permintaan pemilik, 6 Nov 2026)."""
import uuid

from django.utils import timezone

from pos.models import PaymentProof, Transactions

from master.models import BuktiDicek, Log
from master.tests.dasar import Dasar
from master.tests.test_hp import masuk_hp


class BuktiDicekTest(Dasar):
    def setUp(self):
        masuk_hp(self.client, self.admin)
        self.nota = Transactions.objects.create(
            sync_id=uuid.uuid4(), transaction_id="T01-777001", worker_id="ID001", transaction_time=timezone.now(),
            item_amount=150000, total_amount=150000, payment_method="bank_transfer", payment_amount=150000,
            change_amount=0)
        self.tanpa = Transactions.objects.create(
            sync_id=uuid.uuid4(), transaction_id="T01-777002", worker_id="ID001", transaction_time=timezone.now(),
            item_amount=50000, total_amount=50000, payment_method="bank_transfer", payment_amount=50000,
            change_amount=0)
        self.bukti = PaymentProof.objects.create(
            proof_id=uuid.uuid4(), sync_id=self.nota.sync_id, object_key=f"bukti/{uuid.uuid4()}.jpg",
            content_type="image/jpeg", byte_size=10, sha256="0" * 64, captured_at=timezone.now(),
            uploaded_at=timezone.now(), device_code="T01")

    def test_daftar_dan_beranda_menunjukkan_belum_dicek(self):
        isi = self.client.get("/hp/transfer/").content.decode()
        self.assertIn("T01-777001", isi)
        self.assertIn("Tandai dicek", isi)
        self.assertIn("Kasir belum mengunggah bukti", isi)
        self.assertContains(self.client.get("/hp/"), "1 bukti belum dicek")

    def test_tandai_dan_batal_tercatat(self):
        r = self.client.post("/hp/transfer/", {"proof_id": str(self.bukti.proof_id), "aksi": "cek"},
                             content_type="application/json")
        self.assertTrue(r.json()["ok"], r.json())
        self.assertTrue(BuktiDicek.objects.filter(proof_id=self.bukti.proof_id).exists())
        self.assertContains(self.client.get("/hp/"), "semua bukti dicek")
        self.assertIn("✓ Dicek", self.client.get("/hp/transfer/").content.decode())
        # tandai dua kali tidak dobel
        self.client.post("/hp/transfer/", {"proof_id": str(self.bukti.proof_id), "aksi": "cek"},
                         content_type="application/json")
        self.assertEqual(Log.objects.filter(objek="bukti_transfer").count(), 1)
        r = self.client.post("/hp/transfer/", {"proof_id": str(self.bukti.proof_id), "aksi": "batal"},
                             content_type="application/json")
        self.assertTrue(r.json()["ok"])
        self.assertFalse(BuktiDicek.objects.exists())
        self.assertEqual(Log.objects.filter(objek="bukti_transfer").count(), 2)

    def test_bukti_tidak_dikenal_ditolak(self):
        r = self.client.post("/hp/transfer/", {"proof_id": "bukan-uuid", "aksi": "cek"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/hp/transfer/", {"proof_id": str(uuid.uuid4()), "aksi": "cek"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_tanpa_hak_laporan_tidak_bisa(self):
        masuk_hp(self.client, self.gudang)
        r = self.client.get("/hp/transfer/")
        self.assertNotEqual(r.status_code, 200)
