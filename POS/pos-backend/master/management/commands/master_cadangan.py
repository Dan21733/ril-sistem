"""Buat cadangan data master ke sebuah folder (sebaiknya folder yang tersalin ke luar PC).

Contoh:
  python manage.py master_cadangan --ke "G:\\My Drive\\Cadangan Master" --settings=master.settings
Folder bawaan bisa diisi di master/.env:  MASTER_CADANGAN_KE=G:\\My Drive\\Cadangan Master
"""
import os

from django.core.management.base import BaseCommand, CommandError

from ._cadangan import buat


class Command(BaseCommand):
    help = "Cadangkan data master (data + foto faktur) ke satu berkas zip."

    def add_arguments(self, parser):
        parser.add_argument("--ke", help="Folder tujuan. Bawaan: MASTER_CADANGAN_KE di master/.env.")
        parser.add_argument("--simpan", type=int, default=30, help="Berapa cadangan terakhir yang disimpan (bawaan 30).")
        parser.add_argument("--jika-diatur", action="store_true",
                            help="Diam saja kalau folder tujuan belum diatur (dipakai Start Master.bat).")

    def handle(self, *args, **o):
        folder = o["ke"] or os.getenv("MASTER_CADANGAN_KE", "").strip()
        if not folder:
            if o["jika_diatur"]:
                self.stdout.write(self.style.WARNING("Cadangan master belum diatur (MASTER_CADANGAN_KE di master/.env)."))
                return
            raise CommandError("Isi --ke, atau MASTER_CADANGAN_KE di master/.env.")
        try:
            berkas, info, hilang = buat(folder, o["simpan"])
        except OSError as galat:
            # Folder tujuan tidak terjangkau (drive cloud belum login, NAS mati): jangan halangi master.
            if o["jika_diatur"]:
                self.stdout.write(self.style.ERROR(f"CADANGAN GAGAL: {galat}"))
                return
            raise CommandError(f"Cadangan gagal: {galat}") from galat
        j = info["jumlah"]
        self.stdout.write(self.style.SUCCESS(
            f"Cadangan tersimpan: {berkas}\n"
            f"  supplier {j.get('master.supplier', 0)}, PO {j.get('master.pembelian', 0)}, "
            f"faktur di luar PO {j.get('master.fakturmanual', 0)}, foto {info['foto']}"))
        if hilang:
            self.stdout.write(self.style.WARNING(f"  {len(hilang)} foto tidak ditemukan di server dan tidak ikut."))
