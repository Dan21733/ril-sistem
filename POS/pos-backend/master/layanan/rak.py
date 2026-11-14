"""Rak baris x kolom (14 Nov 2026).

Satu rak fisik (induk, mis. "A") dipecah jadi kotak: baris dihitung dari BAWAH (baris 1 = paling dekat
lantai, jadi menambah tingkat di atas tidak mengubah nomor lama), kolom dari KIRI saat menghadap rak.
Kode kotak: A-3-2 (rak A, baris 3, kolom 2). Rak 1 kolom memakai kode pendek A-3, sama dengan rak bertingkat lama.
Tiap kotak tetap satu baris tabel Rak, jadi semua yang menunjuk ke rak (varian, hitung, Siapkan) tidak berubah.
"""
import re

from django.db import transaction

from master.layanan import inti as layanan
from master.layanan.inti import Tolak
from master.models import Rak

BATAS_BARIS = 20
BATAS_KOLOM = 20


def kode_induk(teks):
    kode = re.sub(r"\s+", "", str(teks or "")).upper()
    if not kode:
        raise Tolak("Kode rak wajib diisi, mis. A.")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-/]*", kode):
        raise Tolak("Kode rak hanya huruf, angka, titik, strip.")
    return kode


def kode_kotak(induk, baris, kolom, satu_kolom):
    return f"{induk}-{baris}" if satu_kolom else f"{induk}-{baris}-{kolom}"


def buat(pengguna, *, induk, nama, baris, kolom, tempat=None, pic=None, langsung=False):
    """Buat semua kotak rak induk (baris x kolom). Kotak yang sudah ada (posisi sama) dilewati, jadi juga
    dipakai untuk menambah baris/kolom rak yang sudah ada. Hasil: (dibuat, sudah_ada)."""
    induk = kode_induk(induk)
    nama = layanan.rapikan_teks(nama) or f"Rak {induk}"
    if not 1 <= baris <= BATAS_BARIS:
        raise Tolak(f"Jumlah baris 1 sampai {BATAS_BARIS}.")
    if not 1 <= kolom <= BATAS_KOLOM:
        raise Tolak(f"Jumlah kolom 1 sampai {BATAS_KOLOM}.")
    ada_posisi = {(r.baris, r.kolom or 1): r for r in Rak.objects.filter(induk=induk)}
    satu = kolom == 1 and all((k or 1) == 1 for _, k in ada_posisi)
    if len(kode_kotak(induk, baris, kolom, satu)) > 20:
        raise Tolak("Kode rak terlalu panjang (kode kotak maksimal 20 karakter).")
    rencana = []
    for b in range(1, baris + 1):
        for k in range(1, kolom + 1):
            if (b, k) in ada_posisi:
                continue
            rencana.append(Rak(kode=kode_kotak(induk, b, k, satu), nama=nama, tempat=tempat, pic=pic,
                               langsung=langsung, induk=induk, baris=b, kolom=k))
    bentrok = set(Rak.objects.filter(kode__in=[r.kode for r in rencana]).values_list("kode", flat=True))
    if bentrok:
        raise Tolak("Kode sudah dipakai rak lain: " + ", ".join(sorted(bentrok)[:5])
                    + ". Pakai kode rak lain atau hapus rak itu dulu.")
    with transaction.atomic():
        Rak.objects.bulk_create(rencana)
        if rencana:
            layanan.catat(pengguna, aksi="tambah", objek="rak", kolom="rak",
                          baru=f"Rak {induk}: {len(rencana)} kotak ({baris} baris x {kolom} kolom) - {nama}")
    return rencana, len(ada_posisi)


def urut(r):
    """Urutan tampil: per rak induk, lalu baris, lalu kolom; rak tunggal urut kode alami."""
    alami = [(0, int(x), "") if x.isdigit() else (1, 0, x.lower()) for x in re.split(r"(\d+)", r.induk or r.kode) if x]
    return (alami, r.baris or 0, r.kolom or 0, r.kode)


def label(r):
    """Untuk karyawan: "Rak A · baris 3 · kolom 2" (kolom tidak disebut untuk rak 1 kolom, kode A-3)."""
    if not r.induk or not r.baris:
        return f"Rak {r.kode}"
    return f"Rak {r.induk} · baris {r.baris}" + (f" · kolom {r.kolom}" if r.kode == f"{r.induk}-{r.baris}-{r.kolom}" else "")


def pilihan_tugas():
    """Pilihan rak di form buat tugas: satu pilihan per rak induk ("Rak G · 42 kotak", nilai "induk:G"),
    rak tunggal tetap sendiri. Diuraikan lagi jadi kode kotak oleh `uraikan`."""
    hasil, per = [], {}
    for r in sorted(Rak.objects.select_related("tempat"), key=urut):
        if r.induk:
            if r.induk not in per:
                per[r.induk] = {"nilai": f"induk:{r.induk}", "kode": r.induk, "nama": r.nama, "tempat": r.tempat,
                                "tempat_id": r.tempat_id, "n": 0}
                hasil.append(per[r.induk])
            per[r.induk]["n"] += 1
        else:
            hasil.append({"nilai": r.kode, "kode": r.kode, "nama": r.nama, "tempat": r.tempat, "tempat_id": r.tempat_id, "n": 1})
    return hasil


def uraikan(nilai):
    """["induk:G", "R1"] -> semua kode kotak rak G + "R1"."""
    nilai = list(nilai or [])
    induk = [n[6:] for n in nilai if str(n).startswith("induk:")]
    kode = [n for n in nilai if not str(n).startswith("induk:")]
    if induk:
        kode += list(Rak.objects.filter(induk__in=induk).values_list("kode", flat=True))
    return kode
