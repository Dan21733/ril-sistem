"""Kosongkan data percobaan master sebelum mulai dipakai sungguhan.

Yang dihapus: pesanan (PO) dan pembayarannya, retur, faktur di luar PO beserta
foto dan pembayarannya, transfer antar toko dan pelunasannya, nota manual beserta fotonya, opname, riwayat buka dus, riwayat stok, log, riwayat
impor. Yang TETAP: daftar toko, akun, pengaturan dan tarif PPN, rak, alias, data barang,
modal rata, pasangan buka dus, dan supplier (kecuali --supplier).

Tabel kasir (item, nota) TIDAK disentuh.
Cadangan dibuat dulu secara otomatis kalau MASTER_CADANGAN_KE diatur.
"""
import os

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from master import models as m

from ._cadangan import buat


class Command(BaseCommand):
    help = "Hapus data percobaan master (PO, faktur, opname, log, riwayat). Data barang dan akun tetap."

    def add_arguments(self, parser):
        parser.add_argument("--yakin", action="store_true")
        parser.add_argument("--supplier", action="store_true", help="Hapus juga semua supplier.")
        parser.add_argument("--tanpa-cadangan", action="store_true")

    def handle(self, *args, **o):
        if not o["yakin"]:
            raise CommandError("Ini menghapus data. Tambahkan --yakin kalau memang mau.")
        folder = os.getenv("MASTER_CADANGAN_KE", "").strip()
        if folder:
            berkas, _, _ = buat(folder, simpan=30)
            self.stdout.write(f"Cadangan sebelum dikosongkan: {berkas}")
        elif not o["tanpa_cadangan"]:
            raise CommandError("MASTER_CADANGAN_KE belum diatur, jadi tidak ada cadangan. "
                               "Atur dulu, atau tambahkan --tanpa-cadangan kalau memang tidak perlu.")
        kunci_foto = list(m.FotoFaktur.objects.values_list("object_key", flat=True)) + list(m.FotoNota.objects.values_list("object_key", flat=True))
        with transaction.atomic():
            urutan = [m.BayarFaktur, m.FotoFaktur, m.FakturManual, m.ReturBaris, m.Retur, m.Pembayaran,
                      m.PembelianBaris, m.Pembelian, m.OpnameBaris, m.Opname, m.BukaDus, m.MutasiStok, m.Log, m.Impor,
                      m.PelunasanSaldo, m.TransferBaris, m.Transfer, m.FotoNota, m.NotaManualBaris, m.NotaManual]
            if o["supplier"]:
                urutan.append(m.Supplier)
            for model in urutan:
                n, _ = model.objects.all().delete()
                if n:
                    self.stdout.write(f"  {model._meta.db_table}: {n} baris dihapus")
            transaction.on_commit(lambda: [default_storage.delete(k) for k in kunci_foto if default_storage.exists(k)])
        self.stdout.write(self.style.SUCCESS("Data percobaan master sudah dikosongkan."))
