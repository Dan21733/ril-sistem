"""Menahan nota final tablet yang tidak lolos pemeriksaan server (NotaTinjau).

Tablet mengirim `final_lokal: true` untuk nota yang PIN-nya sudah cocok di
tablet dan sudah final (dicetak, diserahkan ke pembeli). Nota seperti itu
tidak bisa "diperbaiki" kasir lagi, jadi penolakan server hanya membuat nota
nyangkut di tablet dan memblokir penjualan berikutnya. Sebagai gantinya nota
ditahan di sini, tablet diberi jawaban "terkirim", dan pemilik memutuskan dari
master.

Yang TIDAK ditahan: konflik identitas nota (409) dan pembatasan percobaan PIN
(429); keduanya tetap dijawab seperti biasa.
"""
import uuid

from .models import NotaTinjau

PESAN_DITAHAN = "Nota diterima server dan menunggu ACC pemilik"


def sync_id_dari(data):
    try:
        return uuid.UUID(str((data or {}).get("sync_id")))
    except (TypeError, ValueError, AttributeError):
        return None


def sudah_ditahan(sync_id):
    if sync_id is None:
        return None
    return NotaTinjau.objects.filter(sync_id=sync_id).first()


def _bersihkan(data):
    """Salinan payload untuk disimpan: tanpa PIN, aman untuk JSON."""
    import json

    bersih = {k: v for k, v in dict(data or {}).items() if k not in ("pin", "final_lokal")}
    return json.loads(json.dumps(bersih, default=str))


def tahan(data, alasan, device_code=None, worker_id=None):
    """Simpan nota. Kembalikan baris NotaTinjau, atau None kalau sync_id tidak sah."""
    sync_id = sync_id_dari(data)
    if sync_id is None:
        return None
    nota, _ = NotaTinjau.objects.get_or_create(
        sync_id=sync_id,
        defaults={
            "transaction_id": (data or {}).get("transaction_id") or None,
            "device_code": device_code or "",
            "payload": _bersihkan(data),
            "worker_id": worker_id,
            "alasan": str(alasan).replace("pin: ", "PIN: ", 1)[:2000],
        },
    )
    return nota


def jawaban(nota):
    """Bentuk jawaban yang dianggap tablet sebagai nota terkirim."""
    return {
        "status": "new",
        "sync_id": str(nota.sync_id),
        "transaction_id": nota.transaction_id,
        "ditahan": True,
        "detail": f"{PESAN_DITAHAN}: {nota.alasan}",
    }
