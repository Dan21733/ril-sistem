"""Penjadwal master: pekerjaan berkala yang tidak menunggu ada orang membuka halaman.

Sekarang: ganti PIN karyawan terjadwal, menghapus PIN terbaca yang sudah
lewat masa tampilnya, dan notifikasi HP untuk permintaan ACC baru. Di server rumah
dijalankan layanan `jadwal` di compose:

    python manage.py master_jadwal --terus --settings=master.settings
"""
import time
import traceback

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from master.layanan import karyawan


def satu_putaran():
    dibuat = karyawan.jalankan_rotasi()
    dibersihkan = karyawan.bersihkan_pin_tampil()
    karyawan.detak()
    try:
        from master.layanan import push
        push.kirim_baru()
    except Exception:  # notifikasi tidak boleh menghentikan pekerjaan lain
        import logging
        logging.getLogger(__name__).exception("Kirim notifikasi gagal")
    return dibuat, dibersihkan


class Command(BaseCommand):
    help = "Jalankan pekerjaan berkala master (ganti PIN terjadwal, bersih-bersih)."

    def add_arguments(self, parser):
        parser.add_argument("--terus", action="store_true", help="Ulang tiap --jeda detik sampai dihentikan.")
        parser.add_argument("--jeda", type=int, default=60)

    def handle(self, *args, **opsi):
        if not opsi["terus"]:
            dibuat, dibersihkan = satu_putaran()
            self.stdout.write(f"PIN baru: {dibuat}, PIN terbaca dihapus: {dibersihkan}")
            return
        self.stdout.write(f"Penjadwal master jalan, tiap {opsi['jeda']} detik.")
        while True:
            close_old_connections()
            try:
                dibuat, _ = satu_putaran()
                if dibuat:
                    self.stdout.write(f"{dibuat} PIN baru disiapkan.")
            except Exception:  # database sebentar mati, dsb.: coba lagi putaran berikutnya
                self.stderr.write(traceback.format_exc(limit=3))
            self.stdout.flush()
            time.sleep(max(5, opsi["jeda"]))
