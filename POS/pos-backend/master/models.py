"""Tabel milik modul master.

Tabel POS (item, transactions, dst.) tidak diubah. Data tambahan untuk barang
disimpan di tabel master dan dicocokkan lewat (item_name, variant):

- ItemInfo   : satu baris per nama barang  -> alias
- VarianInfo : satu baris per varian       -> ID tetap, rak, alias tambahan, aktif

`VarianInfo.id` adalah ID tetap barang: tidak berubah walau nama diganti.
"""
import uuid

from django.conf import settings
from django.db import models


class Toko(models.Model):
    """Toko yang dikelola dari master. Tidak dibatasi jumlahnya.

    `stok_di_kasir`: stok toko ini adalah stok di tabel kasir (item.in_stock).
    POS sekarang hanya mengenal satu toko, jadi paling banyak satu toko yang
    bertanda ini. Toko lain dicatat (PO, faktur, opname, transfer) tanpa stok
    sistem, sampai toko itu punya kasir sendiri.
    """
    kode = models.CharField(max_length=10, unique=True)
    nama = models.CharField(max_length=100)
    atas_nama = models.CharField(max_length=150, blank=True, default="")
    alamat = models.TextField(blank=True, default="")
    telepon = models.CharField(max_length=50, blank=True, default="")
    pkp = models.BooleanField(default=False)
    pkp_mulai = models.DateField(null=True, blank=True)
    stok_di_kasir = models.BooleanField(default=False)
    aktif = models.BooleanField(default=True)
    urutan = models.IntegerField(default=0)

    class Meta:
        db_table = "master_toko"
        ordering = ["urutan", "id"]

    def __str__(self):
        return self.nama

    def pkp_pada(self, tanggal):
        return bool(self.pkp and (self.pkp_mulai is None or tanggal >= self.pkp_mulai))


class Lokasi(models.Model):
    """Tempat stok disimpan di toko yang memegang stok kasir.

    Tepat satu lokasi berjenis `toko` (rak/display). Stoknya tidak disimpan,
    tetapi dihitung: total di kasir - jumlah semua gudang. Lokasi berjenis
    `gudang` menyimpan jumlahnya di StokLokasi. Jumlah lokasi tidak dibatasi.
    """
    JENIS = [("toko", "Toko (display)"), ("gudang", "Gudang")]

    kode = models.CharField(max_length=20, unique=True)
    nama = models.CharField(max_length=100)
    jenis = models.CharField(max_length=10, choices=JENIS, default="gudang")
    keterangan = models.CharField(max_length=200, blank=True, default="")
    aktif = models.BooleanField(default=True)
    urutan = models.IntegerField(default=0)

    class Meta:
        db_table = "master_lokasi"
        ordering = ["urutan", "id"]

    def __str__(self):
        return self.nama


class Rak(models.Model):
    kode = models.CharField(max_length=20, primary_key=True)
    nama = models.CharField(max_length=100)
    # Lokasi tempat rak ini berada. Kolom `lokasi` (teks) adalah catatan lama, dipertahankan.
    tempat = models.ForeignKey(Lokasi, null=True, blank=True, on_delete=models.SET_NULL, related_name="rak")
    lokasi = models.CharField(max_length=100, blank=True, default="")
    keterangan = models.TextField(blank=True, default="")

    class Meta:
        db_table = "master_rak"
        ordering = ["kode"]

    def __str__(self):
        return f"{self.kode} - {self.nama}"


class ItemInfo(models.Model):
    item_name = models.CharField(max_length=255, unique=True)
    alias = models.TextField(blank=True, default="")

    class Meta:
        db_table = "master_item_info"

    def __str__(self):
        return self.item_name


class VarianInfo(models.Model):
    item_name = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    rak = models.ForeignKey(
        Rak, null=True, blank=True, on_delete=models.SET_NULL, related_name="varian"
    )
    alias_tambahan = models.TextField(blank=True, default="")
    aktif = models.BooleanField(default=True)
    # Modal terakhir tetap di item.cost_amount (tabel POS). Modal rata-rata
    # disimpan di sini; sampai barang masuk dicatat, nilainya sama dengan modal terakhir.
    modal_rata = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "master_varian_info"
        constraints = [
            models.UniqueConstraint(
                fields=["item_name", "variant"], name="master_varian_info_unik"
            )
        ]
        indexes = [models.Index(fields=["item_name"], name="master_varian_item_idx")]

    def __str__(self):
        return f"{self.item_name} / {self.variant}"


class Log(models.Model):
    AKSI = [
        ("ubah", "Ubah"),
        ("tambah", "Tambah"),
        ("ganti_nama", "Ganti nama"),
        ("hapus", "Hapus"),
    ]
    SUMBER = [("layar", "Layar"), ("impor", "Impor Excel"), ("massal", "Ubah massal"),
              ("pembelian", "Pembelian"), ("sistem", "Sistem")]

    waktu = models.DateTimeField(auto_now_add=True, db_index=True)
    pengguna = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    nama_pengguna = models.CharField(max_length=150, blank=True, default="")
    aksi = models.CharField(max_length=20, choices=AKSI, default="ubah")
    sumber = models.CharField(max_length=20, choices=SUMBER, default="layar")
    objek = models.CharField(max_length=30, default="barang")
    item_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    variant = models.CharField(max_length=255, blank=True, default="")
    kolom = models.CharField(max_length=50, blank=True, default="")
    lama = models.TextField(blank=True, default="")
    baru = models.TextField(blank=True, default="")
    # Kolom modal ditandai supaya nilainya bisa disembunyikan dari peran yang
    # tidak boleh melihat modal.
    rahasia = models.BooleanField(default=False)
    batch = models.UUIDField(null=True, blank=True, db_index=True)
    catatan = models.TextField(blank=True, default="")

    class Meta:
        db_table = "master_log"
        ordering = ["-waktu", "-id"]


class Impor(models.Model):
    STATUS = [("pratinjau", "Pratinjau"), ("diterapkan", "Diterapkan"), ("batal", "Batal")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    waktu = models.DateTimeField(auto_now_add=True)
    pengguna = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    nama_berkas = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS, default="pratinjau")
    # Hasil baca berkas: daftar perubahan, baris baru, dan baris bermasalah.
    isi = models.JSONField(default=dict)
    hasil = models.JSONField(default=dict)

    class Meta:
        db_table = "master_impor"
        ordering = ["-waktu"]


class Supplier(models.Model):
    nama = models.CharField(max_length=150, unique=True)
    kontak = models.CharField(max_length=150, blank=True, default="")
    telepon = models.CharField(max_length=50, blank=True, default="")
    alamat = models.TextField(blank=True, default="")
    # Disiapkan untuk PKP: dipakai saat faktur pembelian dicatat.
    pkp = models.BooleanField(default=False)
    npwp = models.CharField(max_length=30, blank=True, default="")
    catatan = models.TextField(blank=True, default="")
    aktif = models.BooleanField(default=True)

    class Meta:
        db_table = "master_supplier"
        ordering = ["nama"]

    def __str__(self):
        return self.nama


class MutasiStok(models.Model):
    """Catatan setiap perubahan stok yang dilakukan lewat master.

    Penjualan di kasir memotong item.in_stock langsung dan tidak lewat sini;
    penjualan dibaca dari tabel nota.
    """
    JENIS = [
        ("hitung", "Hitung ulang"),
        ("rusak", "Rusak"),
        ("hilang", "Hilang"),
        ("koreksi", "Koreksi salah input"),
        ("awal", "Stok awal"),
        ("lain", "Lainnya"),
        ("beli", "Barang masuk (pembelian)"),
        ("buka_dus", "Buka dus"),
        ("retur_beli", "Retur ke supplier"),
        ("transfer_keluar", "Transfer ke toko lain"),
        ("transfer_masuk", "Transfer dari toko lain"),
        ("jual_manual", "Penjualan nota manual"),
        ("impor", "Import Excel"),
        ("retur_jual", "Retur dari pembeli"),
    ]

    waktu = models.DateTimeField(auto_now_add=True, db_index=True)
    pengguna = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    nama_pengguna = models.CharField(max_length=150, blank=True, default="")
    varian = models.ForeignKey(VarianInfo, null=True, on_delete=models.SET_NULL, related_name="mutasi")
    item_name = models.CharField(max_length=255, db_index=True)
    variant = models.CharField(max_length=255)
    jenis = models.CharField(max_length=20, choices=JENIS)
    jumlah = models.IntegerField()
    stok_sebelum = models.IntegerField()
    stok_sesudah = models.IntegerField()
    catatan = models.TextField(blank=True, default="")
    # Lokasi tempat perubahan ini terjadi (null = Toko/display atau sebelum ada lokasi).
    lokasi = models.ForeignKey("Lokasi", null=True, blank=True, on_delete=models.PROTECT, related_name="mutasi_stok")

    class Meta:
        db_table = "master_mutasi_stok"
        ordering = ["-waktu", "-id"]


class Pengaturan(models.Model):
    """Pengaturan umum. Selalu satu baris (id=1).

    pkp/pkp_mulai di sini sudah tidak dipakai: status PKP sekarang per toko
    (lihat Toko). Kolomnya dibiarkan supaya tidak ada data yang dihapus.
    """
    pkp = models.BooleanField(default=False)
    pkp_mulai = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "master_pengaturan"

    @classmethod
    def ambil(cls):
        objek, _ = cls.objects.get_or_create(pk=1)
        return objek

    def pkp_pada(self, tanggal):
        return bool(self.pkp and (self.pkp_mulai is None or tanggal >= self.pkp_mulai))


class TarifPPN(models.Model):
    """Tarif PPN efektif, berlaku sejak tanggal tertentu. Tidak pernah ditanam di kode."""
    mulai = models.DateField(unique=True)
    persen = models.DecimalField(max_digits=5, decimal_places=2)
    catatan = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "master_tarif_ppn"
        ordering = ["-mulai"]


class Pembelian(models.Model):
    STATUS = [
        ("dibuat", "Dipesan"),
        ("menunggu", "Menunggu ACC"),
        ("diterima", "Diterima"),
        ("batal", "Batal"),
    ]
    MODE_PPN = [
        ("tanpa", "Tanpa PPN"),
        ("termasuk", "Harga sudah termasuk PPN"),
        ("terpisah", "PPN ditulis terpisah"),
    ]

    nomor = models.CharField(max_length=20, unique=True)
    toko = models.ForeignKey(Toko, on_delete=models.PROTECT, related_name="pembelian", null=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="pembelian")
    status = models.CharField(max_length=20, choices=STATUS, default="dibuat", db_index=True)
    tanggal_pesan = models.DateField()
    catatan = models.TextField(blank=True, default="")
    dibuat_oleh = models.CharField(max_length=150, blank=True, default="")
    dibuat_waktu = models.DateTimeField(auto_now_add=True)

    # Diisi saat barang datang
    tanggal_terima = models.DateField(null=True, blank=True)
    diterima_oleh = models.CharField(max_length=150, blank=True, default="")
    catatan_terima = models.TextField(blank=True, default="")
    nomor_faktur = models.CharField(max_length=60, blank=True, default="")
    tanggal_faktur = models.DateField(null=True, blank=True)
    jatuh_tempo = models.DateField(null=True, blank=True)
    mode_ppn = models.CharField(max_length=20, choices=MODE_PPN, default="tanpa")
    nomor_faktur_pajak = models.CharField(max_length=60, blank=True, default="")
    # Ongkos kirim pembelian. Dibagi ke tiap baris menurut nilainya dan ikut ke modal.
    # ongkir_ke_supplier=True: ditagih di faktur supplier ini, jadi ikut hutang.
    # False: dibayar sendiri ke ekspedisi, hanya masuk modal.
    ongkir = models.BigIntegerField(default=0)
    ongkir_ke_supplier = models.BooleanField(default=True)
    # Terima buta oleh gudang: id baris yang jumlahnya beda dari pesanan dan sedang
    # diminta dihitung ulang (gudang tidak tahu angka pesanannya).
    hitung_ulang = models.JSONField(default=list, blank=True)
    # Kiriman sebagian: PO lanjutan berisi sisa yang belum datang.
    lanjutan_dari = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="lanjutan")

    # Dibekukan saat ACC
    acc_oleh = models.CharField(max_length=150, blank=True, default="")
    acc_waktu = models.DateTimeField(null=True, blank=True)
    tarif_ppn = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    toko_pkp = models.BooleanField(default=False)
    total_dpp = models.BigIntegerField(default=0)
    total_ppn = models.BigIntegerField(default=0)
    total = models.BigIntegerField(default=0)
    terbayar = models.BigIntegerField(default=0)
    # Nilai barang yang sudah diretur ke supplier (mengurangi hutang).
    diretur = models.BigIntegerField(default=0)

    class Meta:
        db_table = "master_pembelian"
        ordering = ["-dibuat_waktu", "-id"]

    @property
    def sisa(self):
        """Hutang yang masih harus dibayar. Minus berarti supplier yang harus mengembalikan uang."""
        return self.total - self.diretur - self.terbayar

    @property
    def lebih_bayar(self):
        return max(-self.sisa, 0)

    @property
    def lunas(self):
        return self.status == "diterima" and self.sisa <= 0


class PembelianBaris(models.Model):
    pembelian = models.ForeignKey(Pembelian, on_delete=models.CASCADE, related_name="baris")
    varian = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="pembelian")
    item_name = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    jumlah_pesan = models.IntegerField()
    jumlah_terima = models.IntegerField(null=True, blank=True)
    # Harga satuan seperti tertulis di faktur (arti PPN-nya mengikuti mode_ppn).
    harga = models.IntegerField()
    # Lokasi tempat barang ditaruh saat datang (null = Toko/display).
    lokasi = models.ForeignKey("Lokasi", null=True, blank=True, on_delete=models.PROTECT, related_name="baris_beli")
    # Hitungan pertama gudang (sebelum hitung ulang). Null = belum dihitung gudang.
    jumlah_hitung1 = models.IntegerField(null=True, blank=True)
    # Barang yang datang tapi tidak ada di pesanan (dicatat gudang, jumlah_pesan = 0).
    di_luar_pesanan = models.BooleanField(default=False)
    # Dibekukan saat ACC
    dpp = models.BigIntegerField(default=0)
    ppn = models.BigIntegerField(default=0)
    modal_satuan = models.IntegerField(default=0)
    ongkir = models.BigIntegerField(default=0)

    class Meta:
        db_table = "master_pembelian_baris"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["pembelian", "varian"], name="master_pembelian_baris_unik")
        ]


class Pembayaran(models.Model):
    CARA = [("tunai", "Tunai"), ("transfer", "Transfer"), ("lain", "Lainnya")]

    pembelian = models.ForeignKey(Pembelian, on_delete=models.PROTECT, related_name="pembayaran")
    tanggal = models.DateField()
    jumlah = models.BigIntegerField()
    cara = models.CharField(max_length=20, choices=CARA, default="transfer")
    catatan = models.CharField(max_length=300, blank=True, default="")
    oleh = models.CharField(max_length=150, blank=True, default="")
    waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "master_pembayaran"
        ordering = ["tanggal", "id"]


class FakturManual(models.Model):
    """Faktur supplier di luar PO: pesanan mendadak, atau faktur lama sebelum sistem dipakai.

    Hanya mencatat hutang. Stok dan modal TIDAK berubah dari sini; barangnya
    ditulis sebagai keterangan bebas, bukan baris barang.
    """
    toko = models.ForeignKey(Toko, on_delete=models.PROTECT, related_name="faktur_manual", null=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="faktur_manual")
    nomor_faktur = models.CharField(max_length=60)
    tanggal_faktur = models.DateField()
    jatuh_tempo = models.DateField(null=True, blank=True)
    keterangan = models.TextField()
    total = models.BigIntegerField()
    terbayar = models.BigIntegerField(default=0)
    catatan = models.TextField(blank=True, default="")
    dibuat_oleh = models.CharField(max_length=150, blank=True, default="")
    dibuat_waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "master_faktur_manual"
        ordering = ["-tanggal_faktur", "-id"]
        constraints = [models.UniqueConstraint(fields=["toko", "supplier", "nomor_faktur"], name="master_faktur_manual_unik_toko")]

    @property
    def sisa(self):
        return self.total - self.terbayar

    @property
    def lunas(self):
        return self.sisa <= 0


class FotoFaktur(models.Model):
    faktur = models.ForeignKey(FakturManual, on_delete=models.CASCADE, related_name="foto")
    # KEY di storage, bukan URL (sama seperti bukti transfer POS).
    object_key = models.CharField(max_length=300, unique=True)
    content_type = models.CharField(max_length=60)
    nama_asli = models.CharField(max_length=200, blank=True, default="")
    ukuran = models.IntegerField(default=0)
    waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "master_faktur_foto"
        ordering = ["id"]


class BayarFaktur(models.Model):
    faktur = models.ForeignKey(FakturManual, on_delete=models.PROTECT, related_name="pembayaran")
    tanggal = models.DateField()
    jumlah = models.BigIntegerField()
    cara = models.CharField(max_length=20, choices=Pembayaran.CARA, default="transfer")
    catatan = models.CharField(max_length=300, blank=True, default="")
    oleh = models.CharField(max_length=150, blank=True, default="")
    waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "master_faktur_bayar"
        ordering = ["tanggal", "id"]


class Retur(models.Model):
    """Retur barang ke supplier setelah pembelian di-ACC. Langsung berlaku."""
    pembelian = models.ForeignKey(Pembelian, on_delete=models.PROTECT, related_name="retur")
    tanggal = models.DateField()
    alasan = models.CharField(max_length=300)
    oleh = models.CharField(max_length=150, blank=True, default="")
    waktu = models.DateTimeField(auto_now_add=True)
    nilai = models.BigIntegerField(default=0)

    class Meta:
        db_table = "master_retur"
        ordering = ["tanggal", "id"]


class ReturBaris(models.Model):
    retur = models.ForeignKey(Retur, on_delete=models.CASCADE, related_name="baris")
    baris = models.ForeignKey(PembelianBaris, on_delete=models.PROTECT, related_name="retur")
    jumlah = models.IntegerField()
    # Nilai yang mengurangi hutang, dipisah supaya PPN masukan bisa dikoreksi nanti.
    dpp = models.BigIntegerField(default=0)
    ppn = models.BigIntegerField(default=0)

    # Lokasi stok di toko yang memegang stok kasir (null = Toko/display).
    lokasi = models.ForeignKey("Lokasi", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "master_retur_baris"
        ordering = ["id"]


class Opname(models.Model):
    """Satu sesi hitung stok oleh kepala gudang. Stok baru berubah setelah di-ACC di master."""
    STATUS = [("draf", "Sedang dihitung"), ("diajukan", "Menunggu ACC"), ("disetujui", "Disetujui"), ("ditolak", "Ditolak")]

    status = models.CharField(max_length=20, choices=STATUS, default="draf", db_index=True)
    toko = models.ForeignKey(Toko, on_delete=models.PROTECT, related_name="opname", null=True)
    # Lokasi yang dihitung (hanya untuk toko yang memegang stok kasir). Null = Toko/display.
    lokasi = models.ForeignKey("Lokasi", null=True, blank=True, on_delete=models.PROTECT, related_name="opname")
    # Hitungan dari tugas (tiket jenis "hitung") yang dibuka pemilik.
    tiket = models.OneToOneField("Tiket", null=True, blank=True, on_delete=models.SET_NULL, related_name="opname")
    dibuat_oleh = models.CharField(max_length=150)
    dibuat_waktu = models.DateTimeField(auto_now_add=True)
    diajukan_waktu = models.DateTimeField(null=True, blank=True)
    catatan = models.CharField(max_length=300, blank=True, default="")
    diputus_oleh = models.CharField(max_length=150, blank=True, default="")
    diputus_waktu = models.DateTimeField(null=True, blank=True)
    catatan_putusan = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "master_opname"
        ordering = ["-id"]


class OpnameBaris(models.Model):
    opname = models.ForeignKey(Opname, on_delete=models.CASCADE, related_name="baris")
    varian = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="opname")
    item_name = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    hitung = models.IntegerField()
    # Stok di sistem saat barang itu dihitung. Tidak diperlihatkan ke kepala gudang.
    # Yang diterapkan saat ACC adalah selisihnya, supaya penjualan sesudah
    # penghitungan tidak ikut terhapus.
    stok_saat_hitung = models.IntegerField()
    waktu_hitung = models.DateTimeField()
    catatan = models.CharField(max_length=200, blank=True, default="")
    diterapkan = models.BooleanField(default=False)

    class Meta:
        db_table = "master_opname_baris"
        ordering = ["item_name", "variant"]
        constraints = [models.UniqueConstraint(fields=["opname", "varian"], name="master_opname_baris_unik")]

    @property
    def selisih(self):
        return self.hitung - self.stok_saat_hitung


class Transfer(models.Model):
    """Pindah barang antar toko. Dua toko bisa beda pemilik, jadi nilainya
    (dengan modal) dicatat sebagai saldo antar toko."""
    STATUS = [("draf", "Disiapkan"), ("dikirim", "Dalam perjalanan"), ("diterima", "Diterima"), ("batal", "Batal")]

    nomor = models.CharField(max_length=20, unique=True)
    dari = models.ForeignKey(Toko, on_delete=models.PROTECT, related_name="transfer_keluar")
    ke = models.ForeignKey(Toko, on_delete=models.PROTECT, related_name="transfer_masuk")
    status = models.CharField(max_length=20, choices=STATUS, default="draf", db_index=True)
    tanggal = models.DateField()
    catatan = models.TextField(blank=True, default="")
    dibuat_oleh = models.CharField(max_length=150, blank=True, default="")
    dibuat_waktu = models.DateTimeField(auto_now_add=True)
    dikirim_oleh = models.CharField(max_length=150, blank=True, default="")
    dikirim_waktu = models.DateTimeField(null=True, blank=True)
    diterima_oleh = models.CharField(max_length=150, blank=True, default="")
    diterima_waktu = models.DateTimeField(null=True, blank=True)
    catatan_terima = models.TextField(blank=True, default="")
    # Nilai yang masuk saldo antar toko: jumlah diterima x modal saat dikirim.
    nilai = models.BigIntegerField(default=0)

    class Meta:
        db_table = "master_transfer"
        ordering = ["-dibuat_waktu", "-id"]


class TransferBaris(models.Model):
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name="baris")
    varian = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="transfer")
    item_name = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    jumlah_kirim = models.IntegerField()
    jumlah_terima = models.IntegerField(null=True, blank=True)
    # Dibekukan saat dikirim.
    modal_satuan = models.IntegerField(default=0)

    # Lokasi stok di toko yang memegang stok kasir (null = Toko/display).
    lokasi = models.ForeignKey("Lokasi", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "master_transfer_baris"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["transfer", "varian"], name="master_transfer_baris_unik")]


class PelunasanSaldo(models.Model):
    """Pelunasan saldo antar toko: `pembayar` membayar ke `penerima`."""
    pembayar = models.ForeignKey(Toko, on_delete=models.PROTECT, related_name="pelunasan_keluar")
    penerima = models.ForeignKey(Toko, on_delete=models.PROTECT, related_name="pelunasan_masuk")
    tanggal = models.DateField()
    jumlah = models.BigIntegerField()
    cara = models.CharField(max_length=20, choices=Pembayaran.CARA, default="transfer")
    catatan = models.CharField(max_length=300, blank=True, default="")
    oleh = models.CharField(max_length=150, blank=True, default="")
    waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "master_pelunasan_saldo"
        ordering = ["-tanggal", "-id"]


class NotaManual(models.Model):
    """Penjualan yang ditulis di nota tangan, dimasukkan dari master (di luar POS)."""
    CARA = [("cash", "Tunai"), ("bank_transfer", "Transfer"), ("edc", "EDC"), ("lainnya", "Lainnya")]

    nomor = models.CharField(max_length=20, unique=True)
    toko = models.ForeignKey(Toko, on_delete=models.PROTECT, related_name="nota_manual")
    nomor_tulisan = models.CharField(max_length=40, blank=True, default="")
    tanggal = models.DateField(db_index=True)
    pembeli = models.CharField(max_length=150, blank=True, default="")
    cara_bayar = models.CharField(max_length=20, choices=CARA, default="cash")
    ongkir = models.BigIntegerField(default=0)
    total_barang = models.BigIntegerField(default=0)
    total = models.BigIntegerField(default=0)
    catatan = models.TextField(blank=True, default="")
    dibuat_oleh = models.CharField(max_length=150, blank=True, default="")
    dibuat_waktu = models.DateTimeField(auto_now_add=True)
    batal = models.BooleanField(default=False, db_index=True)
    batal_alasan = models.CharField(max_length=300, blank=True, default="")
    batal_oleh = models.CharField(max_length=150, blank=True, default="")
    batal_waktu = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "master_nota_manual"
        ordering = ["-tanggal", "-id"]


class NotaManualBaris(models.Model):
    nota = models.ForeignKey(NotaManual, on_delete=models.CASCADE, related_name="baris")
    varian = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="nota_manual")
    item_name = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    jumlah = models.IntegerField()
    harga = models.IntegerField()
    subtotal = models.BigIntegerField()
    # Modal rata saat nota dimasukkan, untuk laporan laba.
    modal_satuan = models.IntegerField(null=True, blank=True)

    # Lokasi stok di toko yang memegang stok kasir (null = Toko/display).
    lokasi = models.ForeignKey("Lokasi", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "master_nota_manual_baris"
        ordering = ["id"]


class FotoNota(models.Model):
    nota = models.ForeignKey(NotaManual, on_delete=models.CASCADE, related_name="foto")
    object_key = models.CharField(max_length=300, unique=True)
    content_type = models.CharField(max_length=60)
    ukuran = models.IntegerField(default=0)
    waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "master_nota_manual_foto"
        ordering = ["id"]


class Konversi(models.Model):
    """Pasangan buka dus yang dikunci: satu kemasan besar dipecah menjadi satuan eceran.

    Contoh: Semen 1 sak -> Semen per kg, isi 50. Pasangan hanya bisa dibuat di
    sini; saat membuka dus tidak ada isian bebas selain jumlah dan (kalau
    isinya memang tidak tetap) isi sebenarnya.
    """
    dari = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="konversi_dari")
    ke = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="konversi_ke")
    isi = models.IntegerField()
    # Isi tidak tetap (mis. paku 1 dus bisa 18-20 kg): isi sebenarnya diisi saat dibuka.
    isi_tidak_tetap = models.BooleanField(default=False)
    aktif = models.BooleanField(default=True)
    catatan = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "master_konversi"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["dari", "ke"], name="master_konversi_unik")]


class BukaDus(models.Model):
    """Satu kali buka dus. Dari aplikasi gudang statusnya menunggu sampai pemilik ACC."""
    STATUS = [("menunggu", "Menunggu ACC"), ("diterapkan", "Diterapkan"), ("ditolak", "Ditolak")]
    konversi = models.ForeignKey(Konversi, on_delete=models.PROTECT, related_name="riwayat")
    status = models.CharField(max_length=12, choices=STATUS, default="menunggu", db_index=True)
    diputus_oleh = models.CharField(max_length=150, blank=True, default="")
    diputus_waktu = models.DateTimeField(null=True, blank=True)
    alasan = models.CharField(max_length=200, blank=True, default="")
    waktu = models.DateTimeField(auto_now_add=True, db_index=True)
    oleh = models.CharField(max_length=150, blank=True, default="")
    jumlah = models.IntegerField()
    isi_total = models.IntegerField()
    modal_satuan = models.IntegerField(null=True, blank=True)
    catatan = models.CharField(max_length=300, blank=True, default="")
    lokasi_dari = models.ForeignKey("Lokasi", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    lokasi_ke = models.ForeignKey("Lokasi", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "master_buka_dus"
        ordering = ["-waktu", "-id"]


class Sampah(models.Model):
    """Varian yang dibuang ke keranjang sampah.

    Baris `item` (tabel kasir) dihapus supaya barang hilang dari kasir dan dari
    semua daftar master; isinya disimpan utuh di `data_item` untuk dipulihkan.
    `VarianInfo` tetap ada, jadi ID tetap dan semua riwayat (PO, opname,
    transfer, nota manual, log) tidak ikut hilang.
    """
    varian = models.OneToOneField(VarianInfo, on_delete=models.CASCADE, related_name="sampah")
    waktu = models.DateTimeField(auto_now_add=True, db_index=True)
    oleh = models.CharField(max_length=150, blank=True, default="")
    data_item = models.JSONField()
    alasan = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "master_sampah"
        ordering = ["-waktu", "-id"]


class StokLokasi(models.Model):
    """Jumlah satu varian di satu gudang. Lokasi toko tidak punya baris di sini."""
    varian = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="stok_lokasi")
    lokasi = models.ForeignKey(Lokasi, on_delete=models.PROTECT, related_name="stok")
    jumlah = models.IntegerField(default=0)

    class Meta:
        db_table = "master_stok_lokasi"
        constraints = [models.UniqueConstraint(fields=["varian", "lokasi"], name="master_stok_lokasi_unik")]


class MutasiLokasi(models.Model):
    """Riwayat perpindahan stok antar lokasi. Total stok (kasir) tidak berubah."""
    SUMBER = [("pindah", "Pindah lokasi"), ("ambil", "Diambil untuk penjualan/transfer"), ("impor", "Import Excel")]

    waktu = models.DateTimeField(auto_now_add=True, db_index=True)
    pengguna = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    nama_pengguna = models.CharField(max_length=150, blank=True, default="")
    varian = models.ForeignKey(VarianInfo, null=True, on_delete=models.SET_NULL, related_name="mutasi_lokasi")
    item_name = models.CharField(max_length=255, db_index=True)
    variant = models.CharField(max_length=255)
    dari = models.ForeignKey(Lokasi, on_delete=models.PROTECT, related_name="mutasi_keluar")
    ke = models.ForeignKey(Lokasi, on_delete=models.PROTECT, related_name="mutasi_masuk")
    jumlah = models.IntegerField()
    sumber = models.CharField(max_length=20, choices=SUMBER, default="pindah")
    catatan = models.TextField(blank=True, default="")

    class Meta:
        db_table = "master_mutasi_lokasi"
        ordering = ["-waktu", "-id"]


class HakAkun(models.Model):
    """Daftar hak per akun, dicentang di halaman Pengguna. Superuser selalu punya semua hak."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hak_master")
    # Pemilik: semua hak, termasuk hak yang baru ditambahkan di versi berikutnya.
    semua = models.BooleanField(default=False)
    hak = models.JSONField(default=list)
    # Toko yang boleh dibuka akun ini (id). Kosong = semua toko.
    toko = models.JSONField(default=list, blank=True)
    diubah = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "master_hak_akun"


class PreferensiAkun(models.Model):
    """Pilihan tampilan per akun (warna tema)."""
    TEMA = [("oranye", "Oranye"), ("moka", "Biru (seperti Moka)")]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferensi_master")
    tema = models.CharField(max_length=20, choices=TEMA, default="oranye")
    # Toko yang terakhir dipilih di master; dibuka lagi saat masuk berikutnya.
    toko = models.ForeignKey("Toko", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # Susunan widget beranda: [{"k": kunci, "lebar": 1|2}, ...] urut tampil. Kosong = susunan bawaan.
    dasbor = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "master_preferensi_akun"


class HitungAcak(models.Model):
    """Hitung acak oleh pemilik: beberapa barang dipilih sistem secara acak, dihitung sendiri.

    Pengaman terhadap kerja sama: yang menghitung bukan yang menjaga, dan tidak ada
    yang tahu barang mana yang akan dicek.
    """
    STATUS = [("draf", "Sedang dihitung"), ("selesai", "Selesai")]

    waktu = models.DateTimeField(auto_now_add=True)
    oleh = models.CharField(max_length=150)
    lokasi = models.ForeignKey(Lokasi, on_delete=models.PROTECT, related_name="hitung_acak")
    status = models.CharField(max_length=10, choices=STATUS, default="draf")
    selesai_waktu = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "master_hitung_acak"
        ordering = ["-id"]


class HitungAcakBaris(models.Model):
    hitung_acak = models.ForeignKey(HitungAcak, on_delete=models.CASCADE, related_name="baris")
    varian = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="hitung_acak")
    hitung = models.IntegerField(null=True, blank=True)
    # Dibekukan saat selesai: stok sistem di lokasi itu.
    stok_sistem = models.IntegerField(null=True, blank=True)
    diterapkan = models.BooleanField(default=False)

    class Meta:
        db_table = "master_hitung_acak_baris"
        ordering = ["id"]

    @property
    def selisih(self):
        if self.hitung is None or self.stok_sistem is None:
            return None
        return self.hitung - self.stok_sistem


class Tiket(models.Model):
    """Izin sementara untuk admin mengajukan perubahan penempatan/stok per lokasi.

    Di luar tiket yang terbuka (dan sebelum batas waktunya), admin tidak bisa
    menginput apa pun. Semua isian baru berlaku setelah di-ACC pemilik.
    """
    STATUS = [("terbuka", "Terbuka"), ("dikirim", "Menunggu ACC"), ("selesai", "Selesai"),
              ("ditolak", "Ditolak"), ("batal", "Dibatalkan")]
    JENIS = [("lokasi", "Pindah lokasi dan rak"), ("barang", "Data barang"), ("hitung", "Hitung stok")]
    IZIN_BARANG = [("harga", "Harga jual dan floor"), ("alias", "Alias"), ("aktif", "Aktif / nonaktif")]

    jenis = models.CharField(max_length=10, choices=JENIS, default="lokasi")
    # Tiket data barang: apa saja yang boleh diusulkan (subset IZIN_BARANG) dan
    # barang mana saja ({"kategori": [...], "brand": [...]}, kosong = semua).
    izin = models.JSONField(default=list, blank=True)
    saring = models.JSONField(default=dict, blank=True)
    untuk = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tiket_master")
    tujuan = models.CharField(max_length=300)
    lingkup = models.CharField(max_length=300, blank=True, default="")
    dibuka_oleh = models.CharField(max_length=150)
    dibuka_waktu = models.DateTimeField(auto_now_add=True)
    batas = models.DateTimeField()
    status = models.CharField(max_length=10, choices=STATUS, default="terbuka", db_index=True)
    catatan_admin = models.CharField(max_length=500, blank=True, default="")
    dikirim_waktu = models.DateTimeField(null=True, blank=True)
    diputus_oleh = models.CharField(max_length=150, blank=True, default="")
    diputus_waktu = models.DateTimeField(null=True, blank=True)
    catatan_putusan = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "master_tiket"
        ordering = ["-id"]

    @property
    def lewat_batas(self):
        from django.utils import timezone
        return timezone.now() >= self.batas

    @property
    def bisa_diisi(self):
        return self.status == "terbuka" and not self.lewat_batas

    @property
    def bisa_diputus(self):
        return self.status == "dikirim" or (self.status == "terbuka" and self.lewat_batas)


class TiketBaris(models.Model):
    JENIS = [("pindah", "Pindah stok"), ("rak", "Ganti rak barang"), ("rak_lokasi", "Pindah rak ke lokasi")]

    tiket = models.ForeignKey(Tiket, on_delete=models.CASCADE, related_name="baris")
    jenis = models.CharField(max_length=12, choices=JENIS)
    varian = models.ForeignKey(VarianInfo, null=True, blank=True, on_delete=models.PROTECT, related_name="tiket")
    rak = models.ForeignKey(Rak, null=True, blank=True, on_delete=models.PROTECT, related_name="tiket")
    dari = models.ForeignKey(Lokasi, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    ke = models.ForeignKey(Lokasi, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    jumlah = models.IntegerField(null=True, blank=True)
    catatan = models.CharField(max_length=300, blank=True, default="")
    diterapkan = models.BooleanField(default=False)
    hasil = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "master_tiket_baris"
        ordering = ["id"]


class TiketUsulan(models.Model):
    """Usulan perubahan data barang di tiket jenis "barang".

    Satu baris = satu kolom satu barang. `lama` = nilai saat diusulkan, supaya
    pemilik tahu kalau nilainya sudah berubah lagi sebelum di-ACC. Alias berlaku
    per barang (varian kosong, variant = "*").
    """
    KOLOM = [("max_price", "Harga jual"), ("min_price", "Harga floor"), ("alias", "Alias"), ("aktif", "Aktif")]

    tiket = models.ForeignKey(Tiket, on_delete=models.CASCADE, related_name="usulan")
    varian = models.ForeignKey(VarianInfo, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    item_name = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    kolom = models.CharField(max_length=12, choices=KOLOM)
    lama = models.TextField(blank=True, default="")
    baru = models.TextField(blank=True, default="")
    diubah = models.DateTimeField(auto_now=True)
    diterapkan = models.BooleanField(default=False)
    hasil = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "master_tiket_usulan"
        ordering = ["item_name", "variant", "id"]
        constraints = [models.UniqueConstraint(fields=["tiket", "item_name", "variant", "kolom"],
                                               name="master_tiket_usulan_unik")]


class AkunKaryawan(models.Model):
    """Karyawan kasir (PIN) yang boleh masuk aplikasi operasional.

    Akun Django-nya dibuat sistem (ops-<id>, tanpa kata sandi). Masuknya hanya
    dengan PIN kasir, dari perangkat yang sudah disetujui pemilik.
    """
    worker_id = models.CharField(max_length=5, unique=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="akun_karyawan")
    peran = models.JSONField(default=list, blank=True)
    diubah = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "master_akun_karyawan"


class PerangkatOps(models.Model):
    """Perangkat (laptop/HP/tablet) yang boleh membuka aplikasi operasional.

    Perangkat baru otomatis terdaftar sebagai "menunggu" dengan kode pendek yang
    tampil di layarnya. Pemilik menyetujui kode itu di master. Sebelum disetujui,
    halaman masuk pun tidak tampil.
    """
    kunci = models.CharField(max_length=64, unique=True)
    kode = models.CharField(max_length=12, unique=True)
    nama = models.CharField(max_length=80, blank=True, default="")
    keterangan = models.CharField(max_length=200, blank=True, default="")
    disetujui = models.DateTimeField(null=True, blank=True)
    disetujui_oleh = models.CharField(max_length=150, blank=True, default="")
    dicabut = models.DateTimeField(null=True, blank=True)
    dibuat = models.DateTimeField(auto_now_add=True)
    terakhir = models.DateTimeField(null=True, blank=True)
    terakhir_akun = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        db_table = "master_perangkat_ops"
        ordering = ["-dibuat"]

    @property
    def status(self):
        if self.dicabut:
            return "dicabut"
        return "disetujui" if self.disetujui else "menunggu"


class PermintaanAmbil(models.Model):
    """Barang yang terjual/dikirim dari gudang dan harus diambilkan kepala gudang.

    Total stok (kasir) sudah berkurang saat nota/transfer dibuat. Stok gudang baru
    berkurang saat kepala gudang (atau pemilik) mengonfirmasi "sudah diambil".
    Selama menunggu, jumlahnya dihitung sebagai "dipesan" supaya stok Toko tidak
    terlihat minus palsu.
    """
    SUMBER = [("nota_manual", "Nota manual"), ("transfer", "Transfer"), ("kasir", "Kasir")]
    STATUS = [("menunggu", "Menunggu diambil"), ("diambil", "Sudah diambil"), ("batal", "Batal")]

    dibuat = models.DateTimeField(auto_now_add=True, db_index=True)
    sumber = models.CharField(max_length=12, choices=SUMBER)
    referensi = models.CharField(max_length=60)
    nota_manual = models.ForeignKey("NotaManual", null=True, blank=True, on_delete=models.CASCADE, related_name="permintaan")
    transfer = models.ForeignKey("Transfer", null=True, blank=True, on_delete=models.CASCADE, related_name="permintaan")
    varian = models.ForeignKey(VarianInfo, on_delete=models.PROTECT, related_name="permintaan_ambil")
    item_name = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    lokasi = models.ForeignKey(Lokasi, on_delete=models.PROTECT, related_name="permintaan_ambil")
    jumlah = models.IntegerField()
    status = models.CharField(max_length=10, choices=STATUS, default="menunggu", db_index=True)
    diambil_jumlah = models.IntegerField(null=True, blank=True)
    diambil_lokasi = models.ForeignKey(Lokasi, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    diambil_oleh = models.CharField(max_length=150, blank=True, default="")
    diambil_waktu = models.DateTimeField(null=True, blank=True)
    oleh_pemilik = models.BooleanField(default=False)
    catatan = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "master_permintaan_ambil"
        ordering = ["dibuat", "id"]

    @property
    def selisih(self):
        if self.diambil_jumlah is None:
            return 0
        return self.diambil_jumlah - self.jumlah


class PenerimaanRetur(models.Model):
    """Penerimaan barang retur/void dari kasir (event `refund_event` milik POS).

    POS hanya mencatat event retur tanpa mengubah stok (REFUND_DESIGN.md). Pemilik
    menerima barangnya di master dan menentukan barang ditaruh di mana; baru saat itu
    stok bertambah. Satu event hanya bisa diterima sekali.
    """
    refund_id = models.UUIDField(unique=True)
    sync_id = models.UUIDField(db_index=True)
    jenis = models.CharField(max_length=10)  # return | void
    waktu = models.DateTimeField(auto_now_add=True)
    pengguna = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    nama_pengguna = models.CharField(max_length=150, blank=True, default="")
    catatan = models.TextField(blank=True, default="")

    class Meta:
        db_table = "master_penerimaan_retur"
        ordering = ["-waktu", "-id"]


class PenerimaanReturBaris(models.Model):
    penerimaan = models.ForeignKey(PenerimaanRetur, on_delete=models.CASCADE, related_name="baris")
    varian = models.ForeignKey(VarianInfo, null=True, on_delete=models.SET_NULL)
    item_name = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    jumlah = models.IntegerField()           # jumlah di event retur
    masuk = models.IntegerField(default=0)   # yang kembali ke stok
    rusak = models.IntegerField(default=0)   # tidak kembali ke stok
    lokasi = models.ForeignKey(Lokasi, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        db_table = "master_penerimaan_retur_baris"


class PemasokBarang(models.Model):
    """Catatan "barang ini diambil dari mana / sama siapa", per barang (semua variannya).

    Melengkapi riwayat pembelian (PO) yang sudah tercatat otomatis: dipakai untuk barang
    yang belum pernah dibeli lewat master, atau untuk menyimpan catatan seperti nama sales,
    minimal order, atau harga patokan.
    """
    item_info = models.ForeignKey(ItemInfo, on_delete=models.CASCADE, related_name="pemasok")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="barang")
    catatan = models.CharField(max_length=300, blank=True, default="")
    dibuat_oleh = models.CharField(max_length=150, blank=True, default="")
    dibuat_waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "master_pemasok_barang"
        ordering = ["supplier__nama"]
        constraints = [models.UniqueConstraint(fields=["item_info", "supplier"], name="master_pemasok_barang_unik")]


class KontakKaryawan(models.Model):
    """Nomor WhatsApp karyawan kasir, untuk tombol "Kirim WA" saat ada PIN baru.

    Disimpan di master (tabel worker milik kasir tidak diubah). Format 62xxxxxxxxxx.
    """
    worker_id = models.CharField(max_length=5, unique=True)
    wa = models.CharField(max_length=20)
    diubah = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "master_kontak_karyawan"


class PinTampil(models.Model):
    """PIN karyawan dalam bentuk terbaca, hanya selama perlu dibagikan.

    Kasir hanya menyimpan hash. Supaya pemilik bisa membagikan PIN baru (termasuk
    yang dibuat otomatis saat ganti PIN terjadwal), PIN itu disimpan di sini
    sampai `hapus_setelah`, lalu dihapus otomatis. Setelah itu PIN yang lupa
    hanya bisa di-reset, tidak bisa dilihat lagi.
    """
    worker_pin_id = models.BigIntegerField(unique=True)
    worker_id = models.CharField(max_length=5)
    pin = models.CharField(max_length=6)
    berlaku_mulai = models.DateTimeField()
    hapus_setelah = models.DateTimeField()
    dibuat = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "master_pin_tampil"
        ordering = ["berlaku_mulai", "worker_id"]
