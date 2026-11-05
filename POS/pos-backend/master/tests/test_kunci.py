"""Kunci tablet kasir dan perangkat gudang dari master (mis. saat stock opname)."""
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from pos.device_authentication import create_device_token
from pos.models import Device, DeviceStatus

from master.layanan import kunci
from master.models import PerangkatOps
from master.tests.dasar import Dasar
from master.tests.test_ops import LOKAL, _setujui_klien


def _cek_pin(pin, waktu=None, cache=None):
    from pos.worker_verification import InvalidWorkerCredentials
    if pin != "111111":
        raise InvalidWorkerCredentials("PIN kasir tidak cocok.")
    return "ID001"


@override_settings(ROOT_URLCONF="config.urls",
                   CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "kunci"}})
@patch("pos.transaction_request.verify_worker_pin", side_effect=_cek_pin)
class KunciTabletTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.device = Device.objects.create(id=uuid.uuid4(), device_code="T01")
        cls.device2 = Device.objects.create(id=uuid.uuid4(), device_code="T02")

    def setUp(self):
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Device {create_device_token(self.device)}")

    def test_tablet_dikunci_tidak_bisa_buka_barang_tapi_nota_offline_tetap_terkirim(self, _):
        self.assertEqual(self.api.get("/api/items/").status_code, 200)
        self.assertEqual(kunci.kunci_tablet(self.admin, "semua", True, "stock opname"), 2)
        r = self.api.get("/api/items/")
        self.assertEqual(r.status_code, 423)
        self.assertEqual((r.data["kode"], r.data["alasan"]), ("kasir_dikunci", "stock opname"))
        self.assertEqual(self.api.get("/api/transactions/history/").status_code, 423)
        r = self.api.get("/api/kas/")
        self.assertEqual((r.status_code, r.data["dikunci"], r.data["kunci_alasan"]), (200, True, "stock opname"))
        # Sinkron antrean offline tidak ditolak karena kunci (boleh gagal karena isi, bukan 423).
        r = self.api.post("/api/transactions/sync/", {"transactions": []}, format="json")
        self.assertNotEqual(r.status_code, 423)
        kunci.kunci_tablet(self.admin, str(self.device.id), False)
        self.assertEqual(self.api.get("/api/items/").status_code, 200)
        self.assertTrue(DeviceStatus.objects.get(device_id=self.device2.id).dikunci)


@override_settings(OPS_WAJIB_PERANGKAT=True, CACHES=LOKAL)
class KunciGudangTest(Dasar):
    def setUp(self):
        cache.clear()

    def test_gudang_dikunci_hanya_tugas_dan_hitung(self):
        _, p = _setujui_klien(self.client, self.admin)
        self.client.force_login(self.gudang)
        self.assertEqual(self.client.get("/gudang/ambil/").status_code, 200)
        kunci.kunci_ops(self.admin, "semua", True, "opname 5 Nov")
        self.assertTrue(PerangkatOps.objects.get(pk=p.pk).dikunci)
        r = self.client.get("/gudang/ambil/")
        self.assertContains(r, "opname 5 Nov", status_code=423)
        self.assertEqual(self.client.get("/gudang/").status_code, 200)
        self.assertNotEqual(self.client.get("/gudang/hitung/").status_code, 423)
        kunci.kunci_ops(self.admin, str(p.pk), False)
        self.assertEqual(self.client.get("/gudang/ambil/").status_code, 200)

    def test_tombol_di_master(self):
        _, p = _setujui_klien(self.client, self.admin)
        self.client.force_login(self.admin)
        self.client.post("/master/kasir/perangkat-ops/", {"aksi": "kunci", "id": "semua", "alasan": "opname"})
        self.assertTrue(PerangkatOps.objects.get(pk=p.pk).dikunci)
