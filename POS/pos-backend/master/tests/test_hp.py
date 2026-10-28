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
from master.models import BukaDus, Pembelian, Supplier, TarifPPN, Tiket, TiketUsulan, VarianInfo
from master.tests.dasar import Dasar, buat_item


def masuk_hp(client, user):
    """Masuk dan tandai aplikasi HP baru dibuka (tidak terkunci)."""
    import time
    client.force_login(user)
    sesi = client.session
    sesi["hp_aktif"] = int(time.time())
    sesi.save()


class HpTest(Dasar):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        buat_item("Cat Avian", "Putih 1kg", floor=40000, jual=45000, modal=31000, kategori="Cat", brand="Avian", stok=5)
        layanan.sinkron_info()

    def setUp(self):
        masuk_hp(self.client, self.admin)

    def post_json(self, nama, data, *args):
        return self.client.post(reverse(nama, args=args), json.dumps(data), content_type="application/json")

    def test_semua_halaman_tampil(self):
        cat = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        from master.models import ItemInfo
        for url in (reverse("hp:beranda"), reverse("hp:barang") + "?q=cat",
                    reverse("hp:barang_detail", args=[ItemInfo.objects.get(item_name="Cat Avian").id]),
                    reverse("hp:tugas"), reverse("hp:tugas_baru"), reverse("hp:tugas_baru") + "?jenis=hitung",
                    reverse("hp:tugas_baru") + "?jenis=lokasi", reverse("hp:input"), reverse("hp:manifest"),
                    reverse("hp:transaksi"), reverse("hp:setelan")):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotIn(b"{#", r.content, url)          # komentar templat tidak boleh bocor ke halaman
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
        masuk_hp(self.client, self.finance)
        r = self.client.get(reverse("hp:barang_detail", args=[ItemInfo.objects.get(item_name="Cat Avian").id]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "31.000")
        self.assertEqual(self.client.get(reverse("hp:tugas")).status_code, 403)
        self.assertEqual(self.client.post(reverse("hp:input_beli"), {}).status_code, 403)
        masuk_hp(self.client, self.gudang)
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

    def test_input_pembelian_pesan_dan_nota_datang_dengan_foto(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from master.models import FotoPembelian
        from master.tests.dasar import PENYIMPANAN_UJI
        TarifPPN.objects.create(mulai=date(2025, 1, 1), persen="11")
        sup = Supplier.objects.create(nama="PT Avian")
        cat = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        hari = date.today().isoformat()
        url = reverse("hp:input_beli")
        stok = lambda: Item.objects.get(item_name="Cat Avian", variant="Putih 1kg").in_stock
        r = self.client.post(url, {"mode": "pesan", "supplier": sup.id, "tanggal": hari,
                                   "baris": json.dumps([{"id": cat.id, "jumlah": 4, "harga": 30000}])})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual((Pembelian.objects.get().status, stok()), ("dibuat", 5))
        with override_settings(STORAGES=PENYIMPANAN_UJI):
            foto = SimpleUploadedFile("nota.jpg", b"\xff\xd8\xff" + b"0" * 100, content_type="image/jpeg")
            r = self.client.post(url, {"mode": "datang", "supplier": sup.id, "tanggal": hari, "mode_ppn": "tanpa",
                                       "nomor_faktur": "NT-77", "ongkir": "", "foto": [foto],
                                       "baris": json.dumps([{"id": cat.id, "jumlah": 4, "harga": 30000}])})
            self.assertTrue(r.json()["ok"], r.json())
            po = Pembelian.objects.get(nomor_faktur="NT-77")
            self.assertEqual((po.status, stok()), ("diterima", 9))
            self.assertEqual(FotoPembelian.objects.filter(pembelian=po).count(), 1)
            r = self.client.get(reverse("master:pembelian_detail", args=[po.id]))
            self.assertContains(r, reverse("master:pembelian_foto", args=[po.foto.get().id]))
            self.assertEqual(self.client.get(reverse("master:pembelian_foto", args=[po.foto.get().id])).status_code, 200)
            # berkas bukan foto ditolak dan PO tidak terbentuk
            salah = SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream")
            r = self.client.post(url, {"mode": "datang", "supplier": sup.id, "tanggal": hari, "mode_ppn": "tanpa", "foto": [salah],
                                       "baris": json.dumps([{"id": cat.id, "jumlah": 1, "harga": 30000}])})
            self.assertEqual(r.status_code, 400)
        r = self.client.post(url, {"mode": "pesan", "supplier": sup.id, "tanggal": hari, "baris": json.dumps([{"id": cat.id, "jumlah": 0, "harga": 1}])})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Pembelian.objects.count(), 2)            # yang gagal tidak meninggalkan PO kosong
        self.assertEqual(self.client.post(url, {"mode": "pesan", "supplier": sup.id, "tanggal": hari, "baris": "[]"}).status_code, 400)

    def test_tugas_hitung_dan_pindah_dari_hp(self):
        from master.models import Lokasi
        from master.layanan import lokasi as svc_lokasi
        wid, _ = svc_karyawan.tambah(self.admin, "Joko")
        akun_karyawan.atur_hak_ops(self.admin, wid, ["gudang_hitung", "ops_tiket_pindah", "ops_tiket_rak"])
        joko = get_user_model().objects.get(username=f"ops-{wid.lower()}")
        r = self.client.get(reverse("hp:tugas_baru") + "?jenis=hitung")
        self.assertContains(r, "Joko")
        toko = svc_lokasi.lokasi_toko()
        r = self.client.post(reverse("hp:tugas_baru"), {"jenis": "hitung", "untuk": joko.id, "tujuan": "Hitung cat",
                                                         "lokasi": toko.id, "kategori": ["Cat"], "jam": "2"})
        t = Tiket.objects.get(tujuan="Hitung cat")
        self.assertEqual((t.jenis, t.saring["lokasi"], t.saring["kategori"]), ("hitung", toko.id, ["Cat"]))
        self.assertContains(self.client.get(reverse("hp:tugas_detail", args=[t.id])), "Karyawan masih menghitung")
        r = self.client.post(reverse("hp:tugas_baru"), {"jenis": "lokasi", "untuk": joko.id, "tujuan": "Rapikan cat", "jam": "4"})
        t2 = Tiket.objects.get(tujuan="Rapikan cat")
        self.assertEqual(t2.jenis, "lokasi")
        gudang = Lokasi.objects.create(kode="GA", nama="Gudang A", jenis="gudang")
        cat = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        from master.models import TiketBaris
        b = TiketBaris.objects.create(tiket=t2, jenis="pindah", varian=cat, dari=toko, ke=gudang, jumlah=3)
        Tiket.objects.filter(pk=t2.pk).update(status="dikirim", dikirim_waktu=timezone.now())
        r = self.client.get(reverse("hp:tugas_detail", args=[t2.id]))
        self.assertContains(r, 'name="jumlah_%d"' % b.id)
        r = self.client.post(reverse("hp:tugas_detail", args=[t2.id]), {"aksi": "terapkan", "pilih": [b.id], f"jumlah_{b.id}": "2", f"ke_{b.id}": gudang.id})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(svc_lokasi.stok_di(cat, gudang), 2)
        self.assertEqual(Tiket.objects.get(pk=t2.pk).status, "selesai")

    def test_transaksi_hari_ini(self):
        r = self.client.get(reverse("hp:transaksi"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "T01-000001")                      # nota uji dari Dasar (hari ini)
        self.assertContains(r, "Palu")
        self.assertContains(self.client.get(reverse("hp:transaksi") + "?tanggal=2001-01-01"), "Belum ada transaksi")
        self.assertEqual(self.client.get(reverse("hp:transaksi") + "?tanggal=salah").status_code, 200)
        masuk_hp(self.client, self.finance)
        self.assertEqual(self.client.get(reverse("hp:transaksi")).status_code, 403)

    def test_periode_beranda_dan_transaksi(self):
        from datetime import timedelta
        for p, judul in (("hari", "hari ini"), ("kemarin", "kemarin"), ("7hari", "7 hari terakhir"), ("bulan", "bulan ini"), ("aneh", "hari ini")):
            r = self.client.get(reverse("hp:beranda") + "?p=" + p)
            self.assertContains(r, "Penjualan · " + judul)
            r = self.client.get(reverse("hp:transaksi") + "?p=" + p)
            self.assertContains(r, "Penjualan bersih · " + judul)
        self.assertContains(self.client.get(reverse("hp:beranda")), "3.000")          # nota uji hari ini
        self.assertContains(self.client.get(reverse("hp:beranda") + "?p=kemarin"), "Belum ada penjualan")
        r = self.client.get(reverse("hp:transaksi") + "?p=7hari")
        self.assertContains(r, "T01-000001")
        kemarin = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self.client.get(reverse("hp:transaksi") + "?tanggal=" + kemarin)
        self.assertEqual(r.context["p"], "kemarin")
        self.assertNotContains(r, "T01-000001")
        self.assertContains(self.client.get(reverse("hp:transaksi") + "?tanggal=2099-01-01"), "T01-000001")   # masa depan -> hari ini

    def test_setelan_dan_service_worker(self):
        r = self.client.get(reverse("hp:setelan"))
        self.assertContains(r, "Warna aksen")
        self.assertContains(r, "Keluar")
        sw = self.client.get(reverse("hp:sw"))
        self.assertEqual(sw["Content-Type"], "application/javascript")
        self.assertIn("showNotification", sw.content.decode())


class PushTest(Dasar):
    def setUp(self):
        masuk_hp(self.client, self.admin)

    def _langganan(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from master.layanan import push
        self.ua = ec.generate_private_key(ec.SECP256R1())
        pub = self.ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = b"0123456789abcdef"
        return {"endpoint": "https://web.push.apple.com/QWERTY", "keys": {"p256dh": push.b64u(pub), "auth": push.b64u(self.auth)}}

    def test_enkripsi_bisa_dibuka_penerima(self):
        """Dekripsi mengikuti RFC 8291 dari sisi penerima, memakai kunci privat penerima."""
        import hashlib
        import hmac
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from master.layanan import push
        sub = self._langganan()
        badan = push.enkripsi(b'{"title":"x"}', sub["keys"]["p256dh"], sub["keys"]["auth"])
        garam, idlen = badan[:16], badan[20]
        as_pub = badan[21:21 + idlen]
        h = lambda k, m: hmac.new(k, m, hashlib.sha256).digest()
        ua_pub = self.ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        bersama = self.ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        ikm = h(h(self.auth, bersama), b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
        prk = h(garam, ikm)
        isi = AESGCM(h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]).decrypt(
            h(prk, b"Content-Encoding: nonce\x00\x01")[:12], badan[21 + idlen:], None)
        self.assertEqual(isi, b'{"title":"x"}\x02')

    def test_langganan_dan_kirim_baru_sekali_saja(self):
        from unittest import mock
        from master.layanan import push
        from master.models import PushLangganan
        sid_lama = uuid.uuid4()
        NotaTinjau.objects.create(sync_id=sid_lama, transaction_id="T01-1", device_code="T01", alasan="floor", payload={"total_amount": 1})
        r = self.client.post(reverse("hp:push"), json.dumps({"aksi": "langganan", "langganan": self._langganan()}),
                             content_type="application/json")
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(PushLangganan.objects.get().user, self.admin)
        terkirim = []
        with mock.patch.object(push, "kirim_satu", side_effect=lambda l, data, **k: terkirim.append(data) or 201):
            self.assertEqual(push.kirim_baru(), 0)                 # yang menunggu sebelum langganan tidak dikirim
            sid = uuid.uuid4()
            NotaTinjau.objects.create(sync_id=sid, transaction_id="T01-2", device_code="T01", alasan="Harga di bawah floor",
                                      payload={"total_amount": 25000})
            self.assertEqual(push.kirim_baru(), 1)
            self.assertEqual(push.kirim_baru(), 0)                 # tidak dikirim dua kali
        self.assertEqual(terkirim[0]["title"], "Harga di bawah floor")
        self.assertIn("Rp25.000", terkirim[0]["body"])
        self.assertEqual(terkirim[0]["badge"], 2)
        # langganan mati (410) dihapus
        with mock.patch.object(push, "kirim_satu", return_value=410):
            r = self.client.post(reverse("hp:push"), json.dumps({"aksi": "tes", "endpoint": "https://web.push.apple.com/QWERTY"}),
                                 content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(PushLangganan.objects.exists())

    def test_finance_tidak_dapat_notifikasi_nota(self):
        from unittest import mock
        from master.layanan import push
        masuk_hp(self.client, self.finance)
        self.client.post(reverse("hp:push"), json.dumps({"aksi": "langganan", "langganan": self._langganan()}), content_type="application/json")
        NotaTinjau.objects.create(sync_id=uuid.uuid4(), transaction_id="T01-3", device_code="T01", alasan="floor", payload={})
        with mock.patch.object(push, "kirim_satu", return_value=201) as k:
            push.kirim_baru()
        k.assert_not_called()


class HpMasukTest(Dasar):
    def test_belum_masuk_diarahkan_ke_masuk_hp_yang_memuat_manifest(self):
        r = self.client.get(reverse("hp:barang") + "?q=palu")
        self.assertRedirects(r, reverse("hp:masuk") + "?next=/hp/barang/%3Fq%3Dpalu", fetch_redirect_response=False)
        r = self.client.get(reverse("hp:masuk"))
        self.assertContains(r, reverse("hp:manifest"))
        r = self.client.post(reverse("hp:masuk"), {"username": "admin", "password": "rahasia-123", "next": ""})
        self.assertRedirects(r, reverse("hp:beranda"), fetch_redirect_response=False)
        self.client.logout()
        r = self.client.post(reverse("hp:push"), "{}", content_type="application/json")
        self.assertEqual(r.status_code, 401)
