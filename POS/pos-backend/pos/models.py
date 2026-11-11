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
    # Kas (rancangan 3 Nov 2026): tablet ini boleh membuka & menutup kas toko.
    boleh_kas = models.BooleanField(default=False)
    # Jumlah nota/aksi kas yang belum terkirim menurut laporan terakhir tablet.
    # Tutup kas ditahan selama ada tablet dengan antrean.
    antrean = models.IntegerField(default=0)
    antrean_waktu = models.DateTimeField(null=True, blank=True)
    # Dikunci pemilik (mis. saat stock opname): kasir tertutup sampai dibuka lagi dari master.
    dikunci = models.BooleanField(default=False)
    kunci_alasan = models.CharField(max_length=200, blank=True, default="")
    kunci_waktu = models.DateTimeField(null=True, blank=True)

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


class NotaPiutang(models.Model):
    """Nota kasir dengan DP / barang belum dibawa (rancangan DP 31 Okt 2026).

    Kasir hanya mencatat faktanya. Master (layanan piutang) mengimpor baris ini
    menjadi Piutang: menghitung DP minimal, pelanggan tempo, dan ACC.
    `stok_ditunda`: barang "pesan dulu" belum memotong stok saat nota masuk.
    """

    sync_id = models.UUIDField(primary_key=True)
    transaction_id = models.CharField(max_length=10, null=True, blank=True)
    worker_id = models.CharField(max_length=5, blank=True, default="")
    dibayar = models.IntegerField()
    cara = models.CharField(max_length=20)
    status_barang = models.CharField(max_length=10)
    jatuh_tempo = models.DateField(null=True, blank=True)
    pelanggan_id = models.IntegerField(null=True, blank=True)
    stok_ditunda = models.BooleanField(default=False)
    masuk = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "nota_piutang"


class AksiPiutang(models.Model):
    """Pelunasan / barang diambil yang dicatat kasir (ber-PIN, bisa dari antrean offline).

    `aksi_id` dibuat tablet, jadi kirim ulang tidak mencatat dua kali. Master
    menerapkannya ke Piutang (status `baru` -> `diterapkan` / `ditolak`).
    Satu nota dirujuk lewat `piutang_id` (piutang master) atau `sync_id` (nota
    kasir yang mungkin belum diimpor master).
    """

    JENIS = [("bayar", "Pembayaran"), ("serahkan", "Barang diambil")]
    STATUS = [("baru", "Menunggu diproses"), ("diterapkan", "Diterapkan"), ("ditolak", "Ditolak")]

    aksi_id = models.UUIDField(primary_key=True)
    jenis = models.CharField(max_length=10, choices=JENIS)
    piutang_id = models.IntegerField(null=True, blank=True)
    sync_id = models.UUIDField(null=True, blank=True)
    jumlah = models.IntegerField(default=0)
    cara = models.CharField(max_length=20, blank=True, default="")
    catatan = models.CharField(max_length=300, blank=True, default="")
    worker_id = models.CharField(max_length=5)
    device_code = models.CharField(max_length=3, blank=True, default="")
    waktu = models.DateTimeField()
    masuk = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, choices=STATUS, default="baru", db_index=True)
    pesan = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "aksi_piutang"
        ordering = ["masuk"]


class NotaBayar(models.Model):
    """Rincian bayar campur (keputusan pemilik 12 Nov 2026): uang DITERIMA per metode.

    Hanya untuk nota `payment_method = "campur"`. Jumlah per metode = yang diterima
    (bukan bersih); kembalian nota (`change_amount`) selalu diberikan tunai dari laci,
    jadi bersih tunai = tunai diterima - kembalian (boleh minus kalau pembeli
    transfer lebih). Tabel nota kasir tidak diubah.
    """

    sync_id = models.UUIDField(db_index=True)
    metode = models.CharField(max_length=20)
    jumlah = models.BigIntegerField()

    class Meta:
        db_table = "nota_bayar"
        constraints = [models.UniqueConstraint(fields=["sync_id", "metode"], name="nota_bayar_unik")]


class NotaToko(models.Model):
    """Nota kasir milik toko titipan (brand), bukan toko pemegang kasir.

    Keranjang campur dipecah tablet menjadi beberapa nota (satu per pemilik
    barang) yang dibayar sekaligus; `grup_id` menyatukan nota-nota itu. Nota
    tanpa baris ini milik toko pemegang kasir (perilaku lama).
    """

    sync_id = models.UUIDField(primary_key=True)
    toko_id = models.IntegerField(null=True, blank=True, db_index=True)
    grup_id = models.UUIDField(null=True, blank=True, db_index=True)
    masuk = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "nota_toko"


class KasSesi(models.Model):
    """Satu sesi laci kas toko: dari buka kas sampai tutup kas (rancangan 3 Nov 2026).

    Sesi per toko, bukan per tablet. Penjualan tidak pernah diblokir walau kas
    belum dibuka: semua uang yang masuk/keluar sejak tutup kas sebelumnya
    (`awal_hitung`) masuk ke sesi ini. Saat tutup kas, angka seharusnya dan
    rinciannya dibekukan di `rincian` supaya ACC sore hari melihat angka yang
    sama dengan yang dilihat saat tutup.
    """

    STATUS = [("buka", "Kas dibuka"), ("ditutup", "Kas ditutup")]
    ACC = [("menunggu", "Menunggu ACC"), ("diterima", "Diterima"), ("ditolak", "Ditolak")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    toko_id = models.IntegerField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS, default="buka", db_index=True)
    awal_hitung = models.DateTimeField()
    dibuka_waktu = models.DateTimeField(default=timezone.now)
    dibuka_oleh = models.CharField(max_length=150)
    dibuka_dari = models.CharField(max_length=20, blank=True, default="")
    modal_saran = models.IntegerField(default=0)
    modal_awal = models.IntegerField(default=0)
    cadangan_lengkap_buka = models.BooleanField(default=True)
    catatan_buka = models.CharField(max_length=300, blank=True, default="")
    ditutup_waktu = models.DateTimeField(null=True, blank=True)
    ditutup_oleh = models.CharField(max_length=150, blank=True, default="")
    ditutup_dari = models.CharField(max_length=20, blank=True, default="")
    ambil_100 = models.IntegerField(default=0)
    ambil_50 = models.IntegerField(default=0)
    # Uang yang diambil saat tutup kas (rupiah). Lembar 100rb/50rb hanya dari tablet versi lama.
    diambil_uang = models.IntegerField(default=0)
    uang_sisa = models.IntegerField(default=0)
    cadangan_lengkap_tutup = models.BooleanField(default=True)
    seharusnya = models.IntegerField(default=0)
    selisih = models.IntegerField(default=0)
    rincian = models.JSONField(default=dict, blank=True)
    catatan_tutup = models.CharField(max_length=300, blank=True, default="")
    acc_status = models.CharField(max_length=10, choices=ACC, blank=True, default="", db_index=True)
    acc_oleh = models.CharField(max_length=150, blank=True, default="")
    acc_waktu = models.DateTimeField(null=True, blank=True)
    acc_catatan = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "kas_sesi"
        ordering = ["-dibuka_waktu"]
        constraints = [models.UniqueConstraint(
            fields=["toko_id"], condition=models.Q(status="buka"), name="kas_sesi_satu_buka_per_toko")]

    @property
    def diambil(self):
        return self.ambil_100 * 100000 + self.ambil_50 * 50000 + self.diambil_uang

    @property
    def dihitung(self):
        return self.diambil + self.uang_sisa


class KasGerak(models.Model):
    """Uang keluar/masuk laci di luar nota: uang keluar, tambah modal, cadangan.

    Dicatat saat kejadian (ber-PIN di tablet, atau oleh pemilik di master/HP).
    `gerak_id` dibuat pencatat, jadi antrean offline tablet boleh mengirim ulang.
    Masuk ke sesi kas menurut waktunya, sama seperti nota.
    """

    JENIS = [("keluar", "Uang keluar"), ("tambah_modal", "Tambah modal"),
             ("pakai_cadangan", "Pakai cadangan"), ("isi_cadangan", "Isi lagi cadangan")]

    gerak_id = models.UUIDField(primary_key=True)
    toko_id = models.IntegerField(null=True, blank=True, db_index=True)
    jenis = models.CharField(max_length=20, choices=JENIS)
    jumlah = models.IntegerField()
    kategori = models.CharField(max_length=40, blank=True, default="")
    keterangan = models.CharField(max_length=300, blank=True, default="")
    worker_id = models.CharField(max_length=5, blank=True, default="")
    oleh = models.CharField(max_length=150, blank=True, default="")
    device_code = models.CharField(max_length=20, blank=True, default="")
    waktu = models.DateTimeField(db_index=True)
    masuk = models.DateTimeField(default=timezone.now)
    batal = models.BooleanField(default=False)
    batal_oleh = models.CharField(max_length=150, blank=True, default="")
    batal_alasan = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "kas_gerak"
        ordering = ["waktu"]
