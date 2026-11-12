"""HP: tab Menu dan mode tampilan simpel/lanjutan per akun (B, 12 Nov 2026)."""
import json

from django.urls import reverse

from master.models import SetelanHp
from master.tests.dasar import Dasar
from master.tests.test_hp import masuk_hp


class MenuHpTest(Dasar):
    def setUp(self):
        masuk_hp(self.client, self.admin)

    def _mode(self, mode):
        return self.client.post(reverse("hp:mode"), json.dumps({"mode": mode}), content_type="application/json")

    def test_lanjutan_bawaan(self):
        r = self.client.get(reverse("hp:menu"))
        self.assertEqual(r.status_code, 200)
        for teks in ("Piutang &amp; invoice", "Titipan", "Pesanan", "PO &amp; barang datang", "Setelan", "Buka master"):
            self.assertContains(r, teks)
        # Catat pembelian pindah dari tab bawah ke Menu (12 Nov 2026).
        self.assertContains(r, reverse("hp:input") + "?mode=datang")
        self.assertContains(r, reverse("hp:input") + "?mode=pesan")
        self.assertNotContains(self.client.get(reverse("hp:beranda")), f'href="{reverse("hp:input")}"')
        # Yang sudah ada di Beranda tidak diulang di Menu.
        for url in ("hp:transaksi", "hp:transfer", "hp:kas", "hp:biaya"):
            self.assertNotContains(r, f'href="{reverse(url)}"')
        b = self.client.get(reverse("hp:beranda"))
        self.assertContains(b, reverse("hp:tugas"))
        self.assertContains(b, reverse("hp:menu"))
        # Transaksi dibuka dari Beranda: tab Beranda yang menyala.
        t = self.client.get(reverse("hp:transaksi"))
        self.assertContains(t, f'href="{reverse("hp:beranda")}" class="aktif"')

    def test_simpel_hanya_tampilan(self):
        r = self._mode("simpel")
        self.assertEqual(r.json()["ok"], True)
        self.assertEqual(SetelanHp.objects.get(user=self.admin).mode, "simpel")
        m = self.client.get(reverse("hp:menu"))
        self.assertContains(m, "Setelan")
        self.assertNotContains(m, "Nota diproses")
        self.assertNotContains(m, "Buka master")
        b = self.client.get(reverse("hp:beranda"))
        self.assertNotContains(b, reverse("hp:tugas"))
        self.assertNotContains(b, reverse("hp:input"))
        self.assertNotContains(b, "Biaya toko")
        # Hak tidak berubah: halaman yang disembunyikan tetap bisa dibuka.
        self.assertEqual(self.client.get(reverse("hp:biaya")).status_code, 200)
        self.assertContains(self.client.get(reverse("hp:setelan")), 'data-mode="simpel" class="aktif"')
        self._mode("lanjutan")
        self.assertContains(self.client.get(reverse("hp:beranda")), reverse("hp:tugas"))

    def test_mode_tidak_dikenal(self):
        self.assertEqual(self._mode("admin").status_code, 400)
        self.assertFalse(SetelanHp.objects.exists())
