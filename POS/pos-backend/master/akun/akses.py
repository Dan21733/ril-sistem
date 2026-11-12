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
    # DP & piutang pelanggan: lihat, catat pembayaran, tandai barang diambil. ACC = setujui invoice / titip barang.
    "piutang",
    "acc_piutang",
    # Kas laci: buka/tutup kas, uang keluar, ACC tutup kas (melihat angka seharusnya & selisih).
    "kas",
    # Biaya toko (pengeluaran): catat & lihat; ACC uang keluar yang dicatat kasir.
    "biaya",
    "acc_biaya",
    # Gaji karyawan: atur gaji, tandai sudah dibayar. Hanya pemilik.
    "gaji",
    # Aplikasi operasional (/gudang/). "gudang" = boleh membukanya; menu dibuka per hak di bawah.
    "gudang_datang",
    # PIC gudang (12 Nov 2026): boleh memegang lokasi/rak, punya tim, dan ditugaskan menerima barang datang.
    "pic",
    "gudang_hitung",
    "gudang_buka_dus",
    "gudang_ambil",
    "ops_tiket_pindah",
    "ops_tiket_rak",
    "ops_barang_harga",
    "ops_barang_alias",
    "ops_barang_aktif",
}

# Hak yang hanya berlaku di aplikasi operasional, bukan di master.
HAK_OPERASIONAL = {"gudang", "gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil",
                   "ops_tiket_pindah", "ops_tiket_rak", "ops_barang_harga", "ops_barang_alias",
                   "ops_barang_aktif"}
# Tugas (tiket) di aplikasi operasional. Satu hak = satu jenis usulan.
HAK_TIKET_LOKASI = {"ops_tiket_pindah", "ops_tiket_rak"}
# izin tiket data barang -> hak yang dibutuhkan admin
HAK_IZIN_BARANG = {"harga": "ops_barang_harga", "alias": "ops_barang_alias", "aktif": "ops_barang_aktif"}
HAK_TIKET_BARANG = set(HAK_IZIN_BARANG.values())
# Semua yang membuka halaman Tugas (beranda aplikasi operasional).
HAK_TUGAS = HAK_TIKET_LOKASI | HAK_TIKET_BARANG | {"gudang_hitung", "gudang_datang", "gudang_ambil", "gudang_buka_dus"}
# Penyederhanaan 9 Nov 2026 (keputusan pemilik): karyawan cukup satu centang "aplikasi gudang".
# Semua isinya menunggu ACC, dan pekerjaan baru muncul kalau DITUGASKAN (tugas dari pemilik, barang
# datang ke penerima PO). Jadi akun "gudang" otomatis memegang semua hak operasional di bawah.
# Melihat jumlah stok di aplikasi gudang dihapus sama sekali (hitung buta).
HAK_GUDANG_ISI = {"gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil"} | HAK_TIKET_LOKASI | HAK_TIKET_BARANG
HAK_BENTROK = []

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
        ("piutang", "Piutang pelanggan: catat pembayaran, barang diambil"),
        ("acc_piutang", "ACC invoice (ngutang) dan titip barang"),
        ("kas", "Kas laci: buka/tutup kas, uang keluar, ACC tutup kas"),
        ("biaya", "Biaya toko: catat dan lihat pengeluaran, kelola kategori"),
        ("acc_biaya", "ACC uang keluar yang dicatat kasir"),
        ("gaji", "Gaji karyawan: atur gaji, tandai sudah dibayar"),
        ("lihat_log", "Melihat log"),
        ("pengaturan", "Pengaturan"),
        ("kelola_toko", "Kelola toko"),
        ("kelola_kasir", "Kasir: karyawan, PIN, tablet, aturan kasir"),
        ("kelola_pengguna", "Kelola pengguna dan hak"),
    ]),
    ("Aplikasi operasional (/gudang/)", [
        ("gudang", "Pakai aplikasi gudang (ambil, barang datang yang ditugaskan, buka dus, tugas dari pemilik)"),
        ("pic", "PIC gudang: pegang lokasi/rak, punya tim, menerima barang datang"),
    ]),
]

PERAN = {
    "Super Admin": set(SEMUA_HAK),
    # Finance mengisi harga, tetapi tidak pernah melihat modal.
    "Finance": {"master", "ubah_harga", "ubah_info", "lihat_log", "lihat_stok"},
    # Kepala gudang tidak masuk ke master sama sekali. Ia punya halaman sendiri di
    # /gudang/: catat barang datang, hitung stok, buka dus. Tanpa harga dan modal.
    "Gudang": {"gudang", "gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil", "pic"},
}

# Hak karyawan (masuk dengan PIN kasir) di aplikasi operasional, dicentang satu-satu di
# Karyawan > Karyawan & hak akses. "gudang" (boleh membuka aplikasinya) ikut otomatis kalau ada satu saja.
HAK_KARYAWAN_OPS = [
    ("Gudang", [
        ("gudang", "Pakai aplikasi gudang: ambil barang, barang datang yang ditugaskan ke dia, buka dus, "
                   "dan tugas dari pemilik. Semua hasilnya menunggu ACC; jumlah stok tidak pernah terlihat."),
        ("pic", "PIC gudang: bisa dipilih memegang lokasi/rak, punya tim karyawan lapangan, dan menerima "
                "barang datang. Ikut menyalakan aplikasi gudang."),
    ]),
]
LABEL_HAK_OPS = {k: label for _, isi in HAK_KARYAWAN_OPS for k, label in isi}

# Tombol isi cepat (hanya mencentang; tetap bisa dibuang satu-satu).
PAKET_OPS = {}


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
    if "gudang" in hasil:
        hasil |= HAK_GUDANG_ISI
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


def _sekali(fungsi):
    """Hitungan menu dipakai dua kali per halaman (lencana menu + jumlah_penjualan): cukup satu query."""
    hasil = []

    def dalam():
        if not hasil:
            hasil.append(fungsi())
        return hasil[0]
    return dalam


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

    @_sekali
    def _retur():
        if "ubah_stok" not in hak_pengguna(user) and "laporan" not in hak_pengguna(user):
            return 0
        from master.layanan.retur_jual import jumlah_menunggu
        return jumlah_menunggu()

    @_sekali
    def _tinjau():
        if "kelola_kasir" not in hak_pengguna(user):
            return 0
        from master.layanan.nota_tinjau import jumlah_menunggu as tinjau_menunggu
        return tinjau_menunggu()

    def _tugas_acc():
        if "kelola_lokasi" not in hak_pengguna(user):
            return 0
        from master.models import Tiket
        return Tiket.objects.filter(status="dikirim").count()

    def _tema():
        if not getattr(user, "is_authenticated", False):
            return "oranye"
        from master.models import PreferensiAkun
        p = PreferensiAkun.objects.filter(user=user).values_list("tema", flat=True).first()
        return p or "oranye"

    def _ada_titipan():
        from master.models import Toko
        return Toko.objects.filter(aktif=True, titip_di__isnull=False).exists()

    @_sekali
    def _piutang_acc():
        if "acc_piutang" not in hak_pengguna(user):
            return 0
        from master.models import Piutang
        return Piutang.objects.filter(batal=False, acc_status="menunggu").count()

    @_sekali
    def _kas_acc():
        if "kas" not in hak_pengguna(user):
            return 0
        from master.layanan.kas import jumlah_menunggu
        return jumlah_menunggu()

    @_sekali
    def _biaya_acc():
        if "acc_biaya" not in hak_pengguna(user):
            return 0
        from master.layanan.biaya import jumlah_menunggu
        return jumlah_menunggu()

    hak = hak_pengguna(user)
    return {"hak": hak, "hak_tugas": bool(hak & HAK_TUGAS), "daftar_toko": daftar, "tema": SimpleLazyObject(_tema),
            "toko_aktif": SimpleLazyObject(_toko_aktif), "toko_stok": SimpleLazyObject(_toko_stok),
            "toko_gudang": SimpleLazyObject(_toko_gudang),
            "banyak_toko": SimpleLazyObject(lambda: len(daftar) > 1),
            "jumlah_ambil": SimpleLazyObject(_ambil), "jumlah_retur": SimpleLazyObject(_retur),
            "jumlah_tinjau": SimpleLazyObject(_tinjau), "jumlah_tugas_acc": SimpleLazyObject(_tugas_acc),
            "jumlah_piutang_acc": SimpleLazyObject(_piutang_acc),
            "jumlah_kas_acc": SimpleLazyObject(_kas_acc), "jumlah_biaya_acc": SimpleLazyObject(_biaya_acc),
            "jumlah_penjualan": SimpleLazyObject(lambda: str(int(str(_retur())) + int(str(_tinjau())) + int(str(_piutang_acc()))
                                                             + int(str(_kas_acc())) + int(str(_biaya_acc())))),
            "ada_titipan": SimpleLazyObject(_ada_titipan)}
