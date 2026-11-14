"""Denah dilihat dari atas (14 Nov 2026).

- Denah lokasi: letak tiap ruangan (Ruang.x/y/w/h) di petak LOKASI_KOLOM x LOKASI_BARIS.
- Denah ruangan: Ruang.denah = {"blok": [{"t": "rak"|"area"|"pintu", "id": induk/kode/"", "x", "y", "w", "h"}]}
  di petak RUANG_KOLOM x RUANG_BARIS. "rak" menunjuk rak induk (A, TK1), "area" menunjuk kode area lantai/sementara.
Hanya gambar penunjuk arah: tidak mengubah stok atau kode rak apa pun.
"""
from django.db import transaction

from master.layanan import inti as layanan
from master.layanan.inti import Tolak
from master.models import Rak, Ruang

LOKASI_KOLOM, LOKASI_BARIS = 24, 24
RUANG_KOLOM, RUANG_BARIS = 16, 16


def _kotak(d, kolom, baris):
    try:
        x, y, w, h = (int(d[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        raise Tolak("Isi denah tidak terbaca.")
    if not (0 <= x < kolom and 0 <= y < baris and 1 <= w <= kolom - x and 1 <= h <= baris - y):
        raise Tolak("Ada kotak di luar batas denah.")
    return x, y, w, h


def simpan_lokasi(pengguna, lokasi, isi):
    """isi = [{"id": ruang_id, "x", "y", "w", "h"} | {"id": ruang_id, "hapus": true}]"""
    ruang = {r.pk: r for r in Ruang.objects.filter(lokasi=lokasi)}
    with transaction.atomic():
        for d in isi or []:
            r = ruang.get(int(d.get("id") or 0)) if str(d.get("id") or "").isdigit() else None
            if r is None:
                raise Tolak("Ruangan tidak ditemukan di lokasi ini.")
            if d.get("hapus"):
                r.x = r.y = r.w = r.h = None
            else:
                r.x, r.y, r.w, r.h = _kotak(d, LOKASI_KOLOM, LOKASI_BARIS)
            r.save(update_fields=["x", "y", "w", "h"])
        layanan.catat(pengguna, objek="ruang", kolom="denah", baru=f"Denah {lokasi.nama} diubah")


def simpan_ruang(pengguna, ruang, blok):
    """blok = [{"t", "id", "x", "y", "w", "h"}]; rak/area harus milik ruangan ini."""
    induk = set(Rak.objects.filter(ruang=ruang, jenis="rak").exclude(induk="").values_list("induk", flat=True))
    tunggal = set(Rak.objects.filter(ruang=ruang).filter(induk="").values_list("kode", flat=True))
    hasil = []
    for d in blok or []:
        t = d.get("t")
        x, y, w, h = _kotak(d, RUANG_KOLOM, RUANG_BARIS)
        if t == "pintu":
            hasil.append({"t": "pintu", "id": "", "x": x, "y": y, "w": w, "h": h})
        elif t in ("rak", "area") and (d.get("id") in induk or d.get("id") in tunggal):
            hasil.append({"t": t, "id": d["id"], "x": x, "y": y, "w": w, "h": h})
        else:
            raise Tolak(f"Rak/area {d.get('id')} bukan isi ruangan {ruang.nama}.")
    ruang.denah = {"blok": hasil}
    ruang.save(update_fields=["denah"])
    layanan.catat(pengguna, objek="ruang", kolom="denah", baru=f"Denah {ruang.nama}: {len(hasil)} kotak")


def isi_ruang(ruang):
    """Yang bisa ditaruh di denah ruangan: rak induk + rak tunggal/area di ruangan itu."""
    hasil, sudah = [], set()
    for r in Rak.objects.filter(ruang=ruang).order_by("induk", "kode"):
        if r.induk:
            if r.induk not in sudah:
                sudah.add(r.induk)
                hasil.append({"t": "rak", "id": r.induk, "nama": f"Rak {r.induk}"})
        else:
            hasil.append({"t": "rak" if r.jenis == "rak" else "area", "id": r.kode,
                          "nama": r.nama if r.jenis != "rak" else f"Rak {r.kode}", "jenis": r.jenis})
    return hasil


def data_pemilih():
    """Untuk pemilih rak: {nama lokasi: {"ukuran": [kolom, baris], "ruang": {nama ruangan: {x,y,w,h, blok}}}}."""
    hasil = {}
    for r in Ruang.objects.select_related("lokasi"):
        l = hasil.setdefault(r.lokasi.nama, {"ukuran": [LOKASI_KOLOM, LOKASI_BARIS],
                                             "ukuran_ruang": [RUANG_KOLOM, RUANG_BARIS], "ruang": {}})
        l["ruang"][r.nama] = {"x": r.x, "y": r.y, "w": r.w, "h": r.h, "blok": (r.denah or {}).get("blok", [])}
    return hasil
