"""Uang per metode untuk satu nota, termasuk bayar campur (keputusan pemilik 12 Nov 2026).

Bersih = uang yang benar-benar tinggal di toko per metode. Kembalian selalu tunai
dari laci, jadi untuk nota campur: tunai bersih = tunai diterima - kembalian
(boleh minus kalau pembeli transfer lebih), transfer/EDC = sebesar diterima.
"""
from collections import defaultdict

METODE = ("cash", "edc", "bank_transfer")


def bersih(payment_method, payment_amount, change_amount, rincian=None):
    """{metode: jumlah bersih}. `rincian` = [(metode, jumlah diterima)] untuk nota campur."""
    bayar, kembali = int(payment_amount or 0), int(change_amount or 0)
    if payment_method != "campur":
        return {payment_method: bayar - kembali} if payment_method else {}
    hasil = defaultdict(int)
    for metode, jumlah in rincian or []:
        hasil[metode] += int(jumlah or 0)
    if kembali:
        hasil["cash"] -= kembali
    return dict(hasil)


def rincian_nota(sync_ids):
    """{sync_id: [(metode, jumlah diterima)]} untuk nota campur."""
    from .models import NotaBayar
    hasil = defaultdict(list)
    ids = [s for s in sync_ids if s]
    if not ids:
        return {}
    for sid, metode, jumlah in NotaBayar.objects.filter(sync_id__in=ids).values_list("sync_id", "metode", "jumlah"):
        hasil[sid].append((metode, jumlah))
    return dict(hasil)


def rincian_payload(payload):
    """Rincian dari payload nota (nota yang ditahan server / belum masuk tabel)."""
    return [(r.get("method"), r.get("amount")) for r in (payload or {}).get("payments") or [] if isinstance(r, dict)]
