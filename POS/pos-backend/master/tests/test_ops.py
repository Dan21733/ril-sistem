"""Aplikasi operasional: gerbang perangkat, masuk dengan PIN kasir, tiket data barang."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from pos.models import Item

from master.layanan import akun_karyawan
from master.layanan import karyawan as svc_karyawan
from master.layanan import tiket as svc_tiket
from master.layanan import tiket_barang
from master.layanan.inti import Tolak
from master.models import HakAkun, ItemInfo, Log, PerangkatOps, Tiket, TiketUsulan, VarianInfo
from master.tests.dasar import Dasar, buat_item
from master.layanan import inti as layanan

LOKAL = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "uji-ops"}}
PEMILIK = {"kelola_lokasi", "ubah_harga", "ubah_info", "lihat_modal"}
BARANG = ["ops_barang_harga", "ops_barang_alias", "ops_barang_aktif"]


def _setujui_klien(client, admin, nama="Laptop admin"):
    r = client.get("/gudang/masuk/")
    p = PerangkatOps.objects.order_by("-id").first()
    from master.layanan import perangkat_ops
    perangkat_ops.setujui(admin, p.id, nama)
    return r, p


@override_settings(OPS_WAJIB_PERANGKAT=True, CACHES=LOKAL)
class GerbangPerangkatTest(Dasar):
    def setUp(self):
        cache.clear()

    def test_perangkat_baru_hanya_lihat_kode(self):
        r = self.client.get("/gudang/masuk/")
        self.assertEqual(r.status_code, 403)
        p = PerangkatOps.objects.get()
        self.assertEqual(p.status, "menunggu")
        self.assertContains(r, p.kode, status_code=403)
        self.assertNotContains(r, 'name="pin"', status_code=403)
        self.assertIn("ops_perangkat", r.cookies)
        self.assertTrue(r.cookies["ops_perangkat"]["httponly"])
        # cookie tidak disimpan mentah di database
        self.assertNotIn(p.kunci, r.cookies["ops_perangkat"].value)
        # perangkat yang sama tidak membuat permintaan baru
        self.client.get("/gudang/")
        self.assertEqual(PerangkatOps.objects.count(), 1)

    def test_setelah_disetujui_halaman_pin_tampil(self):
        _setujui_klien(self.client, self.admin)
        r = self.client.get("/gudang/masuk/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="pin"')

    def test_cookie_palsu_tidak_dikenali(self):
        _setujui_klien(self.client, self.admin)
        self.client.cookies["ops_perangkat"] = "palsu"
        self.assertEqual(self.client.get("/gudang/masuk/").status_code, 403)

    def test_cabut_mengeluarkan_yang_sedang_masuk(self):
        from master.layanan import perangkat_ops
        _, p = _setujui_klien(self.client, self.admin)
        self.client.force_login(self.gudang)
        self.assertEqual(self.client.get("/gudang/").status_code, 200)
        perangkat_ops.cabut(self.admin, p.id)
        r = self.client.get("/gudang/")
        self.assertContains(r, "dicabut", status_code=403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_batas_permintaan_menunggu(self):
        from master.layanan.perangkat_ops import BATAS_MENUNGGU
        for i in range(BATAS_MENUNGGU):
            PerangkatOps.objects.create(kunci=f"k{i}", kode=f"OPS-X{i:03d}")
        r = self.client.get("/gudang/masuk/")
        self.assertContains(r, "Terlalu banyak", status_code=403)
        self.assertEqual(PerangkatOps.objects.count(), BATAS_MENUNGGU)

    def test_master_tidak_terkena_gerbang(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("master:dashboard")).status_code, 200)
        self.assertEqual(PerangkatOps.objects.count(), 0)

    def test_halaman_perangkat_di_master(self):
        self.client.get("/gudang/masuk/")
        p = PerangkatOps.objects.get()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:perangkat_ops"))
        self.assertContains(r, p.kode)
        self.client.post(reverse("master:perangkat_ops"), {"aksi": "setujui", "id": p.id, "nama": "HP gudang"})
        p.refresh_from_db()
        self.assertEqual((p.status, p.nama), ("disetujui", "HP gudang"))
        self.client.force_login(self.gudang)
        self.assertEqual(self.client.get(reverse("master:perangkat_ops")).status_code, 302)


@override_settings(OPS_WAJIB_PERANGKAT=True, CACHES=LOKAL)
class MasukPinTest(Dasar):
    def setUp(self):
        cache.clear()
        self.wid, self.pin = svc_karyawan.tambah(self.admin, "Sari")
        self.wid2, self.pin2 = svc_karyawan.tambah(self.admin, "Budi")
        akun_karyawan.atur_hak_ops(self.admin, self.wid, BARANG)
        _, self.p = _setujui_klien(self.client, self.admin)

    def test_masuk_dengan_pin_langsung_ke_tugas(self):
        r = self.client.post("/gudang/masuk/", {"pin": self.pin})
        self.assertRedirects(r, "/gudang/", fetch_redirect_response=False)
        r = self.client.get("/gudang/")
        self.assertContains(r, "Belum ada tugas")
        self.assertContains(r, "Sari")
        self.assertNotContains(r, "Hitung stok")
        self.p.refresh_from_db()
        self.assertEqual(self.p.terakhir_akun, "Sari")

    def test_formulir_basi_tidak_berakhir_di_403(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        _setujui_klien(c, self.admin)
        r = c.post("/gudang/masuk/", {"pin": self.pin})            # tanpa token
        self.assertRedirects(r, "/gudang/masuk/", fetch_redirect_response=False)
        self.assertContains(c.get("/gudang/masuk/"), "kedaluwarsa")
        c.force_login(get_user_model().objects.get(username=f"ops-{self.wid.lower()}"))
        r = c.post("/gudang/masuk/", {"pin": self.pin})            # tombol kembali ke halaman masuk setelah masuk
        self.assertRedirects(r, "/gudang/", fetch_redirect_response=False)
        r = c.post("/gudang/keluar/")
        self.assertRedirects(r, "/gudang/masuk/", fetch_redirect_response=False)
        r = c.post("/gudang/", "{}", content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("kedaluwarsa", r.json()["galat"])

    def test_pin_tanpa_peran_ditolak_dengan_pesan_umum(self):
        r = self.client.post("/gudang/masuk/", {"pin": self.pin2})
        self.assertContains(r, "PIN salah, atau belum diberi akses")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_dikunci_setelah_10_salah(self):
        salah = "000000" if self.pin != "000000" and self.pin2 != "000000" else "999999"
        for _ in range(10):
            self.client.post("/gudang/masuk/", {"pin": salah})
        r = self.client.post("/gudang/masuk/", {"pin": self.pin})
        self.assertContains(r, "Terlalu banyak percobaan")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_akun_ops_tidak_bisa_masuk_dengan_sandi_atau_master(self):
        user = get_user_model().objects.get(username=f"ops-{self.wid.lower()}")
        self.assertFalse(user.has_usable_password())
        self.client.post("/gudang/masuk/", {"pin": self.pin})
        r = self.client.get(reverse("master:barang"))
        self.assertNotEqual(r.status_code, 200)

    def test_nonaktif_atau_cabut_peran_langsung_keluar(self):
        self.client.post("/gudang/masuk/", {"pin": self.pin})
        self.assertEqual(self.client.get("/gudang/").status_code, 200)
        svc_karyawan.nonaktifkan(self.admin, self.wid)
        self.assertEqual(self.client.get("/gudang/").status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        # karyawan lain: perannya dicabut
        akun_karyawan.atur_hak_ops(self.admin, self.wid2, BARANG)
        self.client.post("/gudang/masuk/", {"pin": self.pin2})
        self.assertEqual(self.client.get("/gudang/").status_code, 200)
        akun_karyawan.atur_hak_ops(self.admin, self.wid2, [])
        self.assertEqual(self.client.get("/gudang/").status_code, 302)

    def test_sesi_terikat_ke_perangkat(self):
        self.client.post("/gudang/masuk/", {"pin": self.pin})
        sesi = self.client.cookies["gudang_sessionid" if "gudang_sessionid" in self.client.cookies else "master_sessionid"].value
        from django.test import Client
        lain = Client()
        _setujui_klien(lain, self.admin, "HP lain")
        lain.cookies[self.client.cookies.get("master_sessionid") and "master_sessionid" or "gudang_sessionid"] = sesi
        r = lain.get("/gudang/")
        self.assertEqual(r.status_code, 302)

    def test_hak_di_halaman_karyawan(self):
        from pos import pengaturan
        self.client.force_login(self.admin)
        self.client.post(reverse("master:karyawan"), {"aksi": "hak", "id": self.wid2,
                                                      "hak": ["gudang_ambil", "ops_tiket_rak", "penyetuju"]})
        user = get_user_model().objects.get(username=f"ops-{self.wid2.lower()}")
        self.assertEqual(set(HakAkun.objects.get(user=user).hak), {"gudang", "gudang_ambil", "ops_tiket_rak"})
        self.assertIn(self.wid2, pengaturan.bukan_kasir())          # "Jualan di kasir" tidak dicentang
        self.assertIn(self.wid2, pengaturan.penyetuju_floor())
        r = self.client.get(reverse("master:karyawan"))
        self.assertContains(r, "Penyetuju")
        self.assertContains(r, "2 hak operasional")
        self.client.post(reverse("master:karyawan"), {"aksi": "hak", "id": self.wid2, "hak": ["pos_jual"]})
        self.assertNotIn(self.wid2, pengaturan.bukan_kasir())
        self.assertNotIn(self.wid2, pengaturan.penyetuju_floor())
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_menu_hanya_sesuai_hak(self):
        akun_karyawan.atur_hak_ops(self.admin, self.wid2, ["gudang_ambil"])
        self.client.post("/gudang/masuk/", {"pin": self.pin2})
        r = self.client.get("/gudang/")
        self.assertContains(r, "Budi")
        self.assertContains(r, "Belum ada tugas")
        for teks in ("Buka dus", "Hitung stok", "Terima barang datang"):
            self.assertNotContains(r, teks)
        for url in ("/gudang/datang/", "/gudang/hitung/", "/gudang/buka-dus/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.get("/gudang/stok/").status_code, 404)        # menu Stok dihapus
        self.assertEqual(self.client.get("/gudang/masuk/sandi/").status_code, 404)  # masuk hanya dengan PIN
        self.assertEqual(self.client.get("/gudang/ambil/").status_code, 200)


class JualanKasirTest(Dasar):
    def setUp(self):
        self.wid, self.pin = svc_karyawan.tambah(self.admin, "Eva")

    def test_tanpa_hak_jualan_pin_ditolak_kasir_dan_tidak_ke_tablet(self):
        from rest_framework import serializers
        from pos.transaction_request import _verify_pin, PESAN_BUKAN_KASIR
        from pos.worker_verification import daftar_offline
        self.assertEqual(_verify_pin(self.pin, None), self.wid)
        self.assertIn("Eva", [n for n, _ in daftar_offline()])
        akun_karyawan.atur_jual(self.admin, self.wid, False)
        with self.assertRaises(serializers.ValidationError) as e:
            _verify_pin(self.pin, None)
        self.assertIn(PESAN_BUKAN_KASIR, str(e.exception.detail))
        self.assertEqual(_verify_pin(self.pin, None, untuk_kasir=False), self.wid)   # tetap bisa jadi penyetuju
        self.assertNotIn("Eva", [n for n, _ in daftar_offline()])
        self.assertTrue(Log.objects.filter(kolom="jualan di kasir").exists())
        akun_karyawan.atur_jual(self.admin, self.wid, True)
        self.assertEqual(_verify_pin(self.pin, None), self.wid)

    def test_migrasi_hak_lama(self):
        from importlib import import_module
        from django.apps import apps
        from master.models import AkunKaryawan
        lama = get_user_model().objects.create_user("lama", password="x-123-abc")
        HakAkun.objects.create(user=lama, hak=["gudang", "ops_tiket", "ops_barang"])
        akun_karyawan.atur_hak_ops(self.admin, self.wid, ["gudang_ambil"])
        AkunKaryawan.objects.filter(worker_id=self.wid).update(peran=["kepala_gudang", "admin_barang"])
        import_module("master.migrations.0027_hak_ops_rinci").maju(apps, None)
        self.assertEqual(set(HakAkun.objects.get(user=lama).hak),
                         {"gudang", "ops_tiket_pindah", "ops_tiket_rak", "ops_barang_harga", "ops_barang_alias", "ops_barang_aktif"})
        self.assertEqual(set(AkunKaryawan.objects.get(worker_id=self.wid).peran),
                         {"gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil", *BARANG})


class TiketBarangTest(Dasar):
    def setUp(self):
        self.wid, self.pin = svc_karyawan.tambah(self.admin, "Sari")
        akun_karyawan.atur_hak_ops(self.admin, self.wid, BARANG)
        self.sari = get_user_model().objects.get(username=f"ops-{self.wid.lower()}")
        buat_item("Cat Avian", "Putih 1kg", floor=40000, jual=45000, modal=987654, kategori="Cat")
        buat_item("Cat Avian", "Hitam 1kg", floor=40000, jual=45000, modal=31000, kategori="Cat")
        layanan.sinkron_info()
        self.t = svc_tiket.buka(self.admin, untuk=self.sari, tujuan="Harga cat", hak=PEMILIK, jenis="barang",
                                izin=["harga", "alias", "aktif"], kategori=["Cat"],
                                batas=timezone.now() + timedelta(hours=2))
        self.putih = VarianInfo.objects.get(item_name="Cat Avian", variant="Putih 1kg")
        self.hitam = VarianInfo.objects.get(item_name="Cat Avian", variant="Hitam 1kg")

    def _isi(self, **lebih):
        data = {"aksi": "simpan", "v": [self.putih.id, self.hitam.id], "i": ["Cat Avian"],
                f"h_{self.putih.id}": "45.000", f"f_{self.putih.id}": "40.000", f"ak_{self.putih.id}": "1",
                f"h_{self.hitam.id}": "45000", f"f_{self.hitam.id}": "40000", f"ak_{self.hitam.id}": "1",
                "al_Cat Avian": ""}
        data.update(lebih)
        return self.client.post(reverse("gudang:tiket_barang", args=[self.t.id]), data)

    def test_saringan_brand_status_dan_lingkup_ringkas(self):
        buat_item("Cat Nippon", "Putih 1kg", floor=40000, jual=46000, kategori="Cat", brand="Nippon")
        Item.objects.filter(item_name="Cat Avian").update(brand="Avian")
        layanan.sinkron_info()
        VarianInfo.objects.filter(pk=self.hitam.pk).update(aktif=False)
        self.client.force_login(self.sari)
        url = reverse("gudang:tiket_barang", args=[self.t.id])
        r = self.client.get(url)
        self.assertContains(r, "Cat Nippon")
        self.assertContains(r, "Semua brand")
        r = self.client.get(url + "?brand=Nippon")
        self.assertContains(r, "Cat Nippon")
        self.assertNotContains(r, "Cat Avian")
        self.assertContains(r, "<option selected>Nippon</option>")   # pilihan saringan tetap terpilih
        r = self.client.get(url + "?status=nonaktif")
        self.assertContains(r, "Hitam 1kg")
        self.assertNotContains(r, "Putih 1kg")
        self.assertContains(r, "Centang semua Aktif")
        from master.layanan import tiket_barang
        t = Tiket.objects.get(pk=self.t.pk)
        t.saring = {"kategori": ["A", "B", "C", "D", "E"], "brand": []}
        self.assertEqual(tiket_barang.lingkup_teks(t), "kategori A, B, C +2 lagi")

    def test_izin_tiket_dibatasi_hak_admin(self):
        akun_karyawan.atur_hak_ops(self.admin, self.wid, ["ops_barang_alias"])
        self.sari = get_user_model().objects.get(pk=self.sari.pk)   # hak di-cache per objek user
        with self.assertRaises(Tolak):
            svc_tiket.buka(self.admin, untuk=self.sari, tujuan="x", hak=PEMILIK, jenis="barang", izin=["harga"],
                           batas=timezone.now() + timedelta(hours=1))
        # tiket lama berizin harga+alias+aktif: kini hanya alias yang bisa diisi
        self.client.force_login(self.sari)
        r = self.client.get(reverse("gudang:tiket_barang", args=[self.t.id]))
        self.assertContains(r, 'name="al_')
        self.assertNotContains(r, 'name="h_')
        self.assertNotContains(r, 'name="ak_')
        self._isi(**{f"h_{self.putih.id}": "99000", "al_Cat Avian": "avian"})
        self.assertEqual({u.kolom for u in self.t.usulan.all()}, {"alias"})
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:tiket_baru")), 'data-izin="alias"')

    def test_tiket_lokasi_per_hak(self):
        from master.layanan import lokasi
        akun_karyawan.atur_hak_ops(self.admin, self.wid, ["ops_tiket_rak"])
        self.sari = get_user_model().objects.get(pk=self.sari.pk)
        t = svc_tiket.buka(self.admin, untuk=self.sari, tujuan="Rak", hak=PEMILIK, batas=timezone.now() + timedelta(hours=1))
        ga = lokasi.lokasi_toko()
        with self.assertRaises(Tolak):
            svc_tiket.tambah_pindah(self.sari, t, varian_id=self.putih.id, dari=ga.id, ke=ga.id, jumlah=1)
        svc_tiket.tambah_rak(self.sari, t, varian_id=self.putih.id, rak="TK-01")
        self.client.force_login(self.sari)
        r = self.client.get(reverse("gudang:tiket_isi", args=[t.id]) + "?q=avian")
        self.assertContains(r, "Usulkan ganti rak")
        self.assertNotContains(r, "Usulkan pindah")
        self.assertNotContains(r, "Pindah sekaligus")

    def test_buka_hanya_untuk_admin_barang(self):
        with self.assertRaises(Tolak):
            svc_tiket.buka(self.admin, untuk=self.gudang, tujuan="x", hak=PEMILIK, jenis="barang", izin=["harga"],
                           batas=timezone.now() + timedelta(hours=1))
        with self.assertRaises(Tolak):
            svc_tiket.buka(self.admin, untuk=self.sari, tujuan="x", hak=PEMILIK, jenis="barang", izin=[],
                           batas=timezone.now() + timedelta(hours=1))
        with self.assertRaises(Tolak):  # admin barang tidak bisa diberi tiket pindah lokasi
            svc_tiket.buka(self.admin, untuk=self.sari, tujuan="x", hak=PEMILIK, batas=timezone.now() + timedelta(hours=1))

    def test_riwayat_tugas_di_tab_sendiri(self):
        lama = svc_tiket.buka(self.admin, untuk=self.sari, tujuan="Alias lama", hak=PEMILIK, jenis="barang",
                              izin=["alias"], batas=timezone.now() + timedelta(hours=1))
        Tiket.objects.filter(pk=lama.pk).update(status="selesai")
        self.client.force_login(self.sari)
        r = self.client.get(reverse("gudang:tiket"))
        self.assertContains(r, "Harga cat")
        self.assertNotContains(r, "Alias lama")
        self.assertContains(r, "Riwayat")
        self.assertContains(r, "Lihat tugas sebelumnya")
        r = self.client.get(reverse("gudang:riwayat"))
        self.assertContains(r, "Alias lama")
        self.assertNotContains(r, "Harga cat")

    def test_admin_tidak_melihat_modal_stok_atau_barang_di_luar_lingkup(self):
        self.client.force_login(self.sari)
        r = self.client.get(reverse("gudang:tiket"))
        self.assertContains(r, "Harga cat")
        self.assertContains(r, "sisa")
        r = self.client.get(reverse("gudang:tiket_barang", args=[self.t.id]))
        self.assertContains(r, "Putih 1kg")
        self.assertContains(r, "45.000")
        self.assertNotContains(r, "987")       # modal
        self.assertNotContains(r, "Modal")
        self.assertNotContains(r, "Stok")
        self.assertNotContains(r, "Palu")      # kategori lain
        # menu kepala gudang tidak ada
        self.assertEqual(self.client.get(reverse("gudang:hitung")).status_code, 403)

    def test_simpan_usulan_tidak_mengubah_barang(self):
        self.client.force_login(self.sari)
        self._isi(**{f"h_{self.putih.id}": "50.000", f"f_{self.putih.id}": "30000", f"ak_{self.hitam.id}": "",
                     "al_Cat Avian": "avian, Cat Kayu"})
        u = {(x.variant, x.kolom): (x.lama, x.baru) for x in self.t.usulan.all()}
        self.assertEqual(u, {("Putih 1kg", "max_price"): ("45000", "50000"), ("Putih 1kg", "min_price"): ("40000", "30000"),
                             ("Hitam 1kg", "aktif"): ("ya", "tidak"), ("*", "alias"): ("", "avian, cat kayu")})
        self.assertEqual(Item.objects.get(item_name="Cat Avian", variant="Putih 1kg").max_price, 45000)
        # kembali ke nilai sekarang = usulannya hilang
        self._isi(**{f"h_{self.putih.id}": "50.000", f"f_{self.putih.id}": "40000"})
        self.assertFalse(self.t.usulan.filter(kolom="min_price").exists())
        self.assertFalse(self.t.usulan.filter(kolom="alias").exists())

    def test_floor_di_atas_harga_dan_barang_luar_lingkup_ditolak(self):
        self.client.force_login(self.sari)
        palu = VarianInfo.objects.get(item_name="Palu", variant="Kecil")
        r = self._isi(**{f"f_{self.putih.id}": "60000", "v": [self.putih.id, self.hitam.id, palu.id],
                         f"h_{palu.id}": "1", f"f_{palu.id}": "1"})
        self.assertFalse(self.t.usulan.filter(item_name="Palu").exists())
        self.assertFalse(self.t.usulan.filter(variant="Putih 1kg").exists())
        pesan = [str(m) for m in r.wsgi_request._messages]
        self.assertTrue(any("floor" in m for m in pesan), pesan)

    def test_lewat_batas_atau_akun_lain_tidak_bisa_isi(self):
        Tiket.objects.filter(pk=self.t.pk).update(batas=timezone.now() - timedelta(minutes=1))
        self.client.force_login(self.sari)
        self._isi(**{f"h_{self.putih.id}": "50000"})
        self.assertEqual(self.t.usulan.count(), 0)
        self.client.force_login(self.gudang)
        self.assertEqual(self.client.get(reverse("gudang:tiket_barang", args=[self.t.id])).status_code, 403)

    def test_acc_per_baris_dengan_tanda(self):
        self.client.force_login(self.sari)
        self._isi(**{f"h_{self.putih.id}": "90.000", f"f_{self.putih.id}": "40000",
                     f"h_{self.hitam.id}": "45000", f"f_{self.hitam.id}": "30000", "al_Cat Avian": "avian"})
        self.client.post(reverse("gudang:tiket_barang", args=[self.t.id]), {"aksi": "kirim", "catatan": "brosur baru"})
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, "dikirim")
        # harga jual hitam berubah di master setelah diusulkan
        Item.objects.filter(item_name="Cat Avian", variant="Hitam 1kg").update(max_price=29000)
        baris = {(b["u"].variant, b["u"].kolom): {k for k, _ in b["tanda"]}
                 for b in tiket_barang.rincian(self.t, lihat_modal=True)}
        self.assertIn("besar", baris[("Putih 1kg", "max_price")])          # 45rb -> 90rb
        self.assertIn("rugi", baris[("Hitam 1kg", "min_price")])           # 30rb < modal 31rb
        self.assertIn("terbalik", baris[("Hitam 1kg", "min_price")])       # floor 30rb > jual 29rb
        tanpa_modal = {(b["u"].variant, b["u"].kolom): {k for k, _ in b["tanda"]}
                       for b in tiket_barang.rincian(self.t, lihat_modal=False)}
        self.assertNotIn("rugi", tanpa_modal[("Hitam 1kg", "min_price")])

        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:tiket_detail", args=[self.t.id]))
        self.assertContains(r, "Balik pilihan")
        self.assertContains(r, "Di bawah modal")
        self.assertContains(r, "brosur baru")
        usul = {(u.variant, u.kolom): u.id for u in self.t.usulan.all()}
        pilih = [usul[("Putih 1kg", "max_price")], usul[("*", "alias")]]
        self.client.post(reverse("master:tiket_detail", args=[self.t.id]),
                         {"aksi": "terapkan", "pilih": pilih, f"nilai_{usul[('Putih 1kg', 'max_price')]}": "88.000"})
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, "selesai")
        self.assertEqual(Item.objects.get(item_name="Cat Avian", variant="Putih 1kg").max_price, 88000)
        self.assertEqual(Item.objects.get(item_name="Cat Avian", variant="Hitam 1kg").min_price, 40000)
        self.assertEqual(ItemInfo.objects.get(item_name="Cat Avian").alias, "avian")
        self.assertEqual(TiketUsulan.objects.get(pk=usul[("Hitam 1kg", "min_price")]).hasil, "Tidak di-ACC")
        log = Log.objects.filter(item_name="Cat Avian", kolom="max_price").latest("id")
        self.assertEqual((log.nama_pengguna, log.baru), ("admin", "88000"))
        self.assertIn(f"Tiket #{self.t.id}", log.catatan)

    def test_halaman_master_tanpa_hak_modal_tidak_tampil_modal(self):
        self.client.force_login(self.sari)
        self._isi(**{f"f_{self.hitam.id}": "30000"})
        self.client.post(reverse("gudang:tiket_barang", args=[self.t.id]), {"aksi": "kirim"})
        kepala = get_user_model().objects.create_user("wakil", password="x-123-abc")
        HakAkun.objects.create(user=kepala, hak=["master", "kelola_lokasi", "ubah_harga"])
        self.client.force_login(kepala)
        r = self.client.get(reverse("master:tiket_detail", args=[self.t.id]))
        self.assertContains(r, "Hitam 1kg")
        self.assertNotContains(r, "31.000")
        self.assertNotContains(r, "Di bawah modal")

    def test_buka_tiket_barang_dari_master(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("master:tiket_baru"))
        self.assertContains(r, "Data barang")
        self.assertContains(r, 'data-jenis="barang"')
        r = self.client.post(reverse("master:tiket"), {
            "jenis": "barang", "untuk": self.sari.id, "tujuan": "Alias paku", "izin": ["alias"], "kategori": ["Paku"],
            "batas": (timezone.localtime() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")})
        t = Tiket.objects.get(tujuan="Alias paku")
        self.assertRedirects(r, reverse("master:tiket"), fetch_redirect_response=False)
        r = self.client.get(reverse("master:tiket"))
        self.assertContains(r, "Alias paku")
        self.assertContains(r, "Sedang dikerjakan")
        self.assertEqual((t.jenis, t.izin, t.saring["kategori"]), ("barang", ["alias"], ["Paku"]))
        self.client.force_login(self.sari)
        r = self.client.get(reverse("gudang:tiket_barang", args=[t.id]))
        self.assertContains(r, "Palu")
        self.assertNotContains(r, 'name="h_')     # tanpa izin harga, tidak ada isian harga
        self.assertNotContains(r, "Cat Avian")


class HakBentrokTest(Dasar):
    """Yang menghitung stok (hitung buta) tidak boleh bisa melihat jumlah stok sistem."""

    def test_hitung_dan_lihat_stok_ditolak_tanpa_mengubah_apa_pun(self):
        from pos import pengaturan
        wid, _ = svc_karyawan.tambah(self.admin, "Eva")
        akun_karyawan.atur_semua(self.admin, wid, ["pos_jual", "gudang_hitung"])
        with self.assertRaises(Tolak):
            akun_karyawan.atur_semua(self.admin, wid, ["gudang_hitung", "ops_lihat_stok"])
        user = get_user_model().objects.get(username=f"ops-{wid.lower()}")
        self.assertEqual(set(HakAkun.objects.get(user=user).hak), {"gudang", "gudang_hitung"})
        self.assertNotIn(wid, pengaturan.bukan_kasir())   # jualan kasir juga tidak ikut berubah
        self.client.force_login(self.admin)
        r = self.client.post(reverse("master:karyawan"), {"aksi": "hak", "id": wid, "hak": ["gudang_hitung", "ops_lihat_stok"]},
                             follow=True)
        self.assertContains(r, "tidak boleh untuk orang yang sama")
        akun_karyawan.atur_semua(self.admin, wid, ["ops_lihat_stok"])
        self.assertEqual(set(HakAkun.objects.get(user=user).hak), {"gudang", "ops_lihat_stok"})

    def test_migrasi_mencabut_lihat_stok_dari_penghitung(self):
        from importlib import import_module
        from django.apps import apps
        from master.models import AkunKaryawan
        mig = import_module("master.migrations.0028_gudang_tugas_terima_buta")
        wid, _ = svc_karyawan.tambah(self.admin, "Eva")
        akun_karyawan.atur_hak_ops(self.admin, wid, ["gudang_hitung"])
        akun = AkunKaryawan.objects.get(worker_id=wid)
        AkunKaryawan.objects.filter(pk=akun.pk).update(peran=["gudang_hitung", "ops_lihat_stok"])
        HakAkun.objects.filter(user=akun.user).update(hak=["gudang", "gudang_hitung", "ops_lihat_stok"])
        lain = get_user_model().objects.create_user("lihat")
        HakAkun.objects.create(user=lain, hak=["gudang", "ops_lihat_stok"])
        mig.pisah_hitung_dan_lihat_stok(apps, None)
        self.assertEqual(AkunKaryawan.objects.get(pk=akun.pk).peran, ["gudang_hitung"])
        self.assertEqual(HakAkun.objects.get(user=akun.user).hak, ["gudang", "gudang_hitung"])
        self.assertEqual(HakAkun.objects.get(user=lain).hak, ["gudang", "ops_lihat_stok"])
