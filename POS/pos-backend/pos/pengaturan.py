"""Aturan kasir yang bisa diubah pemilik dari master (tabel `pos_setting`).

Nilai dibaca ulang paling lambat tiap 10 detik per proses, jadi perubahan dari
master terasa di kasir tanpa restart.
"""
import time

from .models import PosSetting

BAWAAN = {
    # "buka" = tablet baru boleh mendaftar sendiri; "tutup" = hanya tablet
    # yang sudah terdaftar dan aktif yang bisa memakai kasir (whitelist).
    "pendaftaran_tablet": "buka",
    # Berapa hari PIN lama masih diterima untuk nota yang DIBUAT saat PIN itu
    # berlaku (nota offline yang telat terkirim).
    "pin_tenggang_hari": "14",
    # Ganti PIN otomatis (dijalankan `master_jadwal`).
    "rotasi_aktif": "0",
    "rotasi_interval_hari": "14",
    "rotasi_hari": "5",  # 0 = Senin ... 5 = Sabtu, 6 = Minggu
    "rotasi_jam": "21:00",
    "rotasi_siapkan_hari": "2",
    "rotasi_berikut": "",  # ISO waktu ganti berikutnya; diisi master
    "jadwal_detak": "",  # ISO waktu terakhir penjadwal master hidup
    # Uang yang boleh keluar lewat void/retur/refund, persen dari total nota.
    "refund_maks_persen": "100",
    # worker_id (dipisah koma) yang PIN-nya boleh menyetujui harga di bawah floor.
    "penyetuju_floor": "",
    # worker_id (dipisah koma) yang PIN-nya TIDAK boleh dipakai jualan/void/retur di kasir
    # (mis. staf gudang). Daftar larangan: karyawan yang tidak disebut tetap boleh.
    "bukan_kasir": "",
}


def penyetuju_floor():
    return {x.strip() for x in str(baca("penyetuju_floor")).split(",") if x.strip()}


def bukan_kasir():
    return {x.strip() for x in str(baca("bukan_kasir")).split(",") if x.strip()}

_SIMPAN_DETIK = 10
_cache = {"waktu": 0.0, "isi": {}}


def _muat():
    if time.monotonic() - _cache["waktu"] > _SIMPAN_DETIK:
        _cache["isi"] = dict(PosSetting.objects.values_list("kunci", "nilai"))
        _cache["waktu"] = time.monotonic()
    return _cache["isi"]


def lupakan_cache():
    _cache["waktu"] = 0.0


def baca(kunci):
    return _muat().get(kunci, BAWAAN.get(kunci, ""))


def angka(kunci, bawaan=0):
    try:
        return int(str(baca(kunci)).strip())
    except (TypeError, ValueError):
        try:
            return int(BAWAAN.get(kunci, bawaan))
        except (TypeError, ValueError):
            return bawaan


def semua():
    isi = dict(BAWAAN)
    isi.update(_muat())
    return isi


def simpan(**pasangan):
    for kunci, nilai in pasangan.items():
        PosSetting.objects.update_or_create(kunci=kunci, defaults={"nilai": "" if nilai is None else str(nilai)})
    lupakan_cache()
