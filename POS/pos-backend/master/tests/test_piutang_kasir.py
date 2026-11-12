"""DP fase 2: nota DP dari tablet kasir, daftar piutang di tablet, pelunasan kasir."""
import uuid
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from pos.device_authentication import create_device_token
from pos.models import AksiPiutang, Device, Item, NotaPiutang, Transactions

from master.layanan import piutang as svc
from master.models import Pelanggan, Piutang
from master.tests.dasar import Dasar

PIN = {"111111": "ID001"}


def _cek_pin(pin, waktu=None, cache=None):
    from pos.worker_verification import InvalidWorkerCredentials
    if pin not in PIN:
        raise InvalidWorkerCredentials("PIN kasir tidak cocok.")
    return PIN[pin]


@override_settings(ROOT_URLCONF="config.urls",
                   CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                                       "LOCATION": "piutang-kasir"}})
@patch("pos.transaction_request.verify_worker_pin", side_effect=_cek_pin)
class PiutangKasirTest(Dasar):
    """Palu Kecil 3 x 1.500 = 4.500 (DP minimal 50% = 2.250)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.device = Device.objects.create(id=uuid.uuid4(), device_code="T01")
        cls.tempo = Pelanggan.objects.create(nama="CV Maju", telepon="08123", boleh_tempo=True)

    def setUp(self):
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Device {create_device_token(self.device)}")
        self.urutan = 10

    def _nota(self, piutang=None, bayar=4500, cara="cash", nama="Pak Budi", qty=3):
        self.urutan += 1
        total = 1500 * qty
        t = {"sync_id": str(uuid.uuid4()), "transaction_id": f"T01-{self.urutan:06d}",
             "transaction_time": timezone.now().isoformat(), "item_amount": total, "total_amount": total,
             "payment_method": cara, "payment_amount": bayar,
             "change_amount": bayar - (piutang["dibayar"] if piutang else total), "cust_name": nama,
             "items": [{"item_name": "Palu", "variant": "Kecil", "quantity": qty, "unit_price": 1500,
                        "discount": 0, "subtotal": total}]}
        if piutang is not None:
            t["piutang"] = piutang
        return self.api.post("/api/transactions/", {"pin": "111111", "transaction": t}, format="json"), t

    def _stok(self):
        return Item.objects.get(item_name="Palu", variant="Kecil").in_stock

    def test_nota_dp_dicatat_dan_diimpor_master(self, _):
        r, t = self._nota({"dibayar": 2500, "status_barang": "pesan"}, bayar=3000)
        self.assertEqual(r.status_code, 201, r.data)
        n = NotaPiutang.objects.get(sync_id=t["sync_id"])
        self.assertEqual((n.dibayar, n.status_barang, n.stok_ditunda), (2500, "pesan", True))
        self.assertEqual(Transactions.objects.get(sync_id=t["sync_id"]).change_amount, 500)
        self.assertEqual(self._stok(), 10)
        self.assertEqual(svc.sinkron_kasir(), 1)
        p = Piutang.objects.get(kasir_sync_id=t["sync_id"])
        self.assertEqual((p.total, p.dibayar, p.nama, p.acc_status, p.nomor, p.jenis),
                         (4500, 2500, "Pak Budi", "", "T01-000011", "pesanan"))
        self.assertEqual(p.pembayaran.get().sumber, "kasir")
        self.assertEqual(svc.sinkron_kasir(), 0)        # tidak diimpor dua kali

    def test_lunas_dibawa_tanpa_piutang_dan_validasi(self, _):
        r, t = self._nota({"dibayar": 4500, "status_barang": "bawa"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(NotaPiutang.objects.exists())
        r, _t = self._nota({"dibayar": 3000, "status_barang": "ambil"}, bayar=2000)
        self.assertEqual(r.status_code, 400)
        r, _t = self._nota({"dibayar": 3000, "status_barang": "ambil"}, bayar=3000, cara="bank_transfer")
        self.assertEqual(r.status_code, 201, r.data)
        r, _t = self._nota({"dibayar": 3000, "status_barang": "ambil"}, bayar=3000, nama="")
        self.assertEqual(r.status_code, 400)

    def test_pesan_dulu_stok_saat_diserahkan(self, _):
        r, t = self._nota({"dibayar": 3000, "status_barang": "pesan"}, bayar=3000)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self._stok(), 10)
        svc.sinkron_kasir()
        p = Piutang.objects.get(kasir_sync_id=t["sync_id"])
        svc.bayar(self.admin, p, jumlah=1500)
        svc.serahkan(self.admin, p)
        self.assertEqual(self._stok(), 7)

    def test_dp_kurang_jadi_invoice_menunggu_acc(self, _):
        r, t = self._nota({"dibayar": 0, "status_barang": "bawa", "pelanggan_id": self.tempo.id}, bayar=0)
        self.assertEqual(r.status_code, 201, r.data)
        r2, t2 = self._nota({"dibayar": 1000, "status_barang": "bawa"}, bayar=1000)
        svc.sinkron_kasir()
        p = Piutang.objects.get(kasir_sync_id=t["sync_id"])
        self.assertEqual((p.jenis_acc, p.acc_status, p.pelanggan, p.acc_catatan), ("invoice", "menunggu", self.tempo, ""))
        p2 = Piutang.objects.get(kasir_sync_id=t2["sync_id"])
        self.assertIn("bukan pelanggan tempo", p2.acc_catatan)

    def test_daftar_tablet_dan_pelunasan_kasir(self, _):
        r, t = self._nota({"dibayar": 2500, "status_barang": "pesan"}, bayar=2500)
        # Belum diimpor master: tetap terlihat di tablet.
        r = self.api.get("/api/piutang/?tab=belum_lunas", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([x["sisa"] for x in r.data["hasil"]], [2000])
        kunci = r.data["hasil"][0]["kunci"]
        self.assertEqual(self.api.get("/api/piutang/", HTTP_X_APPROVER_PIN="999999").status_code, 400)
        d = self.api.get(f"/api/piutang/{kunci}/", HTTP_X_APPROVER_PIN="111111").data
        self.assertEqual(d["barang"][0]["jumlah"], 3)
        aksi = {"pin": "111111", "aksi_id": str(uuid.uuid4()), "jenis": "bayar", "kunci": kunci, "jumlah": 2000,
                "cara": "cash", "waktu": timezone.now().isoformat()}
        self.assertEqual(self.api.post("/api/piutang/aksi/", aksi, format="json").status_code, 201)
        self.assertEqual(self.api.post("/api/piutang/aksi/", aksi, format="json").status_code, 200)   # idempoten
        self.assertEqual(AksiPiutang.objects.count(), 1)
        # Langsung tampak lunas di tablet walau master belum memproses.
        r = self.api.get("/api/piutang/?tab=belum_lunas", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual(r.data["hasil"][0]["menunggu"], [{"jenis": "bayar", "jumlah": 2000}])
        svc.sinkron_kasir()
        p = Piutang.objects.get(kasir_sync_id=t["sync_id"])
        self.assertTrue(p.lunas)
        self.assertEqual(AksiPiutang.objects.get().status, "diterapkan")
        self.assertEqual(p.pembayaran.last().oleh, "Rusdi (kasir)")
        r = self.api.get("/api/piutang/?tab=belum_lunas", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual(r.data["hasil"], [])
        r = self.api.get("/api/piutang/?tab=ambil", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual(r.data["hasil"][0]["kunci"], f"p{p.id}")
        serah = {"pin": "111111", "aksi_id": str(uuid.uuid4()), "jenis": "serahkan", "kunci": f"p{p.id}"}
        self.assertEqual(self.api.post("/api/piutang/aksi/", serah, format="json").status_code, 201)
        svc.sinkron_kasir()
        p.refresh_from_db()
        self.assertIsNotNone(p.barang_selesai)

    def test_aksi_lebih_dari_sisa_ditolak(self, _):
        r, t = self._nota({"dibayar": 2500, "status_barang": "pesan"}, bayar=2500)
        svc.sinkron_kasir()
        p = Piutang.objects.get(kasir_sync_id=t["sync_id"])
        self.api.post("/api/piutang/aksi/", {"pin": "111111", "aksi_id": str(uuid.uuid4()), "jenis": "bayar",
                                             "kunci": f"p{p.id}", "jumlah": 9000}, format="json")
        svc.sinkron_kasir()
        a = AksiPiutang.objects.get()
        self.assertEqual(a.status, "ditolak")
        self.assertIn("Lebih dari sisa", a.pesan)

    def test_aturan_dan_min_dp_di_items(self, _):
        from master.models import ItemInfo
        ItemInfo.objects.filter(item_name="Palu").update(min_dp_persen=80)
        r = self.api.get("/api/piutang/aturan/")
        self.assertEqual((r.data["dp_minimal_persen"], r.data["jatuh_tempo_hari"]), (50, 14))
        self.assertEqual([c["nama"] for c in r.data["pelanggan"]], ["CV Maju"])
        r = self.api.get("/api/items/?page_size=100")
        self.assertEqual({i["min_dp"] for i in r.data["results"] if i["item_name"] == "Palu"}, {80})
        self.assertEqual({i["min_dp"] for i in r.data["results"] if i["item_name"] == "Kikir"}, {None})

    def test_riwayat_per_akun(self, _):
        self._nota()
        r = self.api.get("/api/transactions/history/?scope=akun", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 2)     # nota uji Dasar (ID001) + nota barusan
        self.assertEqual(self.api.get("/api/transactions/history/?scope=akun").status_code, 400)

    # ---- Aturan nota diproses 12 Nov 2026 ----
    def test_melanggar_sop_wajib_acc(self, _):
        # Tablet lama: titipan dengan DP sebagian -> tetap tercatat, tapi menunggu ACC pemilik.
        r, t = self._nota({"dibayar": 2500, "status_barang": "ambil"}, bayar=2500)
        self.assertEqual(r.status_code, 201, r.data)
        svc.sinkron_kasir()
        p = Piutang.objects.get(kasir_sync_id=t["sync_id"])
        self.assertEqual((p.jenis, p.jenis_acc, p.acc_status), ("titipan", "sop", "menunggu"))
        self.assertIn("Titipan wajib lunas", p.acc_catatan)

    def test_cod_dibawa_pulang_kurangi_tagihan_dan_daftar_hitam(self, _):
        from pos.models import RefundEvent, RefundEventDetail
        from master.models import DaftarHitam
        r, t = self._nota({"dibayar": 0, "status_barang": "antar"}, bayar=0)
        self.assertEqual(r.status_code, 201, r.data)
        Transactions.objects.filter(sync_id=t["sync_id"]).update(phone_no="0812-7777-1111")
        svc.sinkron_kasir()
        p = Piutang.objects.get(kasir_sync_id=t["sync_id"])
        self.assertEqual((p.jenis, p.jenis_acc, p.sisa), ("cod", "", 4500))
        # Pembeli hanya mau 1 dari 3: 2 dibawa pulang (retur kasir), 1.500 dibayar di tempat.
        rid = uuid.uuid4()
        RefundEvent.objects.create(refund_id=rid, sync_id=t["sync_id"], transaction_id=t["transaction_id"],
                                   event_type="return", reason="COD dibawa pulang", refund_method="cash", refund_amount=0,
                                   approver_worker_id="ID001", device_code="T01", created_at=timezone.now())
        RefundEventDetail.objects.create(refund_id=rid, item_name="Palu", variant="Kecil", quantity=2,
                                         unit_price=1500, subtotal=3000)
        svc.sinkron_kasir()
        p.refresh_from_db()
        self.assertEqual((p.potongan_retur, p.sisa), (3000, 1500))
        self.assertEqual(DaftarHitam.objects.get().telepon, "081277771111")
        svc.sinkron_kasir()                                   # dihitung ulang, tidak dobel
        p.refresh_from_db()
        self.assertEqual(p.potongan_retur, 3000)
        r = self.api.get("/api/piutang/aturan/")
        self.assertEqual(r.data["daftar_hitam"], ["081277771111"])
        # Nota COD berikutnya untuk nomor itu: tercatat tapi melanggar SOP -> ACC.
        r, t2 = self._nota({"dibayar": 0, "status_barang": "antar"}, bayar=0)
        Transactions.objects.filter(sync_id=t2["sync_id"]).update(phone_no="081277771111")
        svc.sinkron_kasir()
        self.assertEqual(Piutang.objects.get(kasir_sync_id=t2["sync_id"]).jenis_acc, "sop")

    def test_tab_belum_selesai_dan_invoice_di_tablet(self, _):
        r, t = self._nota({"dibayar": 0, "status_barang": "antar"}, bayar=0)
        r, t2 = self._nota({"dibayar": 0, "status_barang": "bawa", "pelanggan_id": self.tempo.id}, bayar=0)
        # Belum diimpor master: jenis dihitung di server kasir, pembuat dari PIN.
        r = self.api.get("/api/piutang/?tab=proses", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual([(x["jenis"], x["pembuat"], x["sisa"]) for x in r.data["hasil"]], [("cod", "Rusdi", 4500)])
        svc.sinkron_kasir()
        r = self.api.get("/api/piutang/?tab=proses", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual([(x["jenis"], x["pembuat"]) for x in r.data["hasil"]], [("cod", "Rusdi")])
        r = self.api.get("/api/piutang/?tab=invoice", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual([x["jenis"] for x in r.data["hasil"]], ["invoice"])
        self.assertEqual((r.data["hitungan"]["proses"], r.data["hitungan"]["invoice"]), (1, 1))
        # Ditolak pemilik: tetap tampil sampai kasir membatalkan nota.
        p = Piutang.objects.get(kasir_sync_id=t2["sync_id"])
        svc.acc(self.admin, p, setuju=False, catatan="bukan langganan")
        r = self.api.get("/api/piutang/?tab=invoice", HTTP_X_APPROVER_PIN="111111")
        self.assertEqual([x["acc_status"] for x in r.data["hasil"]], ["ditolak"])

