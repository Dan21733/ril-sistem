from django.db import models
from django.utils import timezone
import uuid


class Worker(models.Model):
    id = models.CharField(
        primary_key=True
    )
    name = models.TextField()
    pin = models.TextField()

    class Meta:
        managed = False
        db_table = "worker"


class Item(models.Model):
    pk = models.CompositePrimaryKey(
        "item_name",
        "variant"
    )

    item_name = models.CharField()
    variant = models.CharField()
    brand = models.CharField()
    category = models.CharField()
    cost_amount = models.IntegerField()
    ecommerce_item = models.BooleanField()
    in_stock = models.IntegerField()
    max_price = models.IntegerField()
    min_price = models.IntegerField()
    moka_id = models.CharField()
    sku = models.CharField()
    stock_alert = models.IntegerField(
        blank=True,
        null=True
    )
    track_alert = models.BooleanField()
    track_stock = models.BooleanField()

    class Meta:
        managed = False
        db_table = "item"


class ItemAliasPhrase(models.Model):
    """Satu frasa pencarian yang dipetakan ke satu item (tabel `item_alias`).

    Alias sengaja TIDAK disimpan di `item` (lihat catatan uji 2026-10-02 di
    ITEM_ALIAS_SEARCH_DESIGN.md): satu item bisa dicari lewat banyak frasa
    ("zink", "wastafel", "bak cuci piring"), sementara tabel `item`
    dikelola Inventory dan tidak boleh ditambah kolom oleh POS.

    `managed = False` seperti tabel eksternal lain: skema dibuat pemilik
    database lewat DDL di dokumen desain, bukan lewat migrasi Django.
    """

    pk = models.CompositePrimaryKey(
        "frasa",
        "item_name",
    )

    frasa = models.CharField()
    item_name = models.CharField()

    class Meta:
        managed = False
        db_table = "item_alias"


class Device(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
    )
    device_code = models.CharField(
        max_length=3,
        unique=True,
    )

    class Meta:
        managed = False
        db_table = "device"


class DeviceTransactionSequence(models.Model):
    device = models.OneToOneField(
        Device,
        models.DO_NOTHING,
        db_column="device_id",
        primary_key=True,
        related_name="transaction_sequence"
    )
    next_sequence = models.IntegerField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "device_transaction_sequence"


class Transactions(models.Model):
    sync_id = models.UUIDField(
        primary_key=True
    )
    transaction_id = models.CharField(
        max_length=10,
        unique=True,
        blank=True,
        null=True
    )
    worker = models.ForeignKey(
        Worker,
        models.DO_NOTHING,
        db_column="worker_id"
    )
    transaction_time = models.DateTimeField()

    item_amount = models.IntegerField()
    shipping_amount = models.IntegerField(
        blank=True,
        null=True
    )
    total_amount = models.IntegerField()
    payment_method = models.CharField()
    payment_amount = models.IntegerField()
    change_amount = models.IntegerField()
    # Nama pelanggan wajib diisi kasir untuk penjualan baru (ditegakkan UI
    # checkout); NULL berarti nota lama dari sebelum kolom ini ada, termasuk
    # antrean offline yang disegel sebelum update — lihat CUSTOMER_DESIGN.md.
    cust_name = models.TextField(
        blank=True,
        null=True
    )
    phone_no = models.CharField(
        max_length=20,
        blank=True,
        null=True
    )

    class Meta:
        managed = False
        db_table = "transactions"


class PaymentProof(models.Model):
    """Bukti transfer yang diunggah device kasir untuk satu nota.

    `object_key` menyimpan KEY relatif storage, bukan URL. Itu yang membuat
    backend penyimpanan bisa diganti (filesystem lokal di dev, object storage
    di produksi) tanpa migrasi data — lihat PAYMENT_PROOF_DESIGN.md §3.

    `uploaded_at` wajib punya default di sisi Python: kolomnya NOT NULL, dan
    Django mengirim NULL eksplisit bila field tidak punya default, sehingga
    default CURRENT_TIMESTAMP milik DB tidak pernah terpakai. Pola itulah yang
    dulu membuat POST /api/devices/register/ gagal 500.
    """

    proof_id = models.UUIDField(
        primary_key=True
    )
    sync_id = models.UUIDField()
    object_key = models.TextField(
        unique=True
    )
    content_type = models.CharField(
        max_length=32
    )
    byte_size = models.IntegerField()
    sha256 = models.CharField(
        max_length=64
    )
    captured_at = models.DateTimeField(
        blank=True,
        null=True
    )
    uploaded_at = models.DateTimeField(
        default=timezone.now
    )
    device_code = models.CharField(
        max_length=3,
        blank=True,
        null=True
    )

    class Meta:
        managed = False
        db_table = "payment_proof"


class RefundEvent(models.Model):
    """Satu aksi void/return/refund atas sebuah nota (REFUND_DESIGN.md).

    Append-only: API tidak pernah UPDATE/DELETE baris ini; koreksi = event
    baru. Nota asal tidak diubah. Efek stok Inventory sengaja tidak ada di
    sini — Inventory mengonsumsi event ini lewat kontrak terpisah.

    `managed = False` seperti tabel eksternal lain: DDL milik pemilik skema.
    """

    refund_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
    )
    sync_id = models.UUIDField()
    transaction_id = models.CharField(
        max_length=10,
        blank=True,
        null=True,
    )
    event_type = models.CharField()
    reason = models.TextField()
    refund_method = models.CharField()
    refund_amount = models.IntegerField()
    approver_worker_id = models.CharField()
    device_code = models.CharField(
        max_length=3,
    )
    created_at = models.DateTimeField(
        default=timezone.now
    )

    class Meta:
        managed = False
        db_table = "refund_event"


class RefundEventDetail(models.Model):
    """Baris barang satu event return/refund (parsial didukung per barang)."""

    pk = models.CompositePrimaryKey(
        "refund_id",
        "item_name",
        "variant",
    )

    refund_id = models.UUIDField()
    item_name = models.CharField()
    variant = models.CharField()
    quantity = models.IntegerField()
    unit_price = models.IntegerField()
    subtotal = models.IntegerField()

    class Meta:
        managed = False
        db_table = "refund_event_detail"


class TransactionDetail(models.Model):
    pk = models.CompositePrimaryKey(
        "sync_id",
        "item_name",
        "variant",
    )

    sync_id = models.UUIDField()
    item_name = models.CharField()
    variant = models.CharField()
    discount = models.IntegerField()
    quantity = models.IntegerField()
    subtotal = models.IntegerField()
    unit_price = models.IntegerField()

    class Meta:
        managed = False
        db_table = "transaction_detail"

# ---------------------------------------------------------------------------
# Tabel milik toko sendiri (17 Okt 2026), dikelola migrasi Django biasa.
#
# Berbeda dengan tabel di atas (managed = False, skema lama dari luar), tiga
# tabel ini dibuat lewat `migrate`. Tidak ada FOREIGN KEY ke `worker`/`device`
# supaya bisa dibuat di database apa pun (termasuk database uji) tanpa
# bergantung pada tabel luar. Diisi dan diubah dari master (menu Kasir).
# ---------------------------------------------------------------------------


class WorkerPin(models.Model):
    """Riwayat PIN karyawan beserta masa berlakunya.

    Nota dicocokkan dengan PIN yang berlaku *saat nota dibuat*
    (`transaction_time`), bukan saat nota sampai di server. Jadi nota offline
    yang baru terkirim setelah PIN diganti tetap diterima, selama PIN lamanya
    berakhir belum lebih dari `pin_tenggang_hari` (pengaturan kasir).

    - berlaku_mulai None  : berlaku sejak awal (PIN lama dari tabel worker).
    - berlaku_sampai None : masih berlaku sampai diganti.
    Karyawan yang dinonaktifkan = semua jendela PIN-nya ditutup.
    """

    worker_id = models.CharField(max_length=5, db_index=True)
    pin_hash = models.TextField()
    berlaku_mulai = models.DateTimeField(null=True, blank=True)
    berlaku_sampai = models.DateTimeField(null=True, blank=True)
    dibuat = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "worker_pin"
        ordering = ["worker_id", "berlaku_mulai"]

    def berlaku_pada(self, waktu):
        return (self.berlaku_mulai is None or self.berlaku_mulai <= waktu) and (
            self.berlaku_sampai is None or waktu < self.berlaku_sampai
        )


class PosSetting(models.Model):
    """Aturan kasir yang diatur pemilik dari master (kunci -> nilai teks)."""

    kunci = models.CharField(max_length=64, primary_key=True)
    nilai = models.TextField(blank=True, default="")

    class Meta:
        db_table = "pos_setting"


class DeviceStatus(models.Model):
    """Status tablet terdaftar: nama yang mudah dikenali, aktif, terakhir terlihat."""

    device_id = models.UUIDField(primary_key=True)
    nama = models.CharField(max_length=60, blank=True, default="")
    aktif = models.BooleanField(default=True)
    didaftar = models.DateTimeField(null=True, blank=True)
    terakhir_dilihat = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "device_status"


class NotaTinjau(models.Model):
    """Nota final dari tablet yang tidak lolos pemeriksaan server.

    Nota yang sudah final di tablet (PIN cocok lokal, sudah dicetak/diserahkan
    ke pembeli) tidak ditolak mentah-mentah: kalau PIN-nya tidak dikenali,
    harganya di bawah floor yang sekarang, barangnya sudah tidak ada, dll.,
    nota disimpan di sini dan tablet menganggapnya terkirim. Pemilik yang
    memutuskan di master (Kasir > Nota perlu ACC): diterima (masuk penjualan
    dan memotong stok) atau ditolak (tetap tercatat, tidak dihitung).
    """

    STATUS = [("menunggu", "Menunggu ACC"), ("diterima", "Diterima"), ("ditolak", "Ditolak")]

    sync_id = models.UUIDField(primary_key=True)
    transaction_id = models.CharField(max_length=10, null=True, blank=True)
    device_code = models.CharField(max_length=3, blank=True, default="")
    payload = models.JSONField()
    worker_id = models.CharField(max_length=5, null=True, blank=True)
    alasan = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS, default="menunggu", db_index=True)
    masuk = models.DateTimeField(default=timezone.now)
    diputuskan_oleh = models.CharField(max_length=150, blank=True, default="")
    diputuskan_waktu = models.DateTimeField(null=True, blank=True)
    catatan = models.TextField(blank=True, default="")

    class Meta:
        db_table = "nota_tinjau"
        ordering = ["-masuk"]


class NotaPersetujuan(models.Model):
    """Persetujuan harga di bawah floor dengan PIN penyetuju di tablet."""

    sync_id = models.UUIDField(primary_key=True)
    approver_worker_id = models.CharField(max_length=5)
    waktu = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "nota_persetujuan"


class MutasiKasir(models.Model):
    """Perubahan stok karena penjualan kasir, dicatat saat stok dipotong.

    Dicatat di dalam transaksi yang sama dengan pemotongan stok, jadi stok
    sebelum/sesudah persis. Master menampilkannya di Riwayat stok bersama
    perubahan stok lain.
    """

    sync_id = models.UUIDField(db_index=True)
    transaction_id = models.CharField(max_length=10, null=True, blank=True)
    worker_id = models.CharField(max_length=5, blank=True, default="")
    item_name = models.CharField(max_length=255, db_index=True)
    variant = models.CharField(max_length=255)
    jumlah = models.IntegerField()
    stok_sebelum = models.IntegerField()
    stok_sesudah = models.IntegerField()
    waktu = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "mutasi_kasir"
        ordering = ["-waktu", "-id"]
