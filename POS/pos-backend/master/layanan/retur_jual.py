"""Penerimaan barang retur dari pembeli (event retur/void yang dicatat kasir).

Kasir hanya mencatat event di `refund_event` / `refund_event_detail` dan sengaja tidak
mengubah stok (POS/pos-backend/REFUND_DESIGN.md: "efek stok milik Inventory"). Di sini
pemilik menerima barangnya dan memilih per baris:

- ditaruh di mana: Toko (rak/display) atau salah satu gudang, dan
- berapa yang rusak (tidak kembali ke stok).

Baru saat diterima stok kasir bertambah (riwayat stok jenis "Retur dari pembeli") dan,
kalau ditaruh di gudang, jumlah gudangnya ikut bertambah.

- `return`: barang yang kembali = baris di refund_event_detail.
- `void`  : nota dibatalkan utuh, jadi semua baris nota asal dianggap kembali.
- `refund`: uang saja, tidak ada barang. Tidak masuk ke sini.
"""
from django.db import connection, transaction

from pos.models import Item, RefundEvent, RefundEventDetail, TransactionDetail, Transactions, Worker

from master.layanan import inti as layanan
from master.layanan import lokasi as svc_lokasi
from master.layanan.inti import Tolak, baca_angka
from master.models import Lokasi, MutasiStok, PenerimaanRetur, PenerimaanReturBaris, VarianInfo

JENIS_BARANG = ("return", "void")
_ADA_TABEL = None


def tersedia():
    """Tabel retur milik POS ada di database ini? (Database pengembangan lama belum punya.)"""
    global _ADA_TABEL
    if not _ADA_TABEL:
        _ADA_TABEL = "refund_event" in connection.introspection.table_names()
    return _ADA_TABEL
LABEL = {"return": "Retur barang", "void": "Nota dibatalkan (void)", "refund": "Uang kembali"}


def _baris_event(events):
    """{refund_id: [(item_name, variant, qty), ...]} untuk event retur/void."""
    hasil = {e.refund_id: [] for e in events}
    retur = [e.refund_id for e in events if e.event_type == "return"]
    for d in RefundEventDetail.objects.filter(refund_id__in=retur).order_by("item_name", "variant"):
        hasil[d.refund_id].append((d.item_name, d.variant, d.quantity))
    void = {e.sync_id: e.refund_id for e in events if e.event_type == "void"}
    for d in TransactionDetail.objects.filter(sync_id__in=list(void)).order_by("item_name", "variant"):
        hasil[void[d.sync_id]].append((d.item_name, d.variant, d.quantity))
    return hasil


def _lengkapi(events):
    sudah = {p.refund_id: p for p in PenerimaanRetur.objects.filter(
        refund_id__in=[e.refund_id for e in events]).prefetch_related("baris__lokasi")}
    baris = _baris_event(events)
    pekerja = dict(Worker.objects.values_list("id", "name"))
    nota = dict(Transactions.objects.filter(sync_id__in={e.sync_id for e in events})
                .values_list("sync_id", "transaction_id"))
    hasil = []
    for e in events:
        hasil.append({
            "refund_id": e.refund_id, "sync_id": e.sync_id, "jenis": e.event_type, "label": LABEL.get(e.event_type, e.event_type),
            "nota": e.transaction_id or nota.get(e.sync_id) or "(tanpa nomor)", "waktu": e.created_at,
            "alasan": e.reason, "uang": e.refund_amount, "disetujui": pekerja.get(e.approver_worker_id, e.approver_worker_id),
            "baris": [{"item_name": n, "variant": v, "jumlah": q} for n, v, q in baris.get(e.refund_id, [])],
            "penerimaan": sudah.get(e.refund_id),
        })
    return hasil


def menunggu(limit=200):
    """Event retur/void yang barangnya belum diterima di master, terlama dulu."""
    if not tersedia():
        return []
    sudah = PenerimaanRetur.objects.values_list("refund_id", flat=True)
    qs = RefundEvent.objects.filter(event_type__in=JENIS_BARANG).exclude(refund_id__in=sudah).order_by("created_at")
    return _lengkapi(list(qs[:limit]))


def jumlah_menunggu():
    if not tersedia():
        return 0
    sudah = PenerimaanRetur.objects.values_list("refund_id", flat=True)
    return RefundEvent.objects.filter(event_type__in=JENIS_BARANG).exclude(refund_id__in=sudah).count()


def untuk_nota(sync_id):
    """Semua event (retur, void, uang kembali) satu nota, untuk panel nota."""
    if not tersedia():
        return []
    return _lengkapi(list(RefundEvent.objects.filter(sync_id=sync_id).order_by("created_at")))


def status_nota(sync_ids):
    """{sync_id: "void" | "retur" | "menunggu" | "uang"} untuk label di daftar nota."""
    if not tersedia():
        return {}
    events = list(RefundEvent.objects.filter(sync_id__in=list(sync_ids)).values_list("sync_id", "refund_id", "event_type"))
    if not events:
        return {}
    sudah = set(PenerimaanRetur.objects.filter(refund_id__in=[r for _, r, _ in events]).values_list("refund_id", flat=True))
    hasil = {}
    for sid, rid, jenis in events:
        if jenis in JENIS_BARANG and rid not in sudah:
            kini = "menunggu"
        else:
            kini = {"void": "void", "return": "retur", "refund": "uang"}[jenis]
        urutan = ["uang", "retur", "void", "menunggu"]
        if sid not in hasil or urutan.index(kini) > urutan.index(hasil[sid]):
            hasil[sid] = kini
    return hasil


def terima(pengguna, refund_id, data, *, hak):
    """Terima barang satu event. data: lokasi_<i> dan rusak_<i> per baris (i = urutan baris)."""
    if "ubah_stok" not in hak:
        raise Tolak("Akun ini tidak boleh mengubah stok.")
    with transaction.atomic():
        event = RefundEvent.objects.filter(refund_id=refund_id).first()
        if event is None or event.event_type not in JENIS_BARANG:
            raise Tolak("Event retur tidak ditemukan.")
        if PenerimaanRetur.objects.select_for_update().filter(refund_id=refund_id).exists():
            raise Tolak("Retur ini sudah diterima sebelumnya.")
        daftar = _baris_event([event])[event.refund_id]
        if not daftar:
            raise Tolak("Event ini tidak punya baris barang.")
        toko = svc_lokasi.lokasi_toko()
        rencana = []
        for i, (nama, varian, qty) in enumerate(daftar):
            lokasi = svc_lokasi.baca(data.get(f"lokasi_{i}"))
            if lokasi.jenis == "gudang" and "kelola_lokasi" not in hak:
                raise Tolak("Akun ini tidak boleh menaruh barang di gudang.")
            rusak = baca_angka(data.get(f"rusak_{i}"), boleh_kosong=True) or 0
            if not 0 <= rusak <= qty:
                raise Tolak(f"{nama} / {varian}: jumlah rusak harus 0 sampai {qty}.")
            rencana.append((nama, varian, qty, rusak, lokasi))
        p = PenerimaanRetur.objects.create(
            refund_id=event.refund_id, sync_id=event.sync_id, jenis=event.event_type,
            pengguna=pengguna if getattr(pengguna, "pk", None) else None,
            nama_pengguna=getattr(pengguna, "username", "") or "sistem",
            catatan=layanan.rapikan_teks(data.get("catatan"))[:300])
        ref = f"{LABEL[event.event_type]} nota {event.transaction_id or event.sync_id}"
        for nama, varian, qty, rusak, lokasi in rencana:
            info = VarianInfo.objects.filter(item_name=nama, variant=varian).first()
            masuk = qty - rusak
            if masuk:
                baris = Item.objects.select_for_update().filter(item_name=nama, variant=varian)
                sebelum = baris.values_list("in_stock", flat=True).first()
                if sebelum is None and not baris.exists():
                    raise Tolak(f"{nama} / {varian} tidak ada di daftar barang (mungkin di Barang dihapus). "
                                "Pulihkan dulu, atau tandai semuanya rusak.")
                sebelum = sebelum or 0
                baris.update(in_stock=sebelum + masuk)
                MutasiStok.objects.create(
                    pengguna=p.pengguna, nama_pengguna=p.nama_pengguna, varian=info, item_name=nama, variant=varian,
                    jenis="retur_jual", jumlah=masuk, stok_sebelum=sebelum, stok_sesudah=sebelum + masuk,
                    catatan=ref + (f", {rusak} rusak" if rusak else ""),
                    lokasi=lokasi if lokasi.jenis == "gudang" else None)
                if info is not None:
                    svc_lokasi.ubah_gudang(info, lokasi, masuk)
            PenerimaanReturBaris.objects.create(penerimaan=p, varian=info, item_name=nama, variant=varian, jumlah=qty,
                                                masuk=masuk, rusak=rusak, lokasi=lokasi if masuk else None)
        layanan.catat(pengguna, aksi="tambah", objek="retur_jual", kolom="retur_jual",
                      baru=f"{ref}: {sum(q - r for _, _, q, r, _ in rencana)} masuk stok, "
                           f"{sum(r for _, _, _, r, _ in rencana)} rusak", catatan=p.catatan)
    return p


def pilihan_lokasi():
    return [svc_lokasi.lokasi_toko()] + list(Lokasi.objects.filter(jenis="gudang", aktif=True))
