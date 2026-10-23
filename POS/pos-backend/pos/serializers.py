import re

from django.core.validators import RegexValidator
from rest_framework import serializers

from .models import Item
from .transaction_id import TRANSACTION_ID_PATTERN


# Nama pelanggan: teks bebas sampai 120 karakter. No. HP opsional: digit
# dengan awalan + opsional, boleh diselingi spasi/tanda hubung/kurung —
# pola yang sama ditegakkan UI checkout (CartList.vue) supaya pesan kasir
# konsisten dengan penolakan server.
CUSTOMER_NAME_MAX_LENGTH = 120
PHONE_NO_MAX_LENGTH = 20
PHONE_NO_PATTERN = re.compile(r"^\+?[0-9][0-9\s()\-]*$")


class ItemSerializer(serializers.Serializer):
    item_name = serializers.CharField()
    variant = serializers.CharField()

    # price = pricelist/ceiling; floor_price = batas bawah nego. Harga floor
    # boleh diketahui kasir ("paling bawah hanya boleh tahu harga floor")
    # supaya nego di atas floor bisa diputuskan tanpa bertanya pemilik.
    # Yang tetap TIDAK boleh dikirim: cost_amount/modal, margin, nilai stok,
    # dan data supplier.
    price = serializers.IntegerField(source="max_price", allow_null=True)
    floor_price = serializers.IntegerField(source="min_price", allow_null=True)

    stock = serializers.IntegerField(source="in_stock", allow_null=True)
    # Kode rak dari master (letak barang), null kalau belum diisi.
    rak = serializers.SerializerMethodField()
    # Alias pencarian (tabel item_alias, satu teks per item_name) supaya tablet
    # bisa mencari lewat alias saat offline (QA 18 Okt 2026). Kosong = tidak ada.
    alias = serializers.SerializerMethodField()

    def get_rak(self, obj):
        return getattr(obj, "rak_kode", None)

    def get_alias(self, obj):
        return (self.context.get("alias") or {}).get(obj.item_name, "")
    stock_alert = serializers.IntegerField(allow_null=True)


class TransactionHistoryItemSerializer(serializers.Serializer):
    """Baris barang nota untuk tampilan riwayat.

    Hanya fakta jual-beli historis; data terlarang (modal, margin, nilai
    stok, supplier) sengaja tidak punya jalur ke response ini.
    """

    item_name = serializers.CharField()
    variant = serializers.CharField()
    quantity = serializers.IntegerField()
    unit_price = serializers.IntegerField()
    discount = serializers.IntegerField()
    subtotal = serializers.IntegerField()


class TransactionHistorySerializer(serializers.Serializer):
    """Header nota untuk riwayat per device.

    worker_name dibaca dari relasi worker sebagai pelaku tercatat; field
    `pin` worker tidak pernah dibaca serializer ini.
    """

    sync_id = serializers.UUIDField()
    transaction_id = serializers.CharField(allow_null=True)
    transaction_time = serializers.DateTimeField()
    cust_name = serializers.CharField(allow_null=True, required=False)
    phone_no = serializers.CharField(allow_null=True, required=False)
    item_amount = serializers.IntegerField()
    shipping_amount = serializers.IntegerField(allow_null=True)
    total_amount = serializers.IntegerField()
    payment_method = serializers.CharField()
    payment_amount = serializers.IntegerField()
    change_amount = serializers.IntegerField()
    cust_name = serializers.CharField(allow_null=True, required=False)
    phone_no = serializers.CharField(allow_null=True, required=False)
    worker_name = serializers.CharField(source="worker.name")
    # Diisi view, bukan model: kode device turunan prefix nomor nota
    # (transactions tidak punya kolom device) + status refund agregat.
    device_code = serializers.CharField(allow_null=True, required=False)
    refund_status = serializers.CharField(required=False)


class RefundItemSerializer(serializers.Serializer):
    item_name = serializers.CharField()
    variant = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.IntegerField(min_value=0)
    subtotal = serializers.IntegerField(min_value=0)


class RefundCreateSerializer(serializers.Serializer):
    """Envelope satu event void/return/refund (REFUND_DESIGN.md).

    `refund_id` dibuat klien agar retry idempotent seperti `proof_id`.
    `approver_pin` write-only: PIN kasir yang memfinalisasi nota asal,
    diverifikasi server lalu dibuang — tidak pernah disimpan atau dibalas.
    Aturan lawan nota asal (jatah barang/uang, kepemilikan kasir) ditegakkan
    `refund_service.persist_refund`, bukan di sini.
    """

    refund_id = serializers.UUIDField()
    sync_id = serializers.UUIDField()
    approver_pin = serializers.CharField(
        write_only=True,
        min_length=6,
        max_length=6,
    )

    event_type = serializers.ChoiceField(
        choices=["void", "return", "refund"],
    )

    reason = serializers.CharField(min_length=3)

    refund_method = serializers.ChoiceField(
        choices=["cash", "edc", "bank_transfer"],
        required=False,
        default="cash",
    )

    refund_amount = serializers.IntegerField(min_value=0)

    items = RefundItemSerializer(
        many=True,
        required=False,
        default=list,
    )


class PaymentProofUploadSerializer(serializers.Serializer):
    """Envelope unggah bukti transfer.

    `proof_id` dibuat klien supaya pengiriman ulang selalu idempotent: retry
    tidak pernah menghasilkan bukti ganda. `captured_at` opsional — waktu
    foto diambil diketahui device, bukan server.
    """

    proof_id = serializers.UUIDField()
    sync_id = serializers.UUIDField()
    captured_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )
    file = serializers.FileField()


class TransactionItemSerializer(serializers.Serializer):
    item_name = serializers.CharField()
    variant = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.IntegerField(min_value=0)
    discount = serializers.IntegerField(
        min_value=0,
        required=False,
        default=0
    )
    subtotal = serializers.IntegerField(min_value=0)


class TransactionCreateSerializer(serializers.Serializer):
    sync_id = serializers.UUIDField()

    # null diizinkan: nota offline yang kehabisan jatah nomor device dikirim
    # tanpa nomor, dan nomor final diberikan saat sync. Nomor sementara lokal
    # (mis. T01-TMP-0007) hanya untuk cetak, tidak boleh dikirim sebagai
    # transaction_id karena kolomnya varchar(10).
    transaction_id = serializers.CharField(
        max_length=10,
        allow_null=True,
        required=False,
        validators=[
            RegexValidator(
                regex=TRANSACTION_ID_PATTERN,
                message=(
                    "Nomor nota harus berbentuk T01-000001 "
                    "(kode device plus enam digit)."
                ),
            )
        ],
    )

    transaction_time = serializers.DateTimeField()

    item_amount = serializers.IntegerField(min_value=0)

    shipping_amount = serializers.IntegerField(
        min_value=0,
        allow_null=True,
        required=False
    )

    total_amount = serializers.IntegerField(min_value=0)

    payment_method = serializers.ChoiceField(
        # Keputusan bisnis (2026-09-26): hanya 3 metode pembayaran.
        # EDC mencakup QRIS dan kartu; tidak ada metode "qris" atau "debit"
        # terpisah. Jangan menambah pilihan lain tanpa keputusan baru.
        choices=[
            "cash",
            "edc",
            "bank_transfer",
        ]
    )

    payment_amount = serializers.IntegerField(min_value=0)

    change_amount = serializers.IntegerField(min_value=0)

    cust_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    phone_no = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=PHONE_NO_MAX_LENGTH,
    )

    items = TransactionItemSerializer(
        many=True,
        allow_empty=False
    )

    def validate_cust_name(self, value):
        # Kolom nullable demi kompatibilitas: antrean offline yang disegel
        # sebelum fitur ini (dan klien lama) tidak membawa nama; NULL di
        # database berarti warisan itu. Penjualan baru selalu membawa nama
        # karena UI checkout memblokir konfirmasi tanpa nama (CUSTOMER_DESIGN.md).
        if value is None:
            return None
        name = str(value).strip()
        if not name:
            return None
        if len(name) > CUSTOMER_NAME_MAX_LENGTH:
            raise serializers.ValidationError(
                f"Nama pelanggan maksimal {CUSTOMER_NAME_MAX_LENGTH} karakter."
            )
        return name

    def validate_phone_no(self, value):
        if value is None:
            return None
        phone = str(value).strip()
        if not phone:
            return None
        if len(phone) > PHONE_NO_MAX_LENGTH or not PHONE_NO_PATTERN.match(phone):
            raise serializers.ValidationError(
                "Nomor HP hanya boleh berisi digit, tanda + di awal, spasi, "
                "tanda hubung, atau kurung."
            )
        return phone

    def validate(self, attrs):
        items = attrs["items"]

        shipping_amount = attrs.get("shipping_amount") or 0

        calculated_item_amount = 0

        for item in items:
            quantity = item["quantity"]
            unit_price = item["unit_price"]
            discount = item.get("discount", 0)

            calculated_subtotal = (
                unit_price * quantity
            ) - discount

            if item["subtotal"] != calculated_subtotal:
                raise serializers.ValidationError({
                    "items": (
                        "Subtotal barang tidak sama dengan jumlah yang "
                        "dihitung."
                    )
                })

            if calculated_subtotal < 0:
                raise serializers.ValidationError({
                    "items": (
                        "Diskon tidak boleh membuat subtotal barang negatif."
                    )
                })

            calculated_item_amount += calculated_subtotal

        calculated_total_amount = (
            calculated_item_amount + shipping_amount
        )

        payment_amount = attrs["payment_amount"]

        if payment_amount < calculated_total_amount:
            raise serializers.ValidationError({
                "payment_amount": (
                    "Jumlah bayar tidak boleh kurang dari total nota."
                )
            })

        # Kembalian hanya masuk akal untuk tunai: EDC/transfer dibayar persis
        # sejumlah total, jadi overpayment + kembalian pada metode itu
        # adalah tanda payload rusak, bukan penjualan sah.
        is_cash = attrs["payment_method"] == "cash"

        if not is_cash and payment_amount != calculated_total_amount:
            raise serializers.ValidationError({
                "payment_amount": (
                    "Pembayaran non-tunai harus tepat sama dengan total nota."
                )
            })

        calculated_change_amount = (
            payment_amount - calculated_total_amount
        )

        if attrs["item_amount"] != calculated_item_amount:
            raise serializers.ValidationError({
                "item_amount": (
                    "Jumlah barang tidak sama dengan jumlah yang dihitung."
                )
            })

        if attrs["total_amount"] != calculated_total_amount:
            raise serializers.ValidationError({
                "total_amount": (
                    "Total nota tidak sama dengan jumlah yang dihitung."
                )
            })

        if attrs["change_amount"] != calculated_change_amount:
            raise serializers.ValidationError({
                "change_amount": (
                    "Kembalian tidak sama dengan jumlah yang dihitung."
                )
            })

        return attrs