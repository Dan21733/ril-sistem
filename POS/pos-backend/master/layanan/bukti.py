"""Cek bukti transfer di HP (permintaan pemilik, 6 Nov 2026).

Kasir mengunggah foto bukti transfer per nota. Pemilik mencocokkannya dengan mutasi
rekening, lalu menandai "Sudah dicek". Tanda disimpan di master (`BuktiDicek`); tabel
bukti milik POS tidak diubah.
"""
from django.db import transaction

from master.layanan.inti import Tolak, catat


def nota_transfer(mulai, selesai, toko):
    """Nota kasir bayar transfer dalam periode, tiap nota membawa daftar buktinya + status cek."""
    from master.layanan import laporan
    from master.models import BuktiDicek
    from pos.models import PaymentProof

    nota, _ = laporan.ambil(mulai, selesai, toko=toko, metode="bank_transfer")
    nota = [n for n in nota if not n.get("manual")]
    bukti = {}
    for b in PaymentProof.objects.filter(sync_id__in=[n["sync_id"] for n in nota]).order_by("uploaded_at"):
        bukti.setdefault(b.sync_id, []).append(b)
    dicek = {d.proof_id: d for d in BuktiDicek.objects.filter(
        proof_id__in=[b.proof_id for daftar in bukti.values() for b in daftar])}
    hasil = []
    for n in nota:
        daftar = [{"bukti": b, "dicek": dicek.get(b.proof_id)} for b in bukti.get(n["sync_id"], [])]
        hasil.append({**n, "bukti": daftar, "semua_dicek": bool(daftar) and all(x["dicek"] for x in daftar)})
    return hasil


def ringkas(nota):
    """Jumlah nota transfer, bukti, dan bukti yang belum dicek (untuk beranda)."""
    semua = [x for n in nota for x in n["bukti"]]
    return {
        "nota": len(nota),
        "bukti": len(semua),
        "belum": sum(1 for x in semua if not x["dicek"]),
        "tanpa_bukti": sum(1 for n in nota if not n["bukti"]),
    }


def tandai(pengguna, proof_id, dicek=True):
    from master.models import BuktiDicek
    from pos.models import PaymentProof

    bukti = PaymentProof.objects.filter(pk=proof_id).first()
    if bukti is None:
        raise Tolak("Bukti transfer tidak ditemukan.")
    nama = pengguna.first_name or pengguna.username
    with transaction.atomic():
        if dicek:
            _, baru = BuktiDicek.objects.get_or_create(proof_id=bukti.proof_id, defaults={"oleh": nama})
            if baru:
                catat(pengguna, aksi="ubah", objek="bukti_transfer", kolom="dicek", lama="belum", baru="dicek",
                      catatan=f"nota {bukti.sync_id}")
        else:
            hapus, _ = BuktiDicek.objects.filter(proof_id=bukti.proof_id).delete()
            if hapus:
                catat(pengguna, aksi="ubah", objek="bukti_transfer", kolom="dicek", lama="dicek", baru="belum",
                      catatan=f"nota {bukti.sync_id}")
