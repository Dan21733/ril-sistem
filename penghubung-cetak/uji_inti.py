"""Uji inti Penghubung Cetak tanpa printer asli dan tanpa Windows.

Jalankan dari folder ini:  python uji_inti.py
"""
import http.client
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

DI_SINI = Path(__file__).resolve().parent
sys.path.insert(0, str(DI_SINI))
sys.path.insert(0, str(DI_SINI.parent / "POS" / "print-bridge"))
os.environ["APPDATA"] = tempfile.mkdtemp()

import bridge  # noqa: E402
import inti  # noqa: E402

ARP_WINDOWS = """
Interface: 192.168.1.50 --- 0xb
  Internet Address      Physical Address      Type
  192.168.1.1           50-91-e3-aa-bb-cc     dynamic
  192.168.1.224         00-26-ab-12-34-56     dynamic
  192.168.1.230         3c-2a-f4-99-88-77     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
"""


class UjiCari(unittest.TestCase):
    def jalankan(self, peng, terbuka, arp, epson=None, hidup=None):
        hidup = set(terbuka) if hidup is None else hidup
        return inti.cari_printer(
            peng, lan=["192.168.1.50"],
            pemindai=lambda alamat: [ip for ip in alamat if ip in terbuka],
            arp=lambda: arp, epson=lambda ip: (epson or {}).get(ip, ""),
            cek=lambda ip: ip in hidup)

    def test_arp_windows(self):
        t = inti.tabel_arp(ARP_WINDOWS)
        self.assertEqual(t["192.168.1.224"], "00-26-ab-12-34-56")
        self.assertNotIn("192.168.1.255", t)

    def test_arp_format_titik_dua(self):
        t = inti.tabel_arp("? (10.0.0.9) at 00:26:AB:12:34:56 [ether] on eth0\n10.0.0.9  00:26:AB:12:34:56")
        self.assertEqual(t["10.0.0.9"], "00-26-ab-12-34-56")

    def test_kandidat_tanpa_diri_sendiri(self):
        k = inti.kandidat_alamat(["192.168.1.50"])
        self.assertEqual(len(k), 253)
        self.assertNotIn("192.168.1.50", k)

    def test_satu_printer_langsung_dipakai(self):
        h = self.jalankan(inti.Pengaturan(), ["192.168.1.224"], inti.tabel_arp(ARP_WINDOWS))
        self.assertEqual((h.ip, h.mac), ("192.168.1.224", "00-26-ab-12-34-56"))

    def test_dua_printer_satu_epson(self):
        h = self.jalankan(inti.Pengaturan(), ["192.168.1.224", "192.168.1.230"],
                          inti.tabel_arp(ARP_WINDOWS), epson={"192.168.1.224": "TM-U220"})
        self.assertEqual((h.ip, h.model), ("192.168.1.224", "TM-U220"))

    def test_dua_printer_tidak_jelas_minta_pilih(self):
        h = self.jalankan(inti.Pengaturan(), ["192.168.1.224", "192.168.1.230"], inti.tabel_arp(ARP_WINDOWS))
        self.assertEqual((h.ip, h.pesan, len(h.semua)), ("", "pilih", 2))

    def test_ip_berubah_dikenali_lewat_mac(self):
        peng = inti.Pengaturan(printer_ip="192.168.1.224", printer_mac="00-26-ab-12-34-56")
        arp = {"192.168.1.231": "00-26-ab-12-34-56", "192.168.1.230": "3c-2a-f4-99-88-77"}
        h = self.jalankan(peng, ["192.168.1.230", "192.168.1.231"], arp)
        self.assertEqual((h.ip, h.pesan), ("192.168.1.231", "pindah IP"))

    def test_ip_lama_dipakai_perangkat_lain_tidak_dipercaya(self):
        peng = inti.Pengaturan(printer_ip="192.168.1.224", printer_mac="00-26-ab-12-34-56")
        arp = {"192.168.1.224": "3c-2a-f4-99-88-77", "192.168.1.240": "00-26-ab-12-34-56"}
        h = self.jalankan(peng, ["192.168.1.224", "192.168.1.240"], arp)
        self.assertEqual(h.ip, "192.168.1.240")

    def test_printer_tersimpan_hilang_tidak_asal_pilih_lain(self):
        peng = inti.Pengaturan(printer_ip="192.168.1.224", printer_mac="00-26-ab-12-34-56")
        h = self.jalankan(peng, ["192.168.1.230"], {"192.168.1.230": "3c-2a-f4-99-88-77"})
        self.assertEqual(h.ip, "")
        self.assertIn("tidak ditemukan", h.pesan)

    def test_tanpa_jaringan(self):
        h = inti.cari_printer(inti.Pengaturan(), lan=[], pemindai=lambda a: [], arp=dict,
                              epson=lambda ip: "", cek=lambda ip: False)
        self.assertIn("belum tersambung", h.pesan)

    def test_pindai_sungguhan(self):
        printer = bridge.FakePrinter().start()
        try:
            self.assertEqual(inti.pindai(["127.0.0.1"], port=printer.port), ["127.0.0.1"])
        finally:
            printer.stop()


class UjiPenghubung(unittest.TestCase):
    def setUp(self):
        self.printer = bridge.FakePrinter().start()
        inti.PORT_PRINTER = self.printer.port
        peng = inti.Pengaturan(printer_ip="127.0.0.1", printer_nama="Kasir 1", port_bridge=0)
        self.p = inti.Penghubung(peng, host="127.0.0.1").mulai()

    def tearDown(self):
        self.p.berhenti()
        self.printer.stop()
        inti.PORT_PRINTER = 9100

    def minta(self, metode, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.p.port, timeout=10)
        c.request(metode, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_cetak_dan_hitungan(self):
        r, d = self.minta("POST", "/print", b"\x1b@NOTA\n", {"X-Print-Job": "a1"})
        self.assertEqual((r.status, json.loads(d)["status"]), (200, "sent"))
        self.minta("POST", "/print", b"\x1b@NOTA\n", {"X-Print-Job": "a1"})
        self.assertEqual(self.p.statistik.tercetak, 1)
        self.assertIn("tidak dicetak dua kali", self.p.statistik.terakhir)

    def test_cors_preflight_dan_header(self):
        r, _ = self.minta("OPTIONS", "/print", headers={
            "Origin": "http://100.101.1.2:5173", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-print-job",
            "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(r.status, 204)
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "http://100.101.1.2:5173")
        self.assertIn("x-print-job", r.getheader("Access-Control-Allow-Headers"))
        self.assertEqual(r.getheader("Access-Control-Allow-Private-Network"), "true")
        r, d = self.minta("GET", "/print/health", headers={"Origin": "http://contoh"})
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "http://contoh")
        self.assertEqual(json.loads(d)["printer_status"], "ok")

    def test_tanpa_origin_tanpa_header_cors(self):
        r, _ = self.minta("GET", "/health")
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_ganti_printer_tanpa_restart(self):
        self.p.ganti_printer("192.0.2.1")
        self.assertEqual(self.p.httpd.config.printer_host, "192.0.2.1")
        self.p.ganti_printer("127.0.0.1")
        self.assertTrue(self.p.cek_printer())

    def test_printer_mati_dihitung_gagal(self):
        self.printer.stop()
        r, _ = self.minta("POST", "/print", b"x", {"X-Print-Job": "b1"})
        self.assertEqual(r.status, 503)
        self.assertEqual(self.p.statistik.gagal, 1)
        self.assertFalse(self.p.cek_printer())

    def test_halaman_uji(self):
        self.assertEqual(inti.cetak_uji("127.0.0.1", "Kasir 1"), "")
        import time
        for _ in range(100):
            if self.printer.jobs:
                break
            time.sleep(0.02)
        isi = self.printer.jobs[-1]
        self.assertIn(b"UJI CETAK", isi)
        self.assertTrue(isi.endswith(b"\x1d\x56\x00"))
        for baris in isi.split(b"\n"):
            self.assertLessEqual(len(baris), 48)


class UjiPengaturan(unittest.TestCase):
    def test_simpan_baca(self):
        berkas = Path(tempfile.mkdtemp()) / "p.json"
        inti.Pengaturan(printer_ip="1.2.3.4", printer_mac="aa").simpan(berkas)
        p = inti.Pengaturan.baca(berkas)
        self.assertEqual((p.printer_ip, p.printer_mac, p.port_bridge), ("1.2.3.4", "aa", 9101))

    def test_berkas_rusak_kembali_bawaan(self):
        berkas = Path(tempfile.mkdtemp()) / "p.json"
        berkas.write_text("{rusak")
        self.assertEqual(inti.Pengaturan.baca(berkas).printer_ip, "")

    def test_alamat(self):
        a = ["192.168.1.50", "100.101.2.3", "8.8.8.8"]
        self.assertEqual(inti.alamat_tailscale(a), "100.101.2.3")
        self.assertEqual(inti.alamat_lan(a), ["192.168.1.50"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
