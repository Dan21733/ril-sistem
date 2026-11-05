"""Kunci tablet kasir dan perangkat gudang dari master (mis. saat stock opname, 5 Nov 2026).

Tablet kasir yang dikunci: server menolak katalog, nota baru, riwayat, dll. (423) dan tablet
menutup layarnya; nota offline yang sudah terjadi tetap boleh terkirim. Perangkat gudang yang
dikunci: hanya daftar Tugas dan halaman hitung yang terbuka.

Satu toko kasir dulu: semua tablet kasir milik toko pemegang kasir sampai tablet diikat ke toko.
"""
import uuid

from django.utils import timezone

from pos.models import Device, DeviceStatus

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, rapikan_teks
from master.models import PerangkatOps


def _alasan(alasan):
    return rapikan_teks(alasan or "")[:200]


def kunci_tablet(pengguna, device_id, kunci, alasan=""):
    """device_id = "semua" untuk semua tablet aktif. Hasil: jumlah tablet yang berubah."""
    if device_id == "semua":
        ids = list(Device.objects.values_list("id", flat=True))
    else:
        try:
            ids = [uuid.UUID(str(device_id))]
        except ValueError:
            raise Tolak("Tablet tidak ditemukan.")
        if not Device.objects.filter(id=ids[0]).exists():
            raise Tolak("Tablet tidak ditemukan.")
    alasan = _alasan(alasan)
    for i in ids:
        DeviceStatus.objects.get_or_create(device_id=i)
    n = DeviceStatus.objects.filter(device_id__in=ids).update(
        dikunci=bool(kunci), kunci_alasan=alasan if kunci else "", kunci_waktu=timezone.now() if kunci else None)
    layanan.catat(pengguna, aksi="ubah", objek="tablet", kolom="kunci kasir",
                  baru=f"{'dikunci' if kunci else 'dibuka'}: {'semua tablet' if device_id == 'semua' else device_id}",
                  catatan=alasan)
    return n


def kunci_ops(pengguna, pid, kunci, alasan=""):
    """pid = "semua" untuk semua perangkat gudang yang disetujui."""
    qs = PerangkatOps.objects.filter(disetujui__isnull=False, dicabut__isnull=True)
    if pid != "semua":
        qs = qs.filter(pk=pid if str(pid or "").isdigit() else 0)
        if not qs.exists():
            raise Tolak("Perangkat tidak ditemukan.")
    alasan = _alasan(alasan)
    n = qs.update(dikunci=bool(kunci), kunci_alasan=alasan if kunci else "")
    layanan.catat(pengguna, aksi="ubah", objek="perangkat", kolom="kunci gudang",
                  baru=f"{'dikunci' if kunci else 'dibuka'}: {'semua perangkat' if pid == 'semua' else pid}",
                  catatan=alasan)
    return n
