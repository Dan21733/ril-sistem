"""Siapkan master: tabel kasir, peran, dan pasangan data untuk setiap barang.

Aman dijalankan berulang. Jalankan setelah `migrate`.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from master.layanan import inti as layanan
from master.layanan.legacy import buat_tabel_pos
from master.akun.akses import PERAN


class Command(BaseCommand):
    help = "Buat peran master dan lengkapi data pendamping barang."

    def handle(self, *args, **options):
        # Tabel kasir (item, nota, worker, ...) dibuat kalau belum ada. Aman di
        # database yang sudah punya tabelnya: semua perintah IF NOT EXISTS.
        buat_tabel_pos()
        for nama in PERAN:
            _, dibuat = Group.objects.get_or_create(name=nama)
            if dibuat:
                self.stdout.write(f"Peran dibuat: {nama}")
        varian, item = layanan.sinkron_info()
        self.stdout.write(f"Data pendamping: {varian} varian dan {item} barang baru dipasangkan.")
        from master.layanan import alias_kasir
        hasil = alias_kasir.gabung_sekali()
        self.stdout.write(f"Alias kasir: {hasil['digabung']} barang digabung dari alias lama, "
                          f"{hasil['ditulis']} frasa disamakan dengan master.")
        if not get_user_model().objects.filter(is_superuser=True).exists():
            self.stdout.write(self.style.WARNING(
                "Belum ada akun utama. Buat dengan:\n"
                "  python manage.py createsuperuser --settings=master.settings"
            ))
        self.stdout.write(self.style.SUCCESS("Master siap."))
