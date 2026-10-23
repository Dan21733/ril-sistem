from django.db import IntegrityError, connection, transaction

from . import pengaturan
from .models import (
    RefundEvent,
    RefundEventDetail,
    TransactionDetail,
    Transactions,
)


class RefundValidationError(ValueError):
    """Client-fixable refund rejection that maps to HTTP 400, not 500."""


class RefundConflict(ValueError):
    """Refund yang bertentangan dengan keadaan nota (HTTP 409).

    Dipakai bila nota sudah void, sudah punya retur/refund sehingga tidak
    boleh di-void, jatah barang/uang habis, atau refund_id dipakai ulang
    dengan isi berbeda. Mengikuti konvensi domain-exception aplikasi ini
    (subclass ValueError).
    """


class RefundNotFound(ValueError):
    """Nota asal tidak ada atau tidak terlihat pemanggil (HTTP 404)."""


class RefundForbidden(PermissionError):
    """Approver bukan kasir nota asal (HTTP 403).

    Keputusan pemilik: void/return/refund hanya boleh dicatat oleh kasir
    yang membuat nota asal (PIN-nya sendiri), plus persetujuan master yang
    sifatnya prosedural di luar POS. Mengikuti konvensi domain-exception
    aplikasi ini (subclass exception bawaan yang deskriptif).
    """


VOID = "void"
RETURN = "return"
REFUND = "refund"

EVENT_TYPES = (VOID, RETURN, REFUND)

# Definisi istilah yang dipakai di sini (ARCHITECTURE.md, dikunci bisnis):
#   void   = batalkan nota (salah input / pelanggan batal). Tidak ada barang
#            kembali; uang yang sudah dibayar boleh dicatat keluar di sini.
#   return = barang fisik kembali. Uang kembali opsional (boleh 0 untuk
#            tukar barang / belum dibayarkan saat itu).
#   refund = uang keluar tanpa barang kembali (mis. ongkir yang tidak jadi
#            dikirim, atau pelunasan uang untuk barang yang sudah diretur
#            pada event sebelumnya).
# Exchange tidak punya tipe sendiri: retur/tukar = event `return` (uang boleh
# 0) lalu penjualan pengganti dicatat sebagai nota baru yang normal.


def _sold_items(sync_id):
    """Peta (item_name, variant) -> fakta bayar baris nota asal.

    `subtotal` adalah yang **benar-benar dibayar** pelanggan untuk baris itu
    (sudah dikurangi diskon), jadi ia plafon keras untuk semua yang boleh
    kembali dari baris tersebut.
    """
    sold = {}

    for row in TransactionDetail.objects.filter(sync_id=sync_id):
        sold[(row.item_name, row.variant)] = {
            "quantity": row.quantity,
            "unit_price": row.unit_price,
            "subtotal": row.subtotal,
        }

    return sold


def _prior_usage(sync_id):
    """Pemakaian kumulatif event sebelumnya.

    Kembalikan `(voided, returned, returned_value, refunded_amount)`:
    `returned` = peta (item_name, variant) -> {quantity, value} yang sudah
    kembali; `returned_value` = total nilai barang yang sudah kembali; dan
    `refunded_amount` = total uang yang sudah keluar (termasuk void).
    """
    returned = {}
    refunded_amount = 0
    voided = False

    events = RefundEvent.objects.filter(sync_id=sync_id).only(
        "refund_id", "event_type", "refund_amount",
    )

    for event in events:
        refunded_amount += event.refund_amount

        if event.event_type == VOID:
            voided = True
            continue

        for detail in RefundEventDetail.objects.filter(
            refund_id=event.refund_id,
        ).only("item_name", "variant", "quantity", "subtotal"):
            key = (detail.item_name, detail.variant)
            bucket = returned.setdefault(key, {"quantity": 0, "value": 0})
            bucket["quantity"] += detail.quantity
            bucket["value"] += detail.subtotal

    returned_value = sum(bucket["value"] for bucket in returned.values())

    return voided, returned, returned_value, refunded_amount


def _check_item_lines(items, sold, returned):
    """Validasi baris barang lawan nota asal + jatah kumulatif.

    Kembalikan nilai barang yang kembali di event ini. Plafon per baris
    memakai `subtotal` nota asal, bukan sekadar `unit_price × quantity`,
    supaya diskon baris tidak bisa diklaim dua kali (barang berdiskon
    diretur lalu uangnya diminta sebesar harga sebelum diskon).
    """
    seen = set()
    event_value = 0

    for item in items:
        key = (item["item_name"], item["variant"])

        if key in seen:
            raise RefundValidationError(
                "Barang yang sama tidak boleh muncul dua kali dalam satu "
                "event."
            )

        seen.add(key)

        original = sold.get(key)

        if original is None:
            raise RefundValidationError(
                f"Barang {item['item_name']} - {item['variant']} "
                "tidak ada di nota asal."
            )

        quantity = item["quantity"]
        unit_price = item["unit_price"]

        if quantity <= 0:
            raise RefundValidationError(
                "Jumlah barang yang diretur harus lebih dari 0."
            )

        if unit_price < 0 or unit_price > original["unit_price"]:
            raise RefundValidationError(
                f"Harga retur {item['item_name']} melebihi harga "
                "yang dibayar pelanggan."
            )

        if item["subtotal"] != unit_price * quantity:
            raise RefundValidationError(
                "Subtotal barang tidak sama dengan jumlah yang dihitung."
            )

        used = returned.get(key) or {"quantity": 0, "value": 0}

        remaining = original["quantity"] - used["quantity"]

        if quantity > remaining:
            raise RefundValidationError(
                f"Sisa {item['item_name']} yang boleh diretur hanya "
                f"{remaining}."
            )

        if used["value"] + item["subtotal"] > original["subtotal"]:
            raise RefundValidationError(
                f"Nilai retur {item['item_name']} melebihi yang "
                "benar-benar dibayar pelanggan untuk baris itu."
            )

        event_value += item["subtotal"]

    return event_value


def _locked_sale(sync_id):
    """Ambil nota sambil mengunci barisnya bila backend mendukung.

    Lock ini menutup balapan dua permintaan bersamaan yang sama-sama lolos
    cek jatah lalu menulis event ganda (uang/barang keluar dua kali). SQLite
    tidak mendukung SELECT ... FOR UPDATE; di sana locknya dilewati dan
    proteksinya bergantung pada serialisasi tulis SQLite.
    """
    queryset = Transactions.objects

    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()

    return queryset.filter(sync_id=sync_id).first()


def _event_items(refund_id):
    """Baris barang satu event, terurut — dipakai pembanding idempotency."""
    return sorted(
        (
            detail.item_name,
            detail.variant,
            detail.quantity,
            detail.unit_price,
            detail.subtotal,
        )
        for detail in RefundEventDetail.objects.filter(refund_id=refund_id)
    )


def _payload_items(items):
    """Tanda tangan isi barang dari payload klien, terurut."""
    return sorted(
        (
            item["item_name"],
            item["variant"],
            item["quantity"],
            item["unit_price"],
            item["subtotal"],
        )
        for item in items
    )


def _same_event(existing, validated_data):
    """Apakah event yang sudah ada isinya identik dengan payload retry ini?

    Idempotency harus utuh: `refund_id` yang sama dengan isi berbeda adalah
    konflik, bukan retry. Kalau tidak, retry yang mengubah jumlah uang atau
    barang akan dilaporkan "berhasil" padahal yang tersimpan yang lama.
    """
    if str(existing.sync_id) != str(validated_data["sync_id"]):
        return False

    if existing.event_type != validated_data["event_type"]:
        return False

    if existing.refund_amount != validated_data["refund_amount"]:
        return False

    if (existing.reason or "") != validated_data["reason"]:
        return False

    if (existing.refund_method or "cash") != (
        validated_data.get("refund_method") or "cash"
    ):
        return False

    return _event_items(existing.refund_id) == _payload_items(
        validated_data.get("items") or []
    )


def persist_refund(validated_data, worker_id, device_code):
    """Catat satu event void/return/refund. Kembalikan (event, created).

    Aturan lengkapnya (aturan final 2026-10-06, plafon uang direvisi pemilik
    2026-10-17; lihat REFUND_DESIGN.md §3/§6):

    - Nota asal tidak pernah diubah; setiap aksi adalah baris event baru.
    - `worker_id` = worker di balik PIN approver. Approver **wajib kasir nota
      asal** (keputusan pemilik: retur hanya transaksi sendiri + ACC master
      prosedural di luar POS). Kasir lain, walau PIN-nya sah, ditolak 403 —
      termasuk saat kasir asal sudah pulang (butuh peran master di sistem).
    - Nota yang sudah void bersifat terminal: tidak ada event lanjutan.
    - Void hanya untuk nota yang masih bersih (belum ada retur/refund).
    - Return wajib minimal satu baris barang; jumlah uangnya opsional.
    - Refund (uang keluar) tidak membawa baris barang; barang kembali = return.
    - Uang kumulatif yang keluar (void + retur + refund) tidak boleh melebihi
      `total_amount` nota — yaitu yang benar-benar dibayar pelanggan. Plafon
      per baris barang tetap `transaction_detail.subtotal` supaya diskon baris
      tidak bisa diklaim dua kali (keputusan pemilik 2026-10-17).
    - `refund_id` ganda dengan isi sama = idempotent (created=False); isi beda
      = konflik. Pengecekan dilakukan sebelum validasi jatah supaya retry yang
      datang setelah nota berubah tetap dibalas idempotent, bukan galat baru.
    """
    sync_id = validated_data["sync_id"]
    event_type = validated_data["event_type"]
    items = list(validated_data.get("items") or [])

    if event_type not in EVENT_TYPES:
        raise RefundValidationError("Jenis event refund tidak dikenal.")

    with transaction.atomic():
        sale = _locked_sale(sync_id)

        if sale is None:
            raise RefundNotFound("Nota asal tidak ditemukan.")

        # Otorisasi lebih dulu daripada idempotency: replay payload identik
        # oleh kasir lain tetap ditolak, bukan dibalas idempotent.
        if str(sale.worker_id) != str(worker_id):
            raise RefundForbidden(
                "Hanya kasir nota asal yang boleh mencatat void, retur, "
                "atau refund."
            )

        # Idempotency diperiksa lebih dulu daripada aturan bentuk payload:
        # retry yang sah tidak boleh berubah menjadi galat baru hanya karena
        # nota sudah bergerak sejak permintaan pertama.
        existing = RefundEvent.objects.filter(
            refund_id=validated_data["refund_id"],
        ).first()

        if existing is not None:
            if _same_event(existing, validated_data):
                return existing, False

            raise RefundConflict(
                "Nomor refund ini sudah dipakai dengan isi berbeda."
            )

        if event_type == VOID and items:
            raise RefundValidationError(
                "Void tidak membawa baris barang; pakai retur untuk barang "
                "yang kembali."
            )

        if event_type == RETURN and not items:
            raise RefundValidationError(
                "Retur wajib memuat minimal satu barang."
            )

        if event_type == REFUND and items:
            raise RefundValidationError(
                "Refund hanya berisi uang; catat barang yang kembali "
                "sebagai retur."
            )

        if event_type == REFUND and validated_data["refund_amount"] <= 0:
            raise RefundValidationError(
                "Refund harus mengembalikan uang lebih dari 0."
            )

        voided, returned, returned_value, refunded_amount = _prior_usage(
            sync_id
        )

        if voided:
            raise RefundConflict(
                "Nota ini sudah void; tidak ada event refund lanjutan "
                "yang diterima."
            )

        if event_type == VOID and (returned_value > 0 or refunded_amount > 0):
            raise RefundConflict(
                "Nota yang sudah punya retur atau refund tidak bisa "
                "di-void; selesaikan lewat jalur refund."
            )

        # Validasi jatah & nilai per baris barang. Nilai yang kembali tidak
        # lagi menjadi plafon uang; plafon uang adalah total nota.
        _check_item_lines(items, _sold_items(sync_id), returned)

        # Plafon uang kumulatif = total nota. Keputusan pemilik 2026-10-17
        # menggantikan plafon "nilai barang kembali + ongkir": pelanggan
        # berhak menerima kembali uang yang benar-benar dibayarnya, mis.
        # pembatalan setelah bayar atau ongkir yang tidak jadi dikirim, tanpa
        # harus mencatat barang kembali lebih dulu. Nilai retur per baris
        # tetap dibatasi `transaction_detail.subtotal` di atas, dan nilai
        # kumulatif semua event tetap tidak boleh melewati total nota.
        #
        # Pemilik bisa menurunkan plafon ini dari master (Aturan kasir,
        # `refund_maks_persen`, bawaan 100%): mis. 90% berarti uang yang
        # boleh keluar paling banyak 90% dari total nota.
        persen = min(100, max(0, pengaturan.angka("refund_maks_persen", 100)))
        money_cap = sale.total_amount * persen // 100

        if refunded_amount + validated_data["refund_amount"] > money_cap:
            if persen < 100:
                raise RefundValidationError(
                    f"Uang kembali melebihi batas {persen}% dari total nota "
                    f"(paling banyak Rp{money_cap:,}).".replace(",", ".")
                )
            raise RefundValidationError(
                "Uang kembali melebihi total nota."
            )

        try:
            with transaction.atomic():
                event = RefundEvent.objects.create(
                    refund_id=validated_data["refund_id"],
                    sync_id=sync_id,
                    transaction_id=sale.transaction_id,
                    event_type=event_type,
                    reason=validated_data["reason"],
                    refund_method=validated_data.get("refund_method") or "cash",
                    refund_amount=validated_data["refund_amount"],
                    approver_worker_id=worker_id,
                    device_code=device_code,
                )

                for item in items:
                    RefundEventDetail.objects.create(
                        refund_id=event.refund_id,
                        item_name=item["item_name"],
                        variant=item["variant"],
                        quantity=item["quantity"],
                        unit_price=item["unit_price"],
                        subtotal=item["subtotal"],
                    )
        except IntegrityError:
            # Savepoint sudah di-rollback; transaksi luar masih sehat, jadi
            # baris yang menang balapan boleh dibaca di sini.
            existing = RefundEvent.objects.filter(
                refund_id=validated_data["refund_id"],
            ).first()

            if existing is None:
                raise

            if _same_event(existing, validated_data):
                return existing, False

            raise RefundConflict(
                "Nomor refund ini sudah dipakai dengan isi berbeda."
            )

    return event, True


def refund_status_map(sync_ids):
    """Petakan sync_id -> 'none'|'void'|'returned'|'refunded' untuk riwayat.

    Satu status agregat per nota, prioritas: void menang atas refund, refund
    menang atas return (uang sudah keluar = langkah paling lanjut).
    """
    statuses = {str(sync_id): "none" for sync_id in sync_ids}

    if not sync_ids:
        return statuses

    for event in RefundEvent.objects.filter(
        sync_id__in=list(sync_ids),
    ).only("sync_id", "event_type"):
        key = str(event.sync_id)
        current = statuses.get(key, "none")

        if event.event_type == VOID:
            statuses[key] = "void"
        elif event.event_type == REFUND and current != "void":
            statuses[key] = "refunded"
        elif event.event_type == RETURN and current == "none":
            statuses[key] = "returned"

    return statuses


def device_code_from_transaction_id(transaction_id):
    """Kode device dari prefix nomor nota; None bila nota tanpa nomor."""
    text = (transaction_id or "").strip()

    if "-" not in text:
        return None

    prefix = text.split("-", 1)[0].strip()

    return prefix or None
