"""Uji fitur toko 17 Okt 2026: riwayat PIN, tablet nonaktif, whitelist, /api/items wajib token."""
import datetime
import uuid
from unittest.mock import MagicMock, patch

import bcrypt
from django.test import TestCase
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from . import pengaturan
from .device_authentication import catat_pendaftaran, catat_terlihat, pastikan_aktif
from .models import DeviceStatus, WorkerPin
from .transaction_request import _verify_pin
from .worker_verification import (
    InvalidWorkerCredentials,
    PinOutsideValidity,
    daftar_offline,
    verify_worker_pin,
)


def _hash(pin):
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt(4)).decode()


class _Worker:
    def __init__(self, id, name, pin):
        self.id, self.name, self.pin = id, name, pin


class _Device:
    def __init__(self):
        self.id = uuid.uuid4()
        self.device_code = "T01"


def _tanpa_worker_lama():
    """Tabel worker luar tidak ada di database uji; semua karyawan di uji ini
    sudah punya riwayat PIN, jadi worker lama dianggap kosong."""
    tambalan = patch("pos.worker_verification.Worker.objects")
    palsu = tambalan.start()
    palsu.only.return_value.iterator.return_value = []
    semua = [_Worker("ID001", "Rusdi", "x"), _Worker("ID002", "Amba", "x")]
    palsu.all.return_value.only.return_value.order_by.return_value = semua

    def kecuali(id__in=()):
        sisa = MagicMock()
        sisa.only.return_value.order_by.return_value = [w for w in semua if w.id not in set(id__in)]
        return sisa
    palsu.exclude.side_effect = kecuali
    return tambalan


class RiwayatPinTests(TestCase):
    def setUp(self):
        pengaturan.lupakan_cache()
        self.addCleanup(_tanpa_worker_lama().stop)
        self.sekarang = timezone.now()
        self.ganti = self.sekarang - datetime.timedelta(days=1)
        # Rusdi: PIN lama sampai kemarin, PIN baru sejak kemarin.
        WorkerPin.objects.create(worker_id="ID001", pin_hash=_hash("111111"), berlaku_mulai=None,
                                 berlaku_sampai=self.ganti)
        WorkerPin.objects.create(worker_id="ID001", pin_hash=_hash("222222"), berlaku_mulai=self.ganti)

    def test_pin_baru_berlaku_sekarang(self):
        self.assertEqual(verify_worker_pin("222222"), "ID001")

    def test_pin_lama_ditolak_untuk_nota_sekarang(self):
        with self.assertRaises(PinOutsideValidity):
            verify_worker_pin("111111")

    def test_nota_offline_sebelum_ganti_pin_tetap_diterima(self):
        dibuat = self.ganti - datetime.timedelta(hours=3)
        self.assertEqual(verify_worker_pin("111111", waktu=dibuat), "ID001")

    def test_pin_baru_tidak_berlaku_untuk_nota_sebelum_ganti(self):
        with self.assertRaises(PinOutsideValidity):
            verify_worker_pin("222222", waktu=self.ganti - datetime.timedelta(hours=1))

    def test_lewat_masa_tenggang_pin_lama_tidak_dikenal(self):
        pengaturan.simpan(pin_tenggang_hari="0")
        with self.assertRaises(InvalidWorkerCredentials) as ctx:
            verify_worker_pin("111111", waktu=self.ganti - datetime.timedelta(hours=1))
        self.assertNotIsInstance(ctx.exception, PinOutsideValidity)

    def test_jam_tablet_maju_tidak_membuka_pin_berikutnya(self):
        besok = self.sekarang + datetime.timedelta(days=1)
        WorkerPin.objects.filter(worker_id="ID001", berlaku_sampai__isnull=True).update(berlaku_sampai=besok)
        WorkerPin.objects.create(worker_id="ID001", pin_hash=_hash("333333"), berlaku_mulai=besok)
        with self.assertRaises(PinOutsideValidity):
            verify_worker_pin("333333", waktu=besok + datetime.timedelta(hours=1))

    def test_pin_di_luar_masa_berlaku_tidak_dihitung_menebak(self):
        with patch("pos.transaction_request.register_pin_failure") as gagal, \
                patch("pos.transaction_request.check_pin_allowed"):
            with self.assertRaises(serializers.ValidationError):
                _verify_pin("111111", "device:T01")
            gagal.assert_not_called()
            with self.assertRaises(serializers.ValidationError):
                _verify_pin("999999", "device:T01")
            gagal.assert_called_once()

    def test_cache_batch_tidak_menghitung_bcrypt_ulang(self):
        cache = {}
        verify_worker_pin("222222", cache=cache)
        with patch("pos.worker_verification.cocokkan_pin") as cocok:
            self.assertEqual(verify_worker_pin("222222", cache=cache), "ID001")
            cocok.assert_not_called()

    def test_daftar_offline_memuat_pin_lama_dan_baru_di_sekitar_pergantian(self):
        hasil = daftar_offline()
        self.assertEqual(sum(1 for nama, _ in hasil if nama == "Rusdi"), 2)
        # Amba belum punya riwayat: memakai worker.pin seperti dulu.
        self.assertIn(("Amba", "x"), hasil)

    def test_daftar_offline_tidak_memuat_pin_lama_yang_sudah_jauh(self):
        WorkerPin.objects.filter(worker_id="ID001", berlaku_sampai__isnull=False).update(
            berlaku_sampai=self.sekarang - datetime.timedelta(days=10))
        self.assertEqual(sum(1 for nama, _ in daftar_offline() if nama == "Rusdi"), 1)


class PenyetujuTidakKeTabletTests(TestCase):
    """QA 18 Okt 2026 (T1): hash PIN penyetuju tidak boleh sampai ke tablet."""

    def setUp(self):
        pengaturan.lupakan_cache()
        self.addCleanup(_tanpa_worker_lama().stop)
        WorkerPin.objects.create(worker_id="ID001", pin_hash=_hash("482915"))
        WorkerPin.objects.create(worker_id="ID002", pin_hash=_hash("730264"))

    def test_penyetuju_tidak_ada_di_daftar_offline(self):
        self.assertEqual({n for n, _ in daftar_offline()}, {"Rusdi", "Amba"})
        pengaturan.simpan(penyetuju_floor="ID002")
        pengaturan.lupakan_cache()
        self.assertEqual({n for n, _ in daftar_offline()}, {"Rusdi"})

    def test_pin_penyetuju_tetap_berlaku_di_server(self):
        pengaturan.simpan(penyetuju_floor="ID002")
        pengaturan.lupakan_cache()
        self.assertEqual(verify_worker_pin("730264"), "ID002")


class AturanNotaTests(TestCase):
    """QA 18 Okt 2026: diskon (T2) dan jam nota di masa depan (T7)."""

    def test_diskon_ditolak_walau_harga_bersih_di_atas_floor(self):
        from types import SimpleNamespace
        from .transaction_service import TransactionValidationError, _validate_item_price

        item = SimpleNamespace(min_price=95000, max_price=100000)
        _validate_item_price({"unit_price": 100000, "discount": 0}, item)
        for diskon in (1, 20000):
            with self.assertRaises(TransactionValidationError):
                _validate_item_price({"unit_price": 100000, "discount": diskon}, item)

    def _persist(self, menit, **kw):
        from .transaction_service import persist_transaction
        from .transaction_sync import TransactionSyncStatus

        data = {"sync_id": uuid.uuid4(), "transaction_id": None, "items": [],
                "transaction_time": timezone.now() + datetime.timedelta(minutes=menit)}
        with patch("pos.transaction_service.resolve_transaction_request", return_value=(TransactionSyncStatus.NEW, None)), \
                patch("pos.transaction_service._process_inventory", side_effect=RuntimeError("lanjut")) as proses:
            try:
                persist_transaction(data, "ID001", None, **kw)
            except RuntimeError:
                pass
            return proses.called

    def test_jam_nota_jauh_di_depan_ditolak(self):
        from .transaction_service import TransactionValidationError

        with self.assertRaisesRegex(TransactionValidationError, "jam tablet"):
            self._persist(180)

    def test_jam_sedikit_maju_dan_jam_lalu_diterima(self):
        self.assertTrue(self._persist(5))
        self.assertTrue(self._persist(-600))

    def test_acc_pemilik_tidak_diperiksa_ulang(self):
        self.assertTrue(self._persist(180, lewati_harga=True))


class TabletTests(TestCase):
    def setUp(self):
        pengaturan.lupakan_cache()

    def test_tablet_nonaktif_ditolak(self):
        d = _Device()
        DeviceStatus.objects.create(device_id=d.id, aktif=False)
        with self.assertRaises(AuthenticationFailed):
            pastikan_aktif(d)

    def test_tablet_tanpa_status_tetap_boleh(self):
        pastikan_aktif(_Device())

    def test_terakhir_dilihat_dicatat_dan_dibatasi(self):
        d = _Device()
        catat_terlihat(d)
        pertama = DeviceStatus.objects.get(device_id=d.id).terakhir_dilihat
        catat_terlihat(d)
        self.assertEqual(DeviceStatus.objects.get(device_id=d.id).terakhir_dilihat, pertama)

    def test_pendaftaran_mencatat_status(self):
        d = _Device()
        catat_pendaftaran(d)
        st = DeviceStatus.objects.get(device_id=d.id)
        self.assertTrue(st.aktif)
        self.assertIsNotNone(st.didaftar)

    def test_pendaftaran_ditutup_menolak_tablet_baru(self):
        pengaturan.simpan(pendaftaran_tablet="tutup")
        with patch("pos.views.validate_device_registration_token"), patch("pos.views.register_device") as daftar:
            r = APIClient().post("/api/devices/register/", {}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("ditutup", r.data["detail"])
        daftar.assert_not_called()

    def test_daftar_barang_wajib_token(self):
        self.assertEqual(APIClient().get("/api/items/").status_code, 401)

    def test_health_memberi_jam_server(self):
        r = APIClient().get("/api/health/")
        self.assertIn("server_time", r.data)

    def test_health_tanpa_token(self):
        r = APIClient().get("/api/health/")
        self.assertEqual((r.status_code, r.data["status"]), (200, "ok"))


def _nota(sync_id=None, transaction_id="T01-000007"):
    return {
        "sync_id": str(sync_id or uuid.uuid4()), "transaction_id": transaction_id,
        "transaction_time": timezone.now().isoformat(), "item_amount": 10000, "shipping_amount": 0,
        "total_amount": 10000, "payment_method": "cash", "payment_amount": 10000, "change_amount": 0,
        "cust_name": "Budi", "phone_no": "",
        "items": [{"item_name": "Paku", "variant": "N/A", "quantity": 1, "unit_price": 10000, "discount": 0, "subtotal": 10000}],
    }


class NotaDitahanTests(TestCase):
    def setUp(self):
        from .device_authentication import AuthenticatedDevice

        pengaturan.lupakan_cache()
        self.addCleanup(_tanpa_worker_lama().stop)
        WorkerPin.objects.create(worker_id="ID001", pin_hash=_hash("482915"))
        self.klien = APIClient()
        d = _Device()
        self.klien.force_authenticate(user=AuthenticatedDevice(device=d))
        for nama in ("check_pin_allowed", "register_pin_failure", "clear_pin_attempts"):
            t = patch(f"pos.transaction_request.{nama}")
            t.start()
            self.addCleanup(t.stop)

    def kirim(self, nota, pin, final_lokal):
        badan = {"pin": pin, "transaction": nota}
        if final_lokal:
            badan["final_lokal"] = True
        return self.klien.post("/api/transactions/", badan, format="json")

    def test_nota_final_dengan_pin_tak_dikenal_ditahan(self):
        from .models import NotaTinjau

        nota = _nota()
        r = self.kirim(nota, "999999", True)
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.data["status"], r.data["transaction_id"], r.data["ditahan"]), ("new", "T01-000007", True))
        tinjau = NotaTinjau.objects.get(sync_id=nota["sync_id"])
        self.assertEqual(tinjau.status, "menunggu")
        self.assertIn("PIN", tinjau.alasan)
        self.assertNotIn("pin", tinjau.payload)
        # Dikirim ulang (jawaban hilang): jawaban sama, tidak dobel.
        self.assertEqual(self.kirim(nota, "999999", True).status_code, 201)
        self.assertEqual(NotaTinjau.objects.count(), 1)

    def test_nota_belum_final_tetap_ditolak_seperti_biasa(self):
        from .models import NotaTinjau

        r = self.kirim(_nota(), "999999", False)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(NotaTinjau.objects.exists())

    def test_nota_final_di_bawah_floor_ditahan_dengan_kasirnya(self):
        from .models import NotaTinjau
        from .transaction_service import TransactionValidationError

        nota = _nota()
        with patch("pos.views.persist_transaction", side_effect=TransactionValidationError("Harga barang di bawah harga floor.")):
            r = self.kirim(nota, "482915", True)
        self.assertEqual(r.status_code, 201)
        tinjau = NotaTinjau.objects.get(sync_id=nota["sync_id"])
        self.assertEqual(tinjau.worker_id, "ID001")
        self.assertIn("floor", tinjau.alasan)

    def test_batch_nota_final_ditahan_nota_lain_tetap_jalan(self):
        from .models import NotaTinjau

        baik, buruk = _nota(transaction_id="T01-000008"), _nota(transaction_id="T01-000009")
        with patch("pos.views._sync_queued_transaction", return_value={
            "sync_id": baik["sync_id"], "transaction_id": "T01-000008", "status": "new"}):
            r = self.klien.post("/api/transactions/sync/", {"transactions": [
                {**baik, "pin": "482915", "final_lokal": True},
                {**buruk, "pin": "999999", "final_lokal": True},
            ]}, format="json")
        self.assertEqual(r.status_code, 200)
        hasil = {x["sync_id"]: x for x in r.data["results"]}
        self.assertEqual(hasil[baik["sync_id"]]["status"], "new")
        self.assertTrue(hasil[buruk["sync_id"]]["ditahan"])
        self.assertTrue(NotaTinjau.objects.filter(sync_id=buruk["sync_id"]).exists())


class PenyetujuFloorTests(NotaDitahanTests):
    """PIN penyetuju untuk harga di bawah floor (pengaturan `penyetuju_floor`)."""

    def setUp(self):
        super().setUp()
        WorkerPin.objects.create(worker_id="ID002", pin_hash=_hash("730264"))
        pengaturan.simpan(penyetuju_floor="ID002")

    def _persist_palsu(self):
        from .models import Transactions

        def palsu(data, worker_id, device_code=None, lewati_harga=False, lewati_floor=False):
            self.dipanggil = {"worker": worker_id, "lewati_floor": lewati_floor}
            nota = Transactions(sync_id=data["sync_id"], transaction_id=data.get("transaction_id"))
            from .transaction_sync import TransactionSyncStatus
            return TransactionSyncStatus.NEW, nota
        return patch("pos.views.persist_transaction", side_effect=palsu)

    def test_pin_penyetuju_melewati_floor_dan_tercatat(self):
        from .models import NotaPersetujuan

        nota = _nota()
        with self._persist_palsu():
            r = self.klien.post("/api/transactions/", {"pin": "482915", "approver_pin": "730264",
                                                       "transaction": nota}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.dipanggil, {"worker": "ID001", "lewati_floor": True})
        self.assertEqual(NotaPersetujuan.objects.get(sync_id=nota["sync_id"]).approver_worker_id, "ID002")

    def test_pin_bukan_penyetuju_ditolak(self):
        with self._persist_palsu():
            r = self.klien.post("/api/transactions/", {"pin": "482915", "approver_pin": "482915",
                                                       "transaction": _nota()}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("approver_pin", r.data)

    def test_tanpa_penyetuju_minta_acc_ditahan(self):
        from .models import NotaTinjau
        from .transaction_service import TransactionValidationError

        nota = _nota()
        with patch("pos.views.persist_transaction", side_effect=TransactionValidationError("Harga barang di bawah harga floor.")):
            r = self.klien.post("/api/transactions/", {"pin": "482915", "minta_acc": True, "transaction": nota},
                                format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["ditahan"])
        self.assertTrue(NotaTinjau.objects.filter(sync_id=nota["sync_id"]).exists())

    def test_batch_dengan_penyetuju(self):
        from .models import NotaPersetujuan

        nota = _nota()
        with patch("pos.views._sync_queued_transaction", return_value={
                "sync_id": nota["sync_id"], "transaction_id": nota["transaction_id"], "status": "new"}) as kirim:
            r = self.klien.post("/api/transactions/sync/", {"transactions": [
                {**nota, "pin": "482915", "approver_pin": "730264", "final_lokal": True}]}, format="json")
        self.assertEqual(r.data["results"][0]["status"], "new")
        self.assertTrue(kirim.call_args.kwargs["lewati_floor"])
        self.assertTrue(NotaPersetujuan.objects.filter(sync_id=nota["sync_id"]).exists())


class LewatiFloorTests(TestCase):
    def test_hanya_floor_yang_dilewati_atas_tetap_dicek(self):
        from types import SimpleNamespace
        from .transaction_service import TransactionValidationError, _validate_item_price

        item = SimpleNamespace(min_price=10000, max_price=12000)
        _validate_item_price({"unit_price": 8000}, item, lewati_floor=True)
        with self.assertRaises(TransactionValidationError):
            _validate_item_price({"unit_price": 8000}, item)
        with self.assertRaises(TransactionValidationError):
            _validate_item_price({"unit_price": 13000}, item, lewati_floor=True)


def _kunci_dari_tablet(pin):
    """Tiru tablet: kunci PIN dengan kunci publik server (RSA-OAEP SHA-256)."""
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from . import kunci_pin

    publik = serialization.load_der_public_key(base64.b64decode(kunci_pin.kunci_publik()))
    isi = publik.encrypt(pin.encode(), padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                                                    algorithm=hashes.SHA256(), label=None))
    return "rsa1:" + base64.b64encode(isi).decode()


class KunciPinTests(PenyetujuFloorTests):
    """PIN penyetuju di antrean tablet dikunci kunci publik server (QA 18 Okt 2026)."""

    def setUp(self):
        from . import kunci_pin
        kunci_pin.lupakan_cache()
        self.addCleanup(kunci_pin.lupakan_cache)
        super().setUp()

    def test_buka_kebalikan_kunci_dan_kunci_tetap(self):
        from . import kunci_pin

        sandi = _kunci_dari_tablet("730264")
        self.assertNotIn("730264", sandi)
        self.assertEqual(kunci_pin.buka(sandi), "730264")
        kunci_pin.lupakan_cache()  # proses lain membaca kunci yang sama dari database
        self.assertEqual(kunci_pin.buka(sandi), "730264")
        with self.assertRaises(ValueError):
            kunci_pin.buka("rsa1:" + "A" * 344)

    def test_nota_dengan_pin_penyetuju_terkunci(self):
        from .models import NotaPersetujuan

        nota = _nota()
        with self._persist_palsu():
            r = self.klien.post("/api/transactions/", {"pin": "482915", "approver_pin_sandi": _kunci_dari_tablet("730264"),
                                                       "transaction": nota}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(self.dipanggil["lewati_floor"])
        self.assertEqual(NotaPersetujuan.objects.get(sync_id=nota["sync_id"]).approver_worker_id, "ID002")

    def test_terkunci_rusak_seperti_pin_salah_dan_nota_final_ditahan(self):
        from .models import NotaTinjau
        from .transaction_service import TransactionValidationError

        r = self.klien.post("/api/transactions/", {"pin": "482915", "approver_pin_sandi": "rsa1:rusak",
                                                   "transaction": _nota()}, format="json")
        self.assertEqual(r.status_code, 400)
        nota = _nota()
        with patch("pos.views.persist_transaction", side_effect=TransactionValidationError("floor")):
            r = self.klien.post("/api/transactions/", {"pin": "482915", "approver_pin_sandi": "rsa1:rusak",
                                                       "final_lokal": True, "transaction": nota}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(NotaTinjau.objects.filter(sync_id=nota["sync_id"]).exists())

    def test_batch_dengan_pin_penyetuju_terkunci(self):
        nota = _nota()
        with patch("pos.views._sync_queued_transaction", return_value={
                "sync_id": nota["sync_id"], "transaction_id": nota["transaction_id"], "status": "new"}) as kirim:
            r = self.klien.post("/api/transactions/sync/", {"transactions": [
                {**nota, "pin": "482915", "approver_pin_sandi": _kunci_dari_tablet("730264"), "final_lokal": True}]},
                format="json")
        self.assertEqual(r.data["results"][0]["status"], "new")
        self.assertTrue(kirim.call_args.kwargs["lewati_floor"])
        self.assertNotIn("approver_pin_sandi", kirim.call_args.args[0])

    def test_daftar_offline_membawa_kunci_publik(self):
        r = self.klien.get("/api/workers/offline-list/")
        self.assertTrue(r.data["kunci_pin"].startswith("MII"))
