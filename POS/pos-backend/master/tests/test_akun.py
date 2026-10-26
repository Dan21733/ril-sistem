"""Uji: Hak akun, login, server gudang, tema dan menu."""
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from master.tests.dasar import Dasar, LOKAL


class HakAkunTest(Dasar):
    def test_hak_dicentang_per_akun(self):
        from master.akun.akses import hak_pengguna
        self.client.force_login(self.admin)
        self.client.post(reverse("master:pengguna"), {"aksi": "tambah", "username": "budi", "password": "kata-sandi-1",
                                                      "templat": "kepala_gudang"})
        budi = get_user_model().objects.get(username="budi")
        self.assertEqual(hak_pengguna(budi), {"gudang", "gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil"})
        self.client.post(reverse("master:pengguna"), {"aksi": "hak", "id": budi.id, "hak": ["gudang", "gudang_hitung"]})
        budi = get_user_model().objects.get(username="budi")
        self.assertEqual(hak_pengguna(budi), {"gudang", "gudang_hitung"})
        self.client.force_login(budi)
        # hitung stok hanya lewat tugas dari pemilik
        self.assertRedirects(self.client.get(reverse("gudang:hitung")), reverse("gudang:tiket"))
        self.assertEqual(self.client.get(reverse("gudang:buka_dus")).status_code, 403)
        r = self.client.get(reverse("gudang:beranda"))
        self.assertContains(r, "Belum ada tugas")
        self.assertNotContains(r, "Buka dus")
        self.assertRedirects(self.client.get(reverse("master:dashboard")), reverse("gudang:beranda"),
                             fetch_redirect_response=False)

    def test_tidak_bisa_cabut_hak_sendiri(self):
        from master.models import HakAkun
        self.client.force_login(self.admin)
        self.client.post(reverse("master:pengguna"), {"aksi": "hak", "id": self.admin.id, "hak": ["master"]})
        self.assertFalse(HakAkun.objects.filter(user=self.admin).exists())  # ditolak, tetap memakai peran lama

    def test_hak_kosong_tidak_bisa_masuk(self):
        from master.models import HakAkun
        HakAkun.objects.create(user=self.finance, hak=[])
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(reverse("master:dashboard")).status_code, 403)


@override_settings(CACHES=LOKAL)
class MasukTest(Dasar):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_master_dikunci_setelah_5_salah(self):
        self.admin.set_password("benar-sekali-1")
        self.admin.save()
        url = reverse("master:login")
        for _ in range(5):
            r = self.client.post(url, {"username": self.admin.username, "password": "salah"})
            self.assertContains(r, "kata sandi salah")
        r = self.client.post(url, {"username": self.admin.username.upper(), "password": "benar-sekali-1"})
        self.assertContains(r, "Terlalu banyak percobaan")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_berhasil_menghapus_hitungan(self):
        from master.akun.masuk import gagal
        self.admin.set_password("benar-sekali-1")
        self.admin.save()
        url = reverse("master:login")
        self.client.post(url, {"username": self.admin.username, "password": "salah"})
        self.assertEqual(gagal(self.admin.username), 1)
        r = self.client.post(url, {"username": self.admin.username, "password": "benar-sekali-1"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(gagal(self.admin.username), 0)


@override_settings(CACHES=LOKAL, ROOT_URLCONF="master.urls.root_gudang", LOGIN_URL="gudang:masuk",
                   LOGIN_REDIRECT_URL="gudang:beranda")
class ServerGudangTest(Dasar):
    def setUp(self):
        from django.core.cache import cache
        from master.models import HakAkun
        cache.clear()
        User = get_user_model()
        self.kepala = User.objects.create_user("kepala", password="sandi-kepala-1")
        HakAkun.objects.create(user=self.kepala, hak=["gudang", "gudang_hitung"])
        self.tanpa = User.objects.create_user("tanpa", password="sandi-tanpa-1")
        HakAkun.objects.create(user=self.tanpa, hak=["master"])

    def test_tidak_ada_master(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/master/").status_code, 404)
        self.assertEqual(self.client.get("/master/login/").status_code, 404)
        self.assertEqual(self.client.get("/master/pengguna/").status_code, 404)
        self.assertEqual(self.client.get("/api/").status_code, 404)

    def test_belum_masuk_diarahkan_ke_masuk_gudang(self):
        r = self.client.get("/gudang/hitung/")
        self.assertRedirects(r, "/gudang/masuk/?next=/gudang/hitung/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/"), "/gudang/", fetch_redirect_response=False)

    def test_masuk_hanya_dengan_pin_lalu_keluar(self):
        self.assertEqual(self.client.post("/gudang/masuk/sandi/", {"username": "kepala", "password": "sandi-kepala-1"}).status_code, 404)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.force_login(self.kepala)
        r = self.client.get("/gudang/")
        self.assertContains(r, 'action="/gudang/keluar/"')
        self.assertNotContains(r, "/master/")
        r = self.client.post("/gudang/keluar/")
        self.assertRedirects(r, "/gudang/masuk/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)


class TemaMenuTest(Dasar):
    def test_ganti_tema_per_akun(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("master:dashboard")), 'data-tema="oranye"')
        self.assertContains(self.client.get(reverse("master:tema")), "Biru (seperti Moka)")
        r = self.client.post(reverse("master:tema"), {"tema": "moka"})
        self.assertRedirects(r, reverse("master:tema"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("master:dashboard")), 'data-tema="moka"')
        self.client.post(reverse("master:tema"), {"tema": "ungu"})
        self.assertContains(self.client.get(reverse("master:dashboard")), 'data-tema="moka"')
        # Akun lain tetap tema bawaan.
        self.client.force_login(self.finance)
        self.assertContains(self.client.get(reverse("master:dashboard")), 'data-tema="oranye"')

    def test_grup_menu_terbuka_sesuai_halaman(self):
        self.client.force_login(self.admin)
        isi = self.client.get(reverse("master:laporan")).content.decode()
        self.assertIn("<details open><summary>Laporan", isi)
        self.assertIn("<details><summary>Stok", isi)
        isi = self.client.get(reverse("master:tiket")).content.decode()
        self.assertIn("<details open><summary>Karyawan", isi)
        self.assertIn("<details><summary>Laporan", isi)
