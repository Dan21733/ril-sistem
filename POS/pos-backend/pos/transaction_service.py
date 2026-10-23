import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Item, MutasiKasir, TransactionDetail, Transactions
from .transaction_id import build_transaction_id
from .transaction_sync import (
    TransactionSyncConflict,
    TransactionSyncStatus,
    resolve_transaction_request,
)


# Jam nota boleh sedikit di depan jam server (jam tablet sedikit maju), tapi
# bukan berjam-jam: nota bertanggal masa depan masuk ke laporan hari lain.
# Lebih dari batas ini ditolak, atau ditahan untuk ACC kalau nota sudah final
# di tablet (QA 18 Okt 2026).
BATAS_JAM_MAJU = datetime.timedelta(minutes=10)


class TransactionValidationError(ValueError):
    """Domain rejection that maps to HTTP 400, not 500.

    Raised for client-fixable sale rejections: unknown item, duplicate
    item in one transaction, or stock rule violations. The stock rule
    (sell allowed only when current stock > 0; quantity may exceed stock
    and push it negative) is an intentional business decision.

    Subclasses ValueError to follow the existing domain-exception
    convention in this app (worker_verification, transaction_id).
    """


def _create_transaction_header(transaction_data, worker_id):
    return Transactions.objects.create(
        sync_id=transaction_data["sync_id"],
        transaction_id=transaction_data.get("transaction_id"),
        worker_id=worker_id,
        transaction_time=transaction_data["transaction_time"],
        item_amount=transaction_data["item_amount"],
        shipping_amount=transaction_data.get("shipping_amount"),
        total_amount=transaction_data["total_amount"],
        payment_method=transaction_data["payment_method"],
        payment_amount=transaction_data["payment_amount"],
        change_amount=transaction_data["change_amount"],
        cust_name=transaction_data.get("cust_name"),
        phone_no=transaction_data.get("phone_no"),
    )


def _create_transaction_details(sync_id, items):
    details = []
    for item in items:
        details.append(
            TransactionDetail.objects.create(
                sync_id=sync_id,
                item_name=item["item_name"],
                variant=item["variant"],
                discount=item.get("discount", 0),
                quantity=item["quantity"],
                subtotal=(item["unit_price"] * item["quantity"])
                - item.get("discount", 0),
                unit_price=item["unit_price"],
            )
        )
    return details

def _validate_item_price(item_data, item, lewati_floor=False):
    """Negotiated price must stay between floor and ceiling.

    This is a POS pricing rule, not part of the inventory seam: it must
    survive unchanged when the Inventory contract replaces the stock
    handling in _process_inventory. Cashiers may negotiate freely above
    the floor; anything below the floor is rejected until the approval
    design exists (approved below-floor selling must record the approver,
    which the current schema cannot store).

    A null master price skips its check because the externally managed
    item table is not guaranteed to be complete.
    """
    unit_price = item_data["unit_price"]

    # Toko tidak memakai diskon per baris: harga dinegosiasikan langsung di
    # unit_price (antara floor dan pricelist), dan layar kasir selalu mengirim
    # diskon 0. Diskon > 0 dari klien lain akan menembus cek floor di bawah,
    # jadi ditolak (QA 18 Okt 2026).
    if item_data.get("discount", 0):
        raise TransactionValidationError(
            "Diskon per barang tidak dipakai. Ubah harga barangnya saja."
        )

    # lewati_floor: harga di bawah floor sudah disetujui PIN penyetuju
    # (pengaturan kasir `penyetuju_floor`). Batas atas pricelist tetap dicek.
    if not lewati_floor and item.min_price is not None and unit_price < item.min_price:
        raise TransactionValidationError(
            "Harga barang di bawah harga floor."
        )

    # max_price 0 = pricelist belum diisi, bukan harga jual Rp 0: batas
    # atasnya min_price, sama seperti katalog di perangkat
    # (pos-frontend/src/utils/priceRange.js resolveItemPrices). Tanpa ini
    # setiap penjualan item seperti itu (mis. "Aries Pintu - Abu",
    # min 245.000 / max 0) ditolak "above the ceiling price".
    ceiling = item.min_price if item.max_price == 0 else item.max_price

    if ceiling is not None and unit_price > ceiling:
        raise TransactionValidationError(
            "Harga barang di atas harga pricelist."
        )


def _process_inventory(items, lewati_harga=False, lewati_floor=False):
    """Inventory seam: lock authoritative stock rows and deduct the sale.

    Aturan stok (keputusan pemilik, dikunci; diterapkan 17 Okt 2026): stok
    boleh minus. Penjualan tidak pernah ditolak karena stok 0 atau kurang;
    stok minus ditandai di master (stok Toko minus / selisih), bukan diblokir
    di kasir. Dulu stok <= 0 ditolak, yang membuat nota offline final bisa
    tertolak saat sync.

    `lewati_harga=True` hanya dipakai saat pemilik meng-ACC nota dari master
    (Nota perlu ACC): harga di nota sudah terjadi di toko dan sudah diperiksa
    pemilik, jadi tidak dicek ulang terhadap floor/pricelist yang sekarang.
    """
    items_by_key = {}

    for item_data in items:
        item_key = (
            item_data["item_name"],
            item_data["variant"],
        )

        if item_key in items_by_key:
            raise TransactionValidationError(
                "Barang yang sama muncul dua kali di nota."
            )

        items_by_key[item_key] = item_data

    locked_items = {}

    for item_key in sorted(items_by_key):
        item_name, variant = item_key

        try:
            item = (
                Item.objects
                .select_for_update()
                .get(
                    item_name=item_name,
                    variant=variant,
                )
            )
        except Item.DoesNotExist:
            raise TransactionValidationError(
                "Barang tidak ada di master."
            )

        if not lewati_harga:
            _validate_item_price(items_by_key[item_key], item, lewati_floor=lewati_floor)

        locked_items[item_key] = item

    perubahan = []

    for item_key, item_data in items_by_key.items():
        item = locked_items[item_key]
        sebelum = item.in_stock or 0
        item.in_stock = sebelum - item_data["quantity"]

        item.save(update_fields=["in_stock"])
        perubahan.append((item_key, sebelum, item.in_stock, item_data["quantity"]))

    return perubahan


def _catat_mutasi(perubahan, transaction_data, worker_id):
    """Riwayat stok penjualan kasir (tabel mutasi_kasir), ditampilkan master."""
    MutasiKasir.objects.bulk_create([
        MutasiKasir(
            sync_id=transaction_data["sync_id"],
            transaction_id=transaction_data.get("transaction_id"),
            worker_id=str(worker_id or ""),
            item_name=item_name,
            variant=variant,
            jumlah=-jumlah,
            stok_sebelum=sebelum,
            stok_sesudah=sesudah,
        )
        for (item_name, variant), sebelum, sesudah, jumlah in perubahan
    ])

def _validate_transaction_id_device(transaction_id, device_code):
    """Nota milik device lain tidak boleh dikirim dari device ini.

    Celah dari ERD: prefix nomor nota datang dari klien, jadi tanpa cek ini
    device mana pun bisa mengirim nomor ber-prefix T02- dan memalsukan
    kepemilikan nota. Format sudah divalidasi serializer (T##-######), jadi
    prefix-nya selalu bisa diparse. Nota tanpa nomor (offline, keputusan
    KEPUTUSAN-1) lewati cek ini dan mengikuti penomoran device saat sync.
    """
    if not transaction_id or not device_code:
        return

    expected_prefix = build_transaction_id(device_code, 1)[:3]

    if transaction_id[:3] != expected_prefix:
        raise TransactionValidationError(
            "Nomor nota bukan milik perangkat ini."
        )


def _validate_idempotent_sale(existing, transaction_data, worker_id):
    """IDs identify a sale, not permission to reuse them for different facts."""
    header_fields = (
        "transaction_time",
        "item_amount",
        "total_amount",
        "payment_method",
        "payment_amount",
        "change_amount",
        "cust_name",
        "phone_no",
    )
    if (
        existing.worker_id != worker_id
        or any(
            (getattr(existing, field) or None)
            != (transaction_data.get(field) or None)
            for field in header_fields
        )
        or (existing.shipping_amount or 0)
        != (transaction_data.get("shipping_amount") or 0)
    ):
        raise TransactionSyncConflict(
            "Nota tersimpan berbeda pada fakta jual atau kasir. "
            "Perlu pemeriksaan manual."
        )

    item_fields = (
        "item_name", "variant", "quantity", "unit_price", "discount", "subtotal",
    )
    stored_items = list(
        TransactionDetail.objects.filter(sync_id=existing.sync_id).values(*item_fields)
    )

    def rows(items):
        return sorted(
            tuple(
                item.get(field, 0) if field == "discount" else item[field]
                for field in item_fields
            )
            for item in items
        )

    if rows(stored_items) != rows(transaction_data["items"]):
        raise TransactionSyncConflict(
            "Nota tersimpan berbeda pada baris barang. "
            "Perlu pemeriksaan manual."
        )


def persist_transaction(transaction_data, worker_id, device_code=None, lewati_harga=False,
                        lewati_floor=False):
    """Persist a validated transaction and its details atomically.

    The database primary/unique constraints remain the final protection
    against concurrent duplicate sync_id or transaction_id submissions.
    """
    sync_id = transaction_data["sync_id"]
    transaction_id = transaction_data.get("transaction_id")

    _validate_transaction_id_device(transaction_id, device_code)

    with transaction.atomic():
        status, existing = resolve_transaction_request(
            sync_id,
            transaction_id,
        )

        if status is TransactionSyncStatus.IDEMPOTENT:
            _validate_idempotent_sale(existing, transaction_data, worker_id)
            return status, existing

        if status is TransactionSyncStatus.CONFLICT:
            raise TransactionSyncConflict(
                "Identitas nota bentrok."
            )

        waktu = transaction_data.get("transaction_time")
        if not lewati_harga and waktu is not None and waktu > timezone.now() + BATAS_JAM_MAJU:
            menit = int((waktu - timezone.now()).total_seconds() // 60)
            raise TransactionValidationError(
                f"Jam nota {menit} menit di depan jam server. Periksa jam tablet."
            )

        perubahan = _process_inventory(transaction_data["items"], lewati_harga=lewati_harga,
                                       lewati_floor=lewati_floor)

        try:
            with transaction.atomic():
                created_transaction = _create_transaction_header(
                    transaction_data,
                    worker_id,
                )
        except IntegrityError:
            # A concurrent request may have inserted the same identity
            # between the initial lookup and the INSERT. Re-resolve using
            # the committed row after the savepoint rolls back.
            #
            # set_rollback(True) marks the enclosing atomic block for
            # rollback: this request already ran _process_inventory and
            # decremented stock rows. Returning idempotently without
            # rolling back would COMMIT that second deduction, so one
            # sale would reduce stock twice. With rollback marked, the
            # response is sent while every write of this request
            # (including the stock deduction) is discarded.
            status, existing = resolve_transaction_request(
                sync_id,
                transaction_id,
            )
            if status is TransactionSyncStatus.IDEMPOTENT:
                # Compare committed details before marking rollback: Django
                # forbids queries inside an atomic block marked for rollback.
                _validate_idempotent_sale(existing, transaction_data, worker_id)
                transaction.set_rollback(True)
                return status, existing
            if status is TransactionSyncStatus.CONFLICT:
                raise TransactionSyncConflict(
                    "Identitas nota bentrok."
                )
            raise

        _create_transaction_details(
            created_transaction.sync_id,
            transaction_data["items"],
        )
        _catat_mutasi(perubahan, transaction_data, worker_id)

        return TransactionSyncStatus.NEW, created_transaction
