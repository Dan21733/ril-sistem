"""Pulihkan cadangan master ke database yang BARU di-migrate.

Urutan:
  python manage.py migrate --settings=master.settings
  python manage.py master_pulihkan "path\\ke\\master-cadangan-....zip" --yakin --settings=master.settings
  python manage.py master_setup --settings=master.settings
"""
from django.core.management.base import BaseCommand, CommandError

from ._cadangan import pulihkan


class Command(BaseCommand):
    help = "Pulihkan data master dari berkas cadangan zip."

    def add_arguments(self, parser):
        parser.add_argument("berkas")
        parser.add_argument("--yakin", action="store_true")

    def handle(self, *args, **o):
        if not o["yakin"]:
            raise CommandError("Tambahkan --yakin untuk benar-benar memulihkan.")
        try:
            info = pulihkan(o["berkas"])
        except (ValueError, FileNotFoundError) as galat:
            raise CommandError(str(galat)) from galat
        self.stdout.write(self.style.SUCCESS(f"Dipulihkan dari cadangan {info['waktu']} ({info['database']})."))
