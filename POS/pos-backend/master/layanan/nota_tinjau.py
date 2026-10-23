"""Nota perlu ACC: nota final tablet yang ditahan server (pos.models.NotaTinjau).

Nota seperti ini sudah terjadi di toko (barang sudah keluar, uang sudah
diterima, nota sudah dicetak), tetapi gagal pemeriksaan server: PIN tidak
dikenal atau sudah lewat masa berlakunya, harga di bawah floor yang sekarang,
barang sudah tidak ada, dll. Pemilik memilih:

- **Terima**: nota masuk penjualan seperti nota biasa (waktu nota asli, stok
  dipotong). Harga tidak dicek ulang terhadap floor sekarang. Kasir dipilih
  pemilik kalau PIN-nya tidak dikenali.
- **Tolak**: nota tetap tercatat di sini beserta alasannya, tetapi tidak
  dihitung sebagai penjualan dan tidak memotong stok.
"""
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from pos.models import Item, NotaTinjau, Worker
from pos.serializers import TransactionCreateSerializer
from pos.transaction_service import TransactionValidationError, persist_transaction
from pos.transaction_sync import TransactionSyncConflict

from master.layanan import inti as layanan
from master.layanan.inti import Tolak


def jumlah_menunggu():
    return NotaTinjau.objects.filter(status="menunggu").count()


def _ringkas(nota, item_ada, pekerja):
    data = nota.payload or {}
    baris = []
    for b in data.get("items") or []:
        if not isinstance(b, dict):
            continue
        kunci = (b.get("item_name"), b.get("variant"))
        item = item_ada.get(kunci)
        harga = b.get("unit_price") or 0
        baris.append({
            "nama": b.get("item_name"), "varian": b.get("variant"), "jumlah": b.get("quantity"),
            "harga": harga, "subtotal": b.get("subtotal"),
            "ada": item is not None,
            "floor": item.min_price if item else None,
            "di_bawah_floor": bool(item and item.min_price and harga < item.min_price),
        })
    waktu = parse_datetime(str(data.get("transaction_time") or "")) if data.get("transaction_time") else None
    return {
        "n": nota, "waktu_nota": waktu, "baris": baris, "total": data.get("total_amount"),
        "bayar": data.get("payment_method"), "pembeli": data.get("cust_name"),
        "kasir": pekerja.get(nota.worker_id) if nota.worker_id else None,
        "barang_hilang": any(not b["ada"] for b in baris),
    }


def daftar(status="menunggu", batas=200):
    qs = NotaTinjau.objects.all()
    if status:
        qs = qs.filter(status=status)
    notas = list(qs.order_by("masuk")[:batas] if status == "menunggu" else qs[:batas])
    kunci = {(b.get("item_name"), b.get("variant")) for n in notas for b in (n.payload or {}).get("items") or []
             if isinstance(b, dict)}
    item_ada = {}
    for nama in {k[0] for k in kunci}:
        for it in Item.objects.filter(item_name=nama).only("item_name", "variant", "min_price"):
            item_ada[(it.item_name, it.variant)] = it
    pekerja = dict(Worker.objects.values_list("id", "name"))
    return [_ringkas(n, item_ada, pekerja) for n in notas]


def _ambil(sync_id):
    nota = NotaTinjau.objects.select_for_update().filter(sync_id=sync_id).first()
    if nota is None:
        raise Tolak("Nota tidak ditemukan.")
    if nota.status != "menunggu":
        raise Tolak("Nota ini sudah diputuskan sebelumnya.")
    return nota


def terima(pengguna, sync_id, worker_id, catatan=""):
    with transaction.atomic():
        nota = _ambil(sync_id)
        worker_id = (worker_id or nota.worker_id or "").strip()
        if not Worker.objects.filter(id=worker_id).exists():
            raise Tolak("Pilih kasir yang membuat nota ini.")
        s = TransactionCreateSerializer(data=nota.payload)
        if not s.is_valid():
            raise Tolak(f"Isi nota tidak lengkap, jadi tidak bisa diterima (hanya bisa ditolak): {s.errors}")
        try:
            persist_transaction(s.validated_data, worker_id, nota.device_code or None, lewati_harga=True)
        except TransactionValidationError as e:
            pesan = str(e)
            if "tidak ada" in pesan.lower():
                pesan += " Pulihkan barangnya dari Barang dihapus, atau tolak nota ini."
            raise Tolak(pesan)
        except TransactionSyncConflict as e:
            raise Tolak(f"Nota bentrok dengan nota yang sudah ada: {e}")
        nota.status = "diterima"
        nota.worker_id = worker_id
        nota.diputuskan_oleh = getattr(pengguna, "username", "") or "sistem"
        nota.diputuskan_waktu = timezone.now()
        nota.catatan = layanan.rapikan_teks(catatan)[:500]
        nota.save()
        layanan.catat(pengguna, aksi="tambah", objek="nota ACC", kolom="nota perlu ACC",
                      baru=f"Diterima: {nota.transaction_id or nota.sync_id} (kasir {worker_id})",
                      catatan=f"Alasan ditahan: {nota.alasan}")
    return nota


def tolak(pengguna, sync_id, alasan):
    alasan = layanan.rapikan_teks(alasan)[:500]
    if not alasan:
        raise Tolak("Tulis alasan menolak nota ini.")
    with transaction.atomic():
        nota = _ambil(sync_id)
        nota.status = "ditolak"
        nota.diputuskan_oleh = getattr(pengguna, "username", "") or "sistem"
        nota.diputuskan_waktu = timezone.now()
        nota.catatan = alasan
        nota.save()
        layanan.catat(pengguna, aksi="ubah", objek="nota ACC", kolom="nota perlu ACC",
                      baru=f"Ditolak: {nota.transaction_id or nota.sync_id}", catatan=alasan)
    return nota
