"""Uji: aplikasi pemilik di HP (/hp/) memakai layanan dan hak yang sama dengan master."""
import json
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from pos.models import Item, NotaTinjau, Transactions

from master.akun.akses import SEMUA_HAK
from master.layanan import akun_karyawan, bukadus
from master.layanan import inti as layanan
from master.layanan import karyawan as svc_karyawan
from master.layanan import tiket as svc_tiket
from master.models import BukaDus, NotaManual, Pembelian, Supplier, TarifPPN, Tiket, TiketUsulan, VarianInfo
from master.tests.dasar import Dasar, buat_item


class HpTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        buat_item("Cat Avian", "Putih 1kg", floor=40000, jual=45000, modal=31000, kategori="Cat", brand="Avian", stok=5)
        layanan.sinkron_info()

    def setUp(self):
        self.client.force_login(self.admin)

    def post_json(self, nama, data, *args):
        return self.client.post(reverse(nama, args=args), json.dumps(data), content_type="application/json")

    def test_semua_halaman_tampil(self):
        cat = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        from master.models import ItemInfo
        for url in (reverse("hp:beranda"), reverse("hp:barang") + "?q=cat",
                    reverse("hp:barang_detail", args=[ItemInfo.objects.get(item_name="Cat Avian").id]),
                    reverse("hp:tugas"), reverse("hp:tugas_baru"), reverse("hp:input"), reverse("hp:manifest")):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.get(reverse("hp:ikon", args=[192]))
        self.assertEqual(r["Content-Type"], "image/png")
        isi = self.client.get(reverse("hp:barang") + "?q=cat").content.decode()
        self.assertIn("Cat Avian", isi)
        self.assertTrue(cat.aktif)

    def test_kembali_hanya_di_dalam_hp(self):
        from master.models import ItemInfo
        url = reverse("hp:barang_detail", args=[ItemInfo.objects.get(item_name="Cat Avian").id])
        self.assertNotContains(self.client.get(url + "?kembali=https://jahat.example/"), "jahat.example")
        self.assertContains(self.client.get(url + "?kembali=/hp/barang/%3Fq%3Dcat"), "/hp/barang/?q=cat")

    def test_finance_tidak_melihat_modal_dan_tidak_bisa_tugas(self):
        from master.models import ItemInfo
        self.client.force_login(self.finance)
        r = self.client.get(reverse("hp:barang_detail", args=[ItemInfo.objects.get(item_name="Cat Avian").id]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "31.000")
        self.assertEqual(self.client.get(reverse("hp:tugas")).status_code, 403)
        self.assertEqual(self.client.post(reverse("hp:input_nota"), {}).status_code, 403)
        self.client.force_login(self.gudang)
        self.assertNotEqual(self.client.get(reverse("hp:beranda")).status_code, 200)

    def test_stok_dari_hp(self):
        cat = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        r = self.post_json("hp:stok", {"id": cat.id, "cara": "tambah", "angka": "3", "jenis": "koreksi", "catatan": "hp"})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(Item.objects.get(item_name="Cat Avian", variant="Putih 1kg").in_stock, 8)

    def test_acc_nota_di_bawah_floor(self):
        sid = uuid.uuid4()
        NotaTinjau.objects.create(
            sync_id=sid, transaction_id="T01-000077", device_code="T01", alasan="floor: harga di bawah floor",
            payload={"sync_id": str(sid), "transaction_id": "T01-000077", "transaction_time": timezone.now().isoformat(),
                     "item_amount": 30000, "shipping_amount": 0, "total_amount": 30000, "payment_method": "cash",
                     "payment_amount": 30000, "change_amount": 0, "cust_name": "Budi", "phone_no": "", "worker_id": "ID001",
                     "items": [{"item_name": "Cat Avian", "variant": "Putih 1kg", "quantity": 1, "unit_price": 30000,
                                "discount": 0, "subtotal": 30000}]})
        r = self.client.get(reverse("hp:beranda"))
        self.assertContains(r, "di bawah floor")
        self.assertEqual(r.context["jumlah_acc"], 1)
        self.assertEqual(self.client.get(reverse("hp:barang")).context["jumlah_acc"], 1)   # lencana di semua tab
        r = self.post_json("hp:acc", {"jenis": "nota", "aksi": "terima", "id": str(sid), "worker_id": "ID001"})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertTrue(Transactions.objects.filter(sync_id=sid).exists())
        r = self.post_json("hp:acc", {"jenis": "nota", "aksi": "terima", "id": str(sid), "worker_id": "ID001"})
        self.assertFalse(r.json()["ok"])

    def test_acc_dan_tolak_buka_dus(self):
        buat_item("Semen", "50 Kg", stok=10)
        buat_item("Semen", "Per Kg", stok=20)
        layanan.sinkron_info()
        k = bukadus.buat_konversi(self.admin, dari_id=self.info("Semen", "50 Kg").id, ke_id=self.info("Semen", "Per Kg").id, isi=50)
        a = bukadus.ajukan(self.gudang, k, jumlah=1)
        b = bukadus.ajukan(self.gudang, k, jumlah=1)
        self.assertContains(self.client.get(reverse("hp:beranda")), "Buka dus")
        self.assertTrue(self.post_json("hp:acc", {"jenis": "dus", "aksi": "terima", "id": a.id}).json()["ok"])
        self.assertTrue(self.post_json("hp:acc", {"jenis": "dus", "aksi": "tolak", "id": b.id, "alasan": "salah"}).json()["ok"])
        self.assertEqual(Item.objects.get(item_name="Semen", variant="Per Kg").in_stock, 70)
        self.assertEqual(set(BukaDus.objects.values_list("status", flat=True)), {"diterapkan", "ditolak"})

    def test_tugas_harga_buat_dan_acc_dengan_nilai_diubah(self):
        wid, _ = svc_karyawan.tambah(self.admin, "Sari")
        akun_karyawan.atur_hak_ops(self.admin, wid, ["ops_barang_harga"])
        sari = get_user_model().objects.get(username=f"ops-{wid.lower()}")
        r = self.client.get(reverse("hp:tugas_baru"))
        self.assertContains(r, "Sari")
        r = self.client.post(reverse("hp:tugas_baru"), {"untuk": sari.id, "tujuan": "Harga cat", "izin": ["harga"],
                                                         "kategori": ["Cat"], "jam": "4"})
        t = Tiket.objects.get(tujuan="Harga cat")
        self.assertRedirects(r, reverse("hp:tugas_detail", args=[t.id]))
        self.assertEqual((t.jenis, t.saring.get("kategori")), ("barang", ["Cat"]))
        cat = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        u = TiketUsulan.objects.create(tiket=t, varian=cat, item_name=cat.item_name, variant=cat.variant,
                                       kolom="max_price", lama="45000", baru="47000")
        Tiket.objects.filter(pk=t.pk).update(status="dikirim", dikirim_waktu=timezone.now())
        r = self.client.get(reverse("hp:tugas_detail", args=[t.id]))
        self.assertContains(r, "ACC yang dicentang")
        self.assertContains(r, 'name="nilai_%d"' % u.id)
        r = self.client.post(reverse("hp:tugas_detail", args=[t.id]), {"aksi": "terapkan", "pilih": [u.id], f"nilai_{u.id}": "48000"})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(Item.objects.get(item_name="Cat Avian", variant="Putih 1kg").max_price, 48000)
        self.assertEqual(Tiket.objects.get(pk=t.pk).status, "selesai")

    def test_tugas_baru_tanpa_karyawan_gagal_rapi(self):
        r = self.client.post(reverse("hp:tugas_baru"), {"tujuan": "x", "izin": ["harga"], "jam": "4"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Tiket.objects.exists())
        self.assertTrue(r.context["galat"])

    def test_input_nota_tulis_tangan(self):
        cat = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        r = self.client.post(reverse("hp:input_nota"), {
            "tanggal": date.today().isoformat(), "pembeli": "Pak Budi", "cara_bayar": "cash", "ongkir": "5000",
            "baris": json.dumps([{"id": cat.id, "jumlah": 2, "harga": 45000}])})
        self.assertTrue(r.json()["ok"], r.json())
        n = NotaManual.objects.get()
        self.assertEqual((n.pembeli, n.total), ("Pak Budi", 95000))
        self.assertEqual(Item.objects.get(item_name="Cat Avian", variant="Putih 1kg").in_stock, 3)
        r = self.client.post(reverse("hp:input_nota"), {"tanggal": date.today().isoformat(), "baris": "[]"})
        self.assertEqual(r.status_code, 400)

    def test_input_pembelian_belum_dan_sudah_datang(self):
        TarifPPN.objects.create(mulai=date(2025, 1, 1), persen="11")
        sup = Supplier.objects.create(nama="PT Avian")
        cat = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        hari = date.today().isoformat()
        r = self.post_json("hp:input_beli", {"supplier": sup.id, "tanggal": hari, "baris": [{"id": cat.id, "jumlah": 4, "harga": 30000}]})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(Pembelian.objects.get().status, "dibuat")
        self.assertEqual(Item.objects.get(item_name="Cat Avian", variant="Putih 1kg").in_stock, 5)
        r = self.post_json("hp:input_beli", {"supplier": sup.id, "tanggal": hari, "sudah_datang": True, "mode_ppn": "tanpa",
                                              "baris": [{"id": cat.id, "jumlah": 4, "harga": 30000}]})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(Item.objects.get(item_name="Cat Avian", variant="Putih 1kg").in_stock, 9)
        r = self.post_json("hp:input_beli", {"supplier": sup.id, "tanggal": hari, "baris": [{"id": cat.id, "jumlah": 0, "harga": 1}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Pembelian.objects.count(), 2)            # yang gagal tidak meninggalkan PO kosong
