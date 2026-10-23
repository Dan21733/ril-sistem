"""Peran dan hak di master.

Hak diperiksa lewat nama peran (Group Django), bukan lewat permission bawaan,
supaya daftarnya terbaca di satu tempat.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

SEMUA_HAK = {
    "lihat_modal",
    "ubah_modal",
    "ubah_harga",
    "ubah_info",
    "ganti_nama",
    "kelola_rak",
    "impor",
    "lihat_log",
    "kelola_pengguna",
    "lihat_stok",
    "ubah_stok",
    "kelola_supplier",
    "pembelian",
    "pengaturan",
    "laporan",
    "opname",
    # "master" = boleh membuka aplikasi master. "gudang" = boleh membuka halaman kepala gudang.
    "master",
    "gudang",
    "transfer",
    "kelola_toko",
    "kelola_kasir",
    # Buang barang ke sampah, pulihkan, dan hapus permanen (dengan password).
    "hapus_barang",
    # Lokasi stok (toko/gudang): kelola daftar lokasi dan memindah stok antar lokasi. Hanya pemilik.
    "kelola_lokasi",
    # Aplikasi operasional (/gudang/). "gudang" = boleh membukanya; menu dibuka per hak di bawah.
    "gudang_datang",
    "gudang_hitung",
    "gudang_buka_dus",
    "gudang_ambil",
    "ops_lihat_stok",
    "ops_tiket_pindah",
    "ops_tiket_rak",
    "ops_barang_harga",
    "ops_barang_alias",
    "ops_barang_aktif",
}

# Hak yang hanya berlaku di aplikasi operasional, bukan di master.
HAK_OPERASIONAL = {"gudang", "gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil",
                   "ops_lihat_stok", "ops_tiket_pindah", "ops_tiket_rak", "ops_barang_harga", "ops_barang_alias",
                   "ops_barang_aktif"}
# Tugas (tiket) di aplikasi operasional. Satu hak = satu jenis usulan.
HAK_TIKET_LOKASI = {"ops_tiket_pindah", "ops_tiket_rak"}
# izin tiket data barang -> hak yang dibutuhkan admin
HAK_IZIN_BARANG = {"harga": "ops_barang_harga", "alias": "ops_barang_alias", "aktif": "ops_barang_aktif"}
HAK_TIKET_BARANG = set(HAK_IZIN_BARANG.values())
# Semua yang membuka halaman Tugas (beranda aplikasi operasional).
HAK_TUGAS = HAK_TIKET_LOKASI | HAK_TIKET_BARANG | {"gudang_hitung", "gudang_datang", "gudang_ambil", "gudang_buka_dus"}
# Hitung buta: yang menghitung tidak boleh bisa melihat stok sistem.
HAK_BENTROK = [({"gudang_hitung", "ops_lihat_stok"},
                "Hitung stok dan lihat jumlah stok tidak boleh untuk orang yang sama: hitungannya bisa disalin dari angka sistem.")]

# Label untuk halaman Pengguna, dikelompokkan. Urutan = urutan tampil.
LABEL_HAK = [
    ("Master (pemilik)", [
        ("master", "Membuka master"),
        ("lihat_modal", "Melihat modal"),
        ("ubah_modal", "Mengubah modal"),
        ("ubah_harga", "Mengubah harga jual dan floor"),
        ("ubah_info", "Mengubah data barang, tambah barang"),
        ("ganti_nama", "Ganti nama barang"),
        ("hapus_barang", "Hapus barang (keranjang sampah)"),
        ("kelola_rak", "Kelola rak"),
        ("kelola_lokasi", "Kelola lokasi, pindah stok, buka dan ACC tiket admin"),
        ("impor", "Import Excel"),
        ("lihat_stok", "Melihat stok"),
        ("ubah_stok", "Mengubah stok, buka dus"),
        ("opname", "Opname (ACC)"),
        ("pembelian", "Pembelian, faktur, hutang"),
        ("kelola_supplier", "Kelola supplier"),
        ("transfer", "Transfer ke toko lain"),
        ("laporan", "Nota dan laporan"),
        ("lihat_log", "Melihat log"),
        ("pengaturan", "Pengaturan"),
        ("kelola_toko", "Kelola toko"),
        ("kelola_kasir", "Kasir: karyawan, PIN, tablet, aturan kasir"),
        ("kelola_pengguna", "Kelola pengguna dan hak"),
    ]),
    ("Aplikasi operasional (/gudang/)", [
        ("gudang", "Membuka aplikasi operasional"),
        ("gudang_datang", "Catat barang datang"),
        ("gudang_hitung", "Hitung stok lewat tugas (opname buta)"),
        ("gudang_buka_dus", "Buka dus"),
        ("gudang_ambil", "Konfirmasi barang diambil dari gudang"),
        ("ops_lihat_stok", "Lihat jumlah stok di tugas pindah (tanpa harga)"),
        ("ops_tiket_pindah", "Tugas: usul pindah stok antar lokasi"),
        ("ops_tiket_rak", "Tugas: usul ganti/pindah rak"),
        ("ops_barang_harga", "Tugas: usul harga jual dan floor"),
        ("ops_barang_alias", "Tugas: usul alias"),
        ("ops_barang_aktif", "Tugas: usul aktif/nonaktif barang"),
    ]),
]

PERAN = {
    "Super Admin": set(SEMUA_HAK),
    # Finance mengisi harga, tetapi tidak pernah melihat modal.
    "Finance": {"master", "ubah_harga", "ubah_info", "lihat_log", "lihat_stok"},
    # Kepala gudang tidak masuk ke master sama sekali. Ia punya halaman sendiri di
    # /gudang/: catat barang datang, hitung stok, buka dus. Tanpa harga dan modal.
    "Gudang": {"gudang", "gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil"},
}

# Hak karyawan (masuk dengan PIN kasir) di aplikasi operasional, dicentang satu-satu di
# Kasir > Karyawan & PIN. "gudang" (boleh membuka aplikasinya) ikut otomatis kalau ada satu saja.
HAK_KARYAWAN_OPS = [
    ("Gudang", [
        ("gudang_ambil", "Ambil barang"),
        ("gudang_datang", "Barang datang"),
        ("gudang_hitung", "Hitung stok (lewat tugas)"),
        ("gudang_buka_dus", "Buka dus"),
    ]),
    ("Tugas lokasi", [
        ("ops_tiket_pindah", "Usul pindah stok antar lokasi"),
        ("ops_tiket_rak", "Usul ganti/pindah rak"),
        ("ops_lihat_stok", "Lihat jumlah stok di tugas pindah"),
    ]),
    ("Tugas data barang", [
        ("ops_barang_harga", "Usul harga jual dan floor"),
        ("ops_barang_alias", "Usul alias"),
        ("ops_barang_aktif", "Usul aktif/nonaktif"),
    ]),
]
LABEL_HAK_OPS = {k: label for _, isi in HAK_KARYAWAN_OPS for k, label in isi}

# Tombol isi cepat (hanya mencentang; tetap bisa dibuang satu-satu).
PAKET_OPS = {
    "kepala_gudang": ("Kepala gudang", {"gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil"}),
    "admin_gudang": ("Admin gudang", {"ops_tiket_pindah", "ops_tiket_rak", "ops_lihat_stok"}),
    "admin_barang": ("Admin barang", set(HAK_TIKET_BARANG)),
}


def hak_pengguna(user):
    if not user.is_authenticated or not user.is_active:
        return set()
    if user.is_superuser:
        return set(SEMUA_HAK)
    # Hak per akun (dicentang di halaman Pengguna). Akun lama yang belum punya
    # daftar hak memakai peran (Group) sebagai templat.
    cache = getattr(user, "_hak_master", None)
    if cache is not None:
        return set(cache)
    from master.models import HakAkun
    baris = HakAkun.objects.filter(user=user).first()
    if baris is not None:
        hasil = set(SEMUA_HAK) if baris.semua else set(baris.hak) & SEMUA_HAK
    else:
        hasil = set()
        for nama in user.groups.values_list("name", flat=True):
            hasil |= PERAN.get(nama, set())
    user._hak_master = frozenset(hasil)
    return hasil


def boleh(user, hak):
    return hak in hak_pengguna(user)


def perlu(hak=None):
    """Decorator: wajib login, dan (kalau diisi) wajib punya hak tertentu."""

    def bungkus(view):
        @wraps(view)
        @login_required
        def dalam(request, *args, **kwargs):
            punya = hak_pengguna(request.user)
            perlu_hak = hak or "master"
            if perlu_hak in HAK_OPERASIONAL:
                if "gudang" not in punya or perlu_hak not in punya:
                    raise PermissionDenied("Akun ini tidak punya hak untuk halaman tersebut.")
                return view(request, *args, **kwargs)
            if "master" not in punya:
                # Akun gudang yang membuka master diarahkan ke halamannya sendiri.
                if "gudang" in punya and request.method == "GET":
                    return redirect("gudang:beranda")
                raise PermissionDenied("Akun ini tidak punya hak untuk halaman tersebut.")
            if perlu_hak not in punya:
                raise PermissionDenied("Akun ini tidak punya hak untuk halaman tersebut.")
            return view(request, *args, **kwargs)

        return dalam

    return bungkus


def konteks(request):
    user = getattr(request, "user", None)
    if user is None:
        return {"hak": set()}
    from django.utils.functional import SimpleLazyObject

    def _toko():
        from master.layanan.toko import boleh_untuk
        return boleh_untuk(user) if getattr(user, "is_authenticated", False) else []

    def _toko_aktif():
        from master.layanan.toko import toko_aktif
        return toko_aktif(request) if getattr(user, "is_authenticated", False) else None

    def _toko_gudang():
        from master.layanan.toko import toko_gudang
        return toko_gudang(request) if getattr(user, "is_authenticated", False) else None

    def _toko_stok():
        from master.layanan.toko import utama
        return utama()

    daftar = SimpleLazyObject(_toko)
    # banyak_toko: pilihan toko hanya ditampilkan kalau toko aktif lebih dari satu.
    def _ambil():
        from master.models import PermintaanAmbil
        return PermintaanAmbil.objects.filter(status="menunggu").count()

    def _retur():
        if "ubah_stok" not in hak_pengguna(user) and "laporan" not in hak_pengguna(user):
            return 0
        from master.layanan.retur_jual import jumlah_menunggu
        return jumlah_menunggu()

    def _tinjau():
        if "kelola_kasir" not in hak_pengguna(user):
            return 0
        from master.layanan.nota_tinjau import jumlah_menunggu as tinjau_menunggu
        return tinjau_menunggu()

    def _tema():
        if not getattr(user, "is_authenticated", False):
            return "oranye"
        from master.models import PreferensiAkun
        p = PreferensiAkun.objects.filter(user=user).values_list("tema", flat=True).first()
        return p or "oranye"

    return {"hak": hak_pengguna(user), "daftar_toko": daftar, "tema": SimpleLazyObject(_tema),
            "toko_aktif": SimpleLazyObject(_toko_aktif), "toko_stok": SimpleLazyObject(_toko_stok),
            "toko_gudang": SimpleLazyObject(_toko_gudang),
            "banyak_toko": SimpleLazyObject(lambda: len(daftar) > 1),
            "jumlah_ambil": SimpleLazyObject(_ambil), "jumlah_retur": SimpleLazyObject(_retur),
            "jumlah_tinjau": SimpleLazyObject(_tinjau)}
