"""Nama lain (alias) supplier.

Di toko satu supplier disebut macam-macam: nama perusahaan, nama pemilik,
nama sales, atau singkatan. Alias disimpan sebagai teks dipisah koma di
`Supplier.alias`; jumlah supplier kecil (puluhan), jadi pencocokan cukup di Python.
"""
from django.db.models import Q

from master.layanan.inti import Tolak, rapikan_teks
from master.models import Supplier


def rapikan_alias(teks, nama=""):
    """'ko ahong ,  SJ, sj' -> 'ko ahong, SJ' (tanpa dobel, tanpa nama utamanya)."""
    hasil, sudah = [], {rapikan_teks(nama).casefold()} if nama else set()
    for bagian in str(teks or "").replace("\n", ",").replace(";", ",").split(","):
        a = rapikan_teks(bagian)[:80]
        if a and a.casefold() not in sudah:
            sudah.add(a.casefold())
            hasil.append(a)
    gabung = ", ".join(hasil)
    if len(gabung) > 500:
        raise Tolak("Nama lain terlalu panjang (maksimal 500 huruf).")
    return gabung


def _semua_nama(s):
    return [s.nama, *s.daftar_alias]


def cek_bentrok(nama, alias, kecuali=None):
    """Tolak kalau nama/alias sudah dipakai supplier lain (nanti pencarian jadi ambigu)."""
    baru = {x.casefold(): x for x in [nama, *[a for a in alias.split(", ") if a]]}
    qs = Supplier.objects.all()
    if kecuali is not None:
        qs = qs.exclude(pk=kecuali.pk)
    for s in qs:
        for n in _semua_nama(s):
            if n.casefold() in baru:
                raise Tolak(f"'{baru[n.casefold()]}' sudah dipakai supplier {s.nama}.")


def cari_persis(nama):
    """Supplier yang nama atau salah satu aliasnya sama persis (tanpa beda huruf besar)."""
    nama = rapikan_teks(nama)
    if not nama:
        return None
    s = Supplier.objects.filter(nama__iexact=nama).first()
    if s:
        return s
    kunci = nama.casefold()
    for s in Supplier.objects.exclude(alias=""):
        if any(a.casefold() == kunci for a in s.daftar_alias):
            return s
    return None


def q_cari(kata, awalan=""):
    """Q untuk mencari supplier berdasarkan nama atau alias (awalan mis. 'supplier__')."""
    return Q(**{f"{awalan}nama__icontains": kata}) | Q(**{f"{awalan}alias__icontains": kata})
