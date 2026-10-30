"""Hapus data uji coba: nota kasir dan karyawan yang dibuat waktu mencoba aplikasi.

Hanya untuk akun superuser (Django `is_superuser`), bukan hak yang bisa dicentang di master.
Bukan untuk menghapus jejak: setiap penghapusan meninggalkan catatan di Riwayat perubahan
(siapa, kapan, alasan, dan isi nota yang dihapus), dan catatan itu tidak bisa dihapus dari master.

Nota kasir: ikut terhapus rincian barang, bukti bayar, retur/refund/void beserta penerimaannya,
mutasi stok kasir, persetujuan floor, dan catatan nota ditahan. Stok bisa dikembalikan: jumlah
terjual dikurangi yang sudah kembali lewat penerimaan retur, dicatat di riwayat stok sebagai
"Koreksi salah input".
"""
import json
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, rapikan_teks

BATAS = 200


def boleh(user):
    return bool(user and user.is_authenticated and user.is_active and user.is_superuser)


def _wajib(user):
    if not boleh(user):
        raise Tolak("Hanya superuser yang boleh menghapus data uji.")


def daftar_nota(*, awal, akhir, kasir="", perangkat="", cari=""):
    """Nota kasir pada rentang tanggal (terbaru dulu), dengan isi dan jumlah retur."""
    from pos.models import RefundEvent, TransactionDetail, Transactions, Worker
    from master.layanan import laporan
    mulai, selesai = laporan.rentang(awal, akhir)
    qs = Transactions.objects.filter(transaction_time__gte=mulai, transaction_time__lt=selesai)
    if kasir:
        qs = qs.filter(worker_id=kasir)
    if perangkat:
        qs = qs.filter(transaction_id__startswith=perangkat + "-")
    if cari:
        qs = qs.filter(transaction_id__icontains=cari)
    nota = list(qs.order_by("-transaction_time").values(
        "sync_id", "transaction_id", "worker_id", "transaction_time", "total_amount", "payment_method")[:BATAS])
    ids = [n["sync_id"] for n in nota]
    isi = defaultdict(list)
    for d in TransactionDetail.objects.filter(sync_id__in=ids).values("sync_id", "item_name", "variant", "quantity"):
        isi[d["sync_id"]].append(d)
    retur = defaultdict(int)
    for e in RefundEvent.objects.filter(sync_id__in=ids).values_list("sync_id", flat=True):
        retur[e] += 1
    pekerja = dict(Worker.objects.values_list("id", "name"))
    for n in nota:
        n["barang"] = isi[n["sync_id"]]
        n["retur"] = retur[n["sync_id"]]
        n["kasir"] = pekerja.get(n["worker_id"], n["worker_id"] or "-")
    return nota


def _uuid(teks):
    import uuid
    try:
        return uuid.UUID(str(teks))
    except (ValueError, TypeError):
        return None


def hapus_nota(pengguna, sync_ids, *, kembalikan_stok=True, alasan=""):
    """Hapus nota kasir terpilih. Hasil: (jumlah nota, jumlah varian yang stoknya dikembalikan)."""
    from pos.models import (MutasiKasir, NotaPersetujuan, NotaTinjau, PaymentProof, RefundEvent,
                            RefundEventDetail, TransactionDetail, Transactions)
    from master.akun.akses import SEMUA_HAK
    from master.models import PenerimaanRetur, PenerimaanReturBaris, VarianInfo
    _wajib(pengguna)
    alasan = rapikan_teks(alasan)[:300]
    if not alasan:
        raise Tolak("Isi alasannya, mis. 'nota uji coba sebelum toko memakai aplikasi'.")
    ids = [u for u in (_uuid(s) for s in sync_ids) if u]
    if not ids:
        raise Tolak("Pilih nota yang mau dihapus.")
    if len(ids) > BATAS:
        raise Tolak(f"Paling banyak {BATAS} nota sekali hapus.")
    with transaction.atomic():
        nota = list(Transactions.objects.select_for_update().filter(sync_id__in=ids).values(
            "sync_id", "transaction_id", "worker_id", "transaction_time", "total_amount", "payment_method"))
        if not nota:
            raise Tolak("Nota tidak ditemukan (mungkin sudah dihapus).")
        ada = [n["sync_id"] for n in nota]
        terjual = defaultdict(int)
        rincian = defaultdict(list)
        for d in TransactionDetail.objects.filter(sync_id__in=ada).values("sync_id", "item_name", "variant", "quantity", "subtotal"):
            terjual[(d["item_name"], d["variant"])] += d["quantity"]
            rincian[d["sync_id"]].append(f"{d['item_name']} / {d['variant']} x{d['quantity']} = {d['subtotal']}")
        refund_ids = list(RefundEvent.objects.filter(sync_id__in=ada).values_list("refund_id", flat=True))
        for b in PenerimaanReturBaris.objects.filter(penerimaan__sync_id__in=ada):
            terjual[(b.item_name, b.variant)] -= b.masuk          # yang sudah kembali ke stok lewat retur
        dikembalikan = 0
        if kembalikan_stok:
            info = {(v.item_name, v.variant): v for v in VarianInfo.objects.filter(
                item_name__in={k[0] for k in terjual}, sampah__isnull=True)}
            nomor = ", ".join(sorted(n["transaction_id"] or "tanpa nomor" for n in nota))[:200]
            for k, qty in sorted(terjual.items()):
                v = info.get(k)
                if v is None or qty == 0:
                    continue
                layanan.sesuaikan_stok(pengguna, v, cara="tambah" if qty > 0 else "kurang", angka=abs(qty), jenis="koreksi",
                                       catatan=f"Hapus nota uji: {nomor}"[:300], hak=set(SEMUA_HAK))
                dikembalikan += 1
        RefundEventDetail.objects.filter(refund_id__in=refund_ids).delete()
        RefundEvent.objects.filter(refund_id__in=refund_ids).delete()
        PenerimaanRetur.objects.filter(sync_id__in=ada).delete()       # barisnya ikut (CASCADE)
        PaymentProof.objects.filter(sync_id__in=ada).delete()
        MutasiKasir.objects.filter(sync_id__in=ada).delete()
        NotaPersetujuan.objects.filter(sync_id__in=ada).delete()
        NotaTinjau.objects.filter(sync_id__in=ada).delete()
        TransactionDetail.objects.filter(sync_id__in=ada).delete()
        Transactions.objects.filter(sync_id__in=ada).delete()
        for n in nota:
            layanan.catat(pengguna, aksi="hapus", objek="nota", kolom="hapus nota uji",
                          lama=json.dumps({"nomor": n["transaction_id"], "sync_id": str(n["sync_id"]), "kasir": n["worker_id"],
                                           "waktu": timezone.localtime(n["transaction_time"]).strftime("%Y-%m-%d %H:%M"),
                                           "total": n["total_amount"], "bayar": n["payment_method"],
                                           "barang": rincian[n["sync_id"]]}, ensure_ascii=False),
                          baru="dihapus" + (" (stok dikembalikan)" if kembalikan_stok else " (stok tidak diubah)"),
                          catatan=alasan)
    return len(nota), dikembalikan


def daftar_karyawan():
    """Karyawan kasir dengan jumlah nota yang masih atas namanya."""
    from django.db.models import Count
    from pos.models import Transactions, Worker
    jumlah = dict(Transactions.objects.values("worker_id").annotate(n=Count("sync_id")).values_list("worker_id", "n"))
    return [{"id": w.id, "nama": w.name, "nota": jumlah.get(w.id, 0)} for w in Worker.objects.order_by("name")]


def hapus_karyawan(pengguna, worker_id, *, alasan=""):
    """Hapus karyawan uji coba. Ditolak kalau masih ada nota atas namanya (hapus notanya dulu)."""
    from django.contrib.auth import get_user_model
    from pos.models import MutasiKasir, NotaTinjau, Transactions, Worker, WorkerPin
    from master.models import AkunKaryawan, KontakKaryawan, PinTampil
    _wajib(pengguna)
    alasan = rapikan_teks(alasan)[:300]
    if not alasan:
        raise Tolak("Isi alasannya, mis. 'karyawan uji coba'.")
    w = Worker.objects.filter(pk=worker_id).first()
    if w is None:
        raise Tolak("Karyawan tidak ditemukan.")
    n = Transactions.objects.filter(worker_id=w.id).count()
    if n:
        raise Tolak(f"{w.name} masih punya {n} nota. Hapus nota ujinya dulu, atau nonaktifkan saja karyawan ini.")
    with transaction.atomic():
        akun = AkunKaryawan.objects.filter(worker_id=w.id).select_related("user").first()
        if akun is not None:
            u = akun.user
            akun.delete()
            if u is not None and not u.is_superuser:
                get_user_model().objects.filter(pk=u.pk).update(is_active=False)   # akun ops dimatikan, jejaknya tetap
        KontakKaryawan.objects.filter(worker_id=w.id).delete()
        PinTampil.objects.filter(worker_id=w.id).delete()
        WorkerPin.objects.filter(worker_id=w.id).delete()
        NotaTinjau.objects.filter(worker_id=w.id).update(worker_id=None)
        MutasiKasir.objects.filter(worker_id=w.id).update(worker_id="")
        nama = w.name
        w.delete()
        layanan.catat(pengguna, aksi="hapus", objek="karyawan", kolom="hapus karyawan uji",
                      lama=json.dumps({"id": worker_id, "nama": nama}, ensure_ascii=False), baru="dihapus", catatan=alasan)
    return nama
