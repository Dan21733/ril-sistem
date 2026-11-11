"""Bayar campur (E, 12 Nov 2026): satu nota dibayar tunai + transfer (+ EDC).

Kembalian selalu tunai dari laci, boleh muncul walau tunai tidak ikut (pembeli transfer lebih).
"""
import uuid
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from pos import pengaturan
from pos.device_authentication import create_device_token
from pos.models import Device, NotaBayar, NotaPiutang, Transactions

from master.layanan import laporan
from master.tests.dasar import Dasar
from master.tests.test_piutang_kasir import _cek_pin


@override_settings(ROOT_URLCONF="config.urls",
                   CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                                       "LOCATION": "bayar-campur"}})
@patch("pos.transaction_request.verify_worker_pin", side_effect=_cek_pin)
class BayarCampurTest(Dasar):
    """Palu Kecil 3 x 1.500 = 4.500."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.device = Device.objects.create(id=uuid.uuid4(), device_code="T01")

    def setUp(self):
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Device {create_device_token(self.device)}")
        self.urutan = 20

    def _nota(self, payments, *, kembali=None, cara="campur", piutang=None):
        self.urutan += 1
        bayar = sum(p["amount"] for p in payments) if payments else 4500
        harus = piutang["dibayar"] if piutang else 4500
        t = {"sync_id": str(uuid.uuid4()), "transaction_id": f"T01-{self.urutan:06d}",
             "transaction_time": timezone.now().isoformat(), "item_amount": 4500, "total_amount": 4500,
             "payment_method": cara, "payment_amount": bayar,
             "change_amount": bayar - harus if kembali is None else kembali, "cust_name": "Pak Budi",
             "items": [{"item_name": "Palu", "variant": "Kecil", "quantity": 3, "unit_price": 1500,
                        "discount": 0, "subtotal": 4500}]}
        if payments is not None:
            t["payments"] = payments
        if piutang:
            t["piutang"] = piutang
        return self.api.post("/api/transactions/", {"pin": "111111", "transaction": t}, format="json"), t

    def test_tunai_transfer_dan_kembalian(self, _):
        r, t = self._nota([{"method": "bank_transfer", "amount": 3000}, {"method": "cash", "amount": 2000}])
        self.assertEqual(r.status_code, 201, r.data)
        n = Transactions.objects.get(sync_id=t["sync_id"])
        self.assertEqual((n.payment_method, n.payment_amount, n.change_amount), ("campur", 5000, 500))
        self.assertEqual(dict(NotaBayar.objects.filter(sync_id=t["sync_id"]).values_list("metode", "jumlah")),
                         {"bank_transfer": 3000, "cash": 2000})
        nota, _b = laporan.ambil(timezone.now() - timezone.timedelta(hours=1), timezone.now() + timezone.timedelta(hours=1))
        x = next(a for a in nota if str(a["sync_id"]) == t["sync_id"])
        self.assertEqual(x["bayar"], {"bank_transfer": 3000, "cash": 1500})
        self.assertIn("Campur", x["metode"])
        # Filter metode transfer ikut menampilkan nota campur (daftar bukti transfer).
        tf, _b = laporan.ambil(timezone.now() - timezone.timedelta(hours=1), timezone.now() + timezone.timedelta(hours=1),
                               metode="bank_transfer")
        self.assertIn(t["sync_id"], [str(a["sync_id"]) for a in tf])

    def test_transfer_lebih_kembalian_tunai(self, _):
        r, t = self._nota([{"method": "bank_transfer", "amount": 5000}])
        self.assertEqual(r.status_code, 201, r.data)
        from pos import bayar
        self.assertEqual(bayar.bersih("campur", 5000, 500, [("bank_transfer", 5000)]), {"bank_transfer": 5000, "cash": -500})

    def test_kas_menghitung_bagian_tunai(self, _):
        from pos import kas
        from pos.models import KasSesi
        from master.layanan.lokasi import lokasi_toko  # noqa: F401  (toko kasir sudah ada di Dasar)
        from pos.kas import toko_kasir
        sesi = KasSesi.objects.create(toko_id=toko_kasir(), modal_awal=10000, dibuka_oleh="x",
                                      awal_hitung=timezone.now())
        self._nota([{"method": "bank_transfer", "amount": 3000}, {"method": "cash", "amount": 2000}])
        h = kas.hitung(sesi)
        self.assertEqual((h["tunai_nota"], h["transfer"]), (1500, 3000))

    def test_validasi(self, _):
        r, _t = self._nota([{"method": "cash", "amount": 1000}, {"method": "cash", "amount": 3500}])
        self.assertEqual(r.status_code, 400)
        r, _t = self._nota([{"method": "cash", "amount": 1000}])          # kurang dari total
        self.assertEqual(r.status_code, 400)
        r, _t = self._nota(None)                                            # campur tanpa rincian
        self.assertEqual(r.status_code, 400)
        r, _t = self._nota([{"method": "cash", "amount": 4500}], cara="cash")   # rincian untuk non-campur
        self.assertEqual(r.status_code, 400)
        r, _t = self._nota([{"method": "bank_transfer", "amount": 3000}, {"method": "cash", "amount": 2000}], kembali=0)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(NotaBayar.objects.exists())

    def test_dp_campur(self, _):
        r, t = self._nota([{"method": "bank_transfer", "amount": 2000}, {"method": "cash", "amount": 500}],
                          piutang={"dibayar": 2500, "status_barang": "ambil"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(NotaPiutang.objects.get(sync_id=t["sync_id"]).cara, "campur")
        from master.layanan import piutang as svc
        svc.sinkron_kasir()
        from master.models import Piutang
        self.assertEqual(Piutang.objects.get(kasir_sync_id=t["sync_id"]).pembayaran.get().cara, "campur")

    def test_aturan_metode_bayar(self, _):
        self.assertEqual(pengaturan.metode_bayar(), ["cash", "bank_transfer", "edc", "campur"])
        pengaturan.simpan(metode_bayar="cash,campur")
        r = self.api.get("/api/piutang/aturan/")
        self.assertEqual(r.data["metode_bayar"], ["cash", "campur"])
        pengaturan.simpan(metode_bayar="")
        self.assertEqual(pengaturan.metode_bayar(), ["cash"])
