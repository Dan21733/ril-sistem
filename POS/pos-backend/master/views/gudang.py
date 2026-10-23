"""Halaman kepala gudang (/gudang/).

Aturan yang dipegang di sini: tidak ada harga, modal, stok sistem, atau nama
supplier yang dikirim ke halaman mana pun. Yang tampil hanya nomor pesanan,
nama barang, rak, dan jumlah yang dipesan atau yang ia isi sendiri.
"""
from django.contrib import messages
from django.db.models import Count, Q
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from pos.models import Item

from master.layanan import bukadus
from master.layanan import inti as layanan
from master.layanan import opname
from master.layanan import pembelian as svc
from master.akun.akses import perlu
from master.layanan.inti import Tolak
from master.models import BukaDus, ItemInfo, Konversi, Opname, Pembelian, Rak, Tiket, VarianInfo
from master.views.utama import kunci_alami

BATAS_HASIL = 80


def _toko(request):
    """Toko yang sedang dikerjakan staf. Hanya toko yang diizinkan untuk akunnya (menu Pengguna)."""
    from master.layanan.toko import toko_gudang
    t = toko_gudang(request)
    if t is None:
        raise PermissionDenied("Akun ini belum diberi akses ke toko mana pun.")
    return t


def _hanya_toko_stok(request):
    """Menu stok per lokasi, tiket, dan ambil barang hanya untuk toko yang memegang stok kasir."""
    from master.layanan.toko import utama
    t, u = _toko(request), utama()
    if u is None or t.pk != u.pk:
        messages.info(request, f"Menu ini hanya untuk {getattr(u, 'nama', 'toko yang memegang stok kasir')}.")
        return redirect("gudang:beranda")
    return None


def _lokasi(request, toko):
    """Lokasi yang sedang dihitung (hanya toko yang memegang stok kasir). Dipilih lewat ?lokasi=, diingat di sesi."""
    from master.layanan import lokasi as svc_lokasi
    if not toko.stok_di_kasir:
        return None
    if request.GET.get("lokasi"):
        try:
            request.session["gudang_lokasi"] = svc_lokasi.baca(request.GET["lokasi"]).pk
        except Tolak:
            pass
    try:
        return svc_lokasi.baca(request.session.get("gudang_lokasi"))
    except Tolak:
        return svc_lokasi.lokasi_toko()


from master.akun.akses import HAK_TIKET_BARANG, HAK_TIKET_LOKASI, HAK_TUGAS

HAK_KEPALA_GUDANG = {"gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil", "ops_lihat_stok"}


@perlu("gudang")
def beranda(request):
    from master.akun.akses import hak_pengguna
    hak = hak_pengguna(request.user)
    if not hak & HAK_KEPALA_GUDANG:
        # Admin: tidak ada menu. Yang tampil hanya tugas (tiket) yang sedang dibuka untuknya.
        if hak & HAK_TUGAS:
            return redirect("gudang:tiket")
        return render(request, "gudang/kosong.html", {"menu": "beranda"})
    toko = _toko(request)
    op = opname.draf(request.user, buat=False, toko=toko)
    return render(request, "gudang/beranda.html", {
        "menu": "beranda", "toko": toko,
        "jumlah_pesanan": Pembelian.objects.filter(status="dibuat", toko=toko).annotate(n=Count("baris")).filter(n__gt=0).count(),
        "jumlah_draf": op.baris.count() if op else 0,
        "jumlah_pasangan": Konversi.objects.filter(aktif=True).count(),
        "punya_tugas": bool(hak & HAK_TUGAS),
        "tiket_terbuka": [t for t in Tiket.objects.filter(
            untuk=request.user, status="terbuka") if t.bisa_diisi],
        "riwayat": Opname.objects.filter(dibuat_oleh=request.user.username, toko=toko).exclude(status="draf")
                         .annotate(n=Count("baris"))[:6],
    })


@perlu("gudang")
def panduan(request):
    """Cara kerja tiap menu (SOP), hanya bagian yang sesuai hak akun ini. Halaman lain dibuat tanpa penjelasan panjang."""
    return render(request, "gudang/panduan.html", {"menu": "panduan"})


@perlu("gudang_datang")
def datang(request):
    toko = _toko(request)
    pesanan = list(Pembelian.objects.filter(status="dibuat", toko=toko).prefetch_related("baris")
                   .annotate(n=Count("baris")).filter(n__gt=0).order_by("tanggal_pesan", "id"))
    for p in pesanan:
        # Nama supplier dirahasiakan dari gudang: pesanan dikenali dari nomor dan isinya.
        nama = list(dict.fromkeys(b.item_name for b in p.baris.all()))
        p.ringkas = ", ".join(nama[:3]) + (f", dan {len(nama) - 3} barang lain" if len(nama) > 3 else "")
    return render(request, "gudang/datang.html", {
        "menu": "datang", "pesanan": pesanan, "toko": toko,
        "menunggu": Pembelian.objects.filter(status="menunggu", diterima_oleh=request.user.username, toko=toko)
                             .order_by("-tanggal_terima")[:10],
    })


@perlu("gudang_datang")
def datang_detail(request, po_id):
    po = get_object_or_404(Pembelian, pk=po_id, status="dibuat")
    if request.method == "POST":
        try:
            svc.catat_datang_gudang(request.user, po, request.POST)
            messages.success(request, f"Barang datang untuk {po.nomor} sudah dicatat. Menunggu ACC pemilik.")
            return redirect("gudang:datang")
        except Tolak as tolak:
            messages.error(request, str(tolak))
    from master.layanan import lokasi as svc_lokasi
    baris = list(po.baris.select_related("varian__rak__tempat"))
    for b in baris:
        b.isi = request.POST.get(f"terima_{b.id}", "") if request.method == "POST" else ""
        # Usulan lokasi: dari isian sebelumnya, atau lokasi rak barangnya.
        rak = b.varian.rak
        b.usul = request.POST.get(f"lokasi_{b.id}") or (str(rak.tempat_id) if rak and rak.tempat_id else "")
    return render(request, "gudang/datang_detail.html", {
        "menu": "datang", "po": po, "baris": baris, "hari_ini": timezone.localdate(), "kirim": request.POST,
        "pilihan_lokasi": svc_lokasi.pilihan() if svc.stok_ikut(po) else [],
    })


def _cari_varian(cari, rak, lokasi=None):
    """Varian untuk dihitung: dicari lewat nama, varian, brand, alias, atau dibatasi rak."""
    qs = VarianInfo.objects.filter(aktif=True, sampah__isnull=True).select_related("rak")
    if rak:
        qs = qs.filter(rak_id=rak)
    elif lokasi is not None and not cari and lokasi.jenis == "gudang":
        # Tanpa kata kunci: tampilkan barang yang raknya ada di lokasi ini.
        qs = qs.filter(rak__tempat=lokasi)
    for kata in cari.split(" "):
        if not kata:
            continue
        qs = qs.filter(
            Q(item_name__icontains=kata) | Q(variant__icontains=kata) | Q(alias_tambahan__icontains=kata)
            | Q(item_name__in=ItemInfo.objects.filter(alias__icontains=kata).values("item_name"))
            | Q(item_name__in=Item.objects.filter(brand__icontains=kata).values("item_name"))
        )
    hasil = list(qs[:BATAS_HASIL + 1])
    terpotong = len(hasil) > BATAS_HASIL
    hasil = hasil[:BATAS_HASIL]
    hasil.sort(key=lambda v: (kunci_alami(v.item_name), kunci_alami(v.variant)))
    return hasil, terpotong


@perlu("gudang_hitung")
def hitung(request):
    from master.layanan import lokasi as svc_lokasi
    toko = _toko(request)
    lokasi = _lokasi(request, toko)
    cari = layanan.rapikan_teks(request.GET.get("q"))
    rak = request.GET.get("rak", "")
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "simpan":
                n = opname.simpan_hitungan(request.user, request.POST, toko=toko, lokasi=lokasi)
                messages.success(request, f"{n} hitungan tersimpan.")
            elif aksi == "hapus":
                opname.hapus_baris(request.user, request.POST.get("id"))
            elif aksi == "ajukan":
                op = opname.ajukan(request.user, request.POST.get("catatan"), toko=toko, lokasi=lokasi)
                messages.success(request, f"Hitungan #{op.id} sudah diajukan. Stok berubah setelah pemilik ACC.")
                return redirect("gudang:beranda")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect(request.get_full_path())

    op = opname.draf(request.user, buat=False, toko=toko, lokasi=lokasi)
    sudah = list(op.baris.select_related("varian__rak")) if op else []
    terisi = {b.varian_id: b for b in sudah}
    hasil, terpotong = ([], False)
    lihat_lokasi = request.GET.get("semua") == "1" and lokasi is not None and lokasi.jenis == "gudang"
    mencari = len(cari) >= 2 or bool(rak) or lihat_lokasi
    if mencari:
        hasil, terpotong = _cari_varian(cari, rak, lokasi if lihat_lokasi else None)
        for v in hasil:
            v.terisi = terisi.get(v.id)
    daftar_rak = Rak.objects.all()
    if lokasi is not None and lokasi.jenis == "gudang":
        daftar_rak = daftar_rak.filter(tempat=lokasi)
    return render(request, "gudang/hitung.html", {
        "menu": "hitung", "toko": toko, "cari": cari, "rak": rak, "daftar_rak": daftar_rak,
        "hasil": hasil, "terpotong": terpotong, "sudah": sudah, "mencari": mencari,
        "lokasi": lokasi, "pilihan_lokasi": svc_lokasi.pilihan() if toko.stok_di_kasir else [],
    })


@perlu("gudang_buka_dus")
def buka_dus(request):
    toko = _toko(request)
    if not toko.stok_di_kasir:
        return render(request, "gudang/buka_dus.html", {"menu": "buka_dus", "toko": toko, "tanpa_stok": True})
    if request.method == "POST":
        try:
            k = get_object_or_404(Konversi.objects.select_related("dari", "ke"), pk=request.POST.get("id"), aktif=True)
            from master.layanan import lokasi as svc_lokasi
            hasil = bukadus.buka(request.user, k, jumlah=request.POST.get("jumlah"),
                                 isi_total=request.POST.get("isi_total"), catatan=request.POST.get("catatan"),
                                 lokasi_dari=svc_lokasi.baca(request.POST.get("lokasi_dari")),
                                 lokasi_ke=svc_lokasi.baca(request.POST.get("lokasi_ke")))
            messages.success(request, f"{hasil.jumlah} {k.dari.item_name} dibuka menjadi {hasil.isi_total} {k.ke.item_name}.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("gudang:buka_dus")
    from master.layanan import lokasi as svc_lokasi
    daftar = list(Konversi.objects.filter(aktif=True).select_related("dari__rak", "ke__rak"))
    for k in daftar:
        k.usul_dari = k.dari.rak.tempat_id if k.dari.rak_id and k.dari.rak else None
        k.usul_ke = k.ke.rak.tempat_id if k.ke.rak_id and k.ke.rak else None
    return render(request, "gudang/buka_dus.html", {
        "menu": "buka_dus", "toko": toko, "pilihan_lokasi": svc_lokasi.pilihan(),
        "daftar": daftar,
        "riwayat": BukaDus.objects.filter(oleh=request.user.username).select_related("konversi__dari", "konversi__ke")[:10],
    })


# ---------------------------------------------------------------------------
# Admin: stok per lokasi (tanpa harga) dan tiket pindah lokasi
# ---------------------------------------------------------------------------


def _baris_stok(varian):
    """Jumlah per lokasi untuk daftar varian: Toko dihitung, gudang dari StokLokasi. Tanpa harga/modal."""
    from master.layanan import lokasi as svc_lokasi
    gudang = svc_lokasi.gudang_aktif()
    total = {(n, v): s for n, v, s in Item.objects.filter(item_name__in={x.item_name for x in varian})
             .values_list("item_name", "variant", "in_stock")}
    per = svc_lokasi.jumlah_gudang(x.id for x in varian)
    from master.layanan.ambil import dipesan
    pesan = dipesan(x.id for x in varian)
    for x in varian:
        jml = per.get(x.id, {})
        x.kolom = [jml.get(l.id, 0) for l in gudang]
        x.per = [(l.nama, jml.get(l.id, 0)) for l in gudang]
        x.toko = (total.get((x.item_name, x.variant)) or 0) - sum(jml.values()) + pesan.get(x.id, 0)
    return gudang


@perlu("ops_lihat_stok")
def stok_lokasi(request):
    tolak = _hanya_toko_stok(request)
    if tolak:
        return tolak
    from master.layanan import lokasi as svc_lokasi
    cari = layanan.rapikan_teks(request.GET.get("q"))
    rak = request.GET.get("rak", "")
    hasil, terpotong = ([], False)
    if len(cari) >= 2 or rak:
        hasil, terpotong = _cari_varian(cari, rak)
    gudang = _baris_stok(hasil)
    return render(request, "gudang/stok_lokasi.html", {
        "menu": "stok", "cari": cari, "rak": rak, "daftar_rak": Rak.objects.select_related("tempat"),
        "hasil": hasil, "terpotong": terpotong, "gudang": gudang, "toko_display": svc_lokasi.lokasi_toko(),
    })


@perlu("gudang")
def tiket(request):
    """Tugas: semua tiket untuk akun ini. Yang masih bisa dikerjakan di atas, dengan sisa waktunya."""
    from master.akun.akses import hak_pengguna
    from master.layanan import tiket_barang
    from master.models import Tiket
    if not hak_pengguna(request.user) & HAK_TUGAS:
        raise PermissionDenied("Akun ini tidak punya hak untuk halaman tersebut.")
    semua = list(Tiket.objects.filter(untuk=request.user).prefetch_related("baris", "usulan")[:40])
    sekarang = timezone.now()
    for t in semua:
        t.jumlah_usulan = len(t.usulan.all()) if t.jenis == "barang" else len(t.baris.all())
        t.sisa_menit = max(0, int((t.batas - sekarang).total_seconds() // 60))
        if t.jenis == "barang":
            t.izin_teks, t.lingkup_teks = tiket_barang.izin_teks(t), tiket_barang.lingkup_teks(t)
    return render(request, "gudang/tiket.html", {
        "menu": "tiket", "aktif": [t for t in semua if t.bisa_diisi],
        "lain": [t for t in semua if not t.bisa_diisi][:20],
    })


@perlu("gudang")
def tiket_barang(request, tiket_id):
    """Isian tiket data barang. Hanya harga jual, floor, alias, aktif; tanpa modal, stok, supplier."""
    from master.akun.akses import hak_pengguna
    from master.layanan import tiket_barang as svc_barang
    if not hak_pengguna(request.user) & HAK_TIKET_BARANG:
        raise PermissionDenied("Akun ini tidak punya hak untuk halaman tersebut.")
    t = get_object_or_404(Tiket, pk=tiket_id, untuk=request.user, jenis="barang")
    izin = svc_barang.izin_efektif(request.user, t)
    cari = layanan.rapikan_teks(request.GET.get("q"))
    try:
        nomor = int(request.GET.get("hal") or 1)
    except ValueError:
        nomor = 1
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "simpan":
                n, galat = svc_barang.simpan(request.user, t, request.POST)
                for g in galat[:8]:
                    messages.error(request, g)
                if len(galat) > 8:
                    messages.error(request, f"... dan {len(galat) - 8} baris lain tidak tersimpan.")
                messages.success(request, f"Tersimpan. {t.usulan.count()} usulan di tiket ini.")
            elif aksi == "kosongkan":
                svc_barang.hapus_semua(request.user, t)
                messages.info(request, "Semua usulan di tiket ini dihapus.")
            elif aksi == "kirim":
                n = svc_barang.kirim(request.user, t, request.POST.get("catatan", ""))
                messages.success(request, f"Tiket #{t.id} dikirim ({n} usulan). Tunggu ACC pemilik.")
                return redirect("gudang:tiket")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect(request.get_full_path())
    kelompok, nomor, jumlah_halaman, total = svc_barang.halaman(t, cari, nomor, izin) if t.bisa_diisi and izin else ([], 1, 1, 0)
    return render(request, "gudang/tiket_barang.html", {
        "menu": "tiket", "t": t, "cari": cari, "kelompok": kelompok, "nomor": nomor,
        "jumlah_halaman": jumlah_halaman, "total": total, "jumlah_usulan": t.usulan.count(),
        "izin_teks": svc_barang.izin_teks(t), "lingkup_teks": svc_barang.lingkup_teks(t),
        "boleh_harga": "harga" in izin, "boleh_alias": "alias" in izin, "boleh_aktif": "aktif" in izin,
        "tanpa_hak": t.bisa_diisi and not izin,
        "usulan": t.usulan.all() if not t.bisa_diisi else [],
    })


@perlu("gudang")
def tiket_isi(request, tiket_id):
    tolak = _hanya_toko_stok(request)
    if tolak:
        return tolak
    from master.layanan import lokasi as svc_lokasi
    from master.layanan import tiket as svc_tiket
    from master.akun.akses import hak_pengguna
    from master.models import Tiket
    hak = hak_pengguna(request.user)
    if not hak & HAK_TUGAS:
        raise PermissionDenied("Akun ini tidak punya hak untuk halaman tersebut.")
    t = get_object_or_404(Tiket, pk=tiket_id, untuk=request.user)
    if t.jenis == "barang":
        return redirect("gudang:tiket_barang", t.id)
    if not hak & HAK_TIKET_LOKASI:
        raise PermissionDenied("Akun ini tidak punya hak untuk halaman tersebut.")
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        p = request.POST
        try:
            if aksi == "pindah":
                svc_tiket.tambah_pindah(request.user, t, varian_id=p.get("varian_id"), dari=p.get("dari"),
                                        ke=p.get("ke"), jumlah=p.get("jumlah"), catatan=p.get("catatan", ""))
                messages.success(request, "Usulan ditambahkan.")
            elif aksi == "massal":
                n = svc_tiket.tambah_massal(request.user, t, dari=p.get("dari"), ke=p.get("ke"), rak=p.get("rak") or None,
                                            kategori=p.get("kategori") or None, ikut_rak=p.get("ikut_rak") == "1",
                                            catatan=p.get("catatan", ""))
                messages.success(request, f"{n} barang ditambahkan ke usulan.")
            elif aksi == "rak":
                svc_tiket.tambah_rak(request.user, t, varian_id=p.get("varian_id"), rak=p.get("rak"),
                                     catatan=p.get("catatan", ""))
                messages.success(request, "Usulan ganti rak ditambahkan.")
            elif aksi == "rak_lokasi":
                svc_tiket.tambah_rak_lokasi(request.user, t, rak=p.get("rak"), ke=p.get("ke"), catatan=p.get("catatan", ""))
                messages.success(request, "Usulan pindah rak ditambahkan.")
            elif aksi == "hapus":
                svc_tiket.hapus_baris(request.user, t, p.get("id"))
            elif aksi == "kirim":
                svc_tiket.kirim(request.user, t, p.get("catatan", ""))
                messages.success(request, f"Tiket #{t.id} dikirim. Tunggu ACC pemilik.")
                return redirect("gudang:tiket")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect(request.get_full_path())
    cari = layanan.rapikan_teks(request.GET.get("q"))
    hasil, terpotong = ([], False)
    if t.bisa_diisi and len(cari) >= 2:
        hasil, terpotong = _cari_varian(cari, "")
    lihat_stok = "ops_lihat_stok" in hak_pengguna(request.user)
    gudang = _baris_stok(hasil) if lihat_stok else []
    return render(request, "gudang/tiket_isi.html", {
        "menu": "tiket", "t": t, "cari": cari, "hasil": hasil, "terpotong": terpotong,
        "lihat_stok": lihat_stok, "gudang": gudang,
        "boleh_pindah": "ops_tiket_pindah" in hak, "boleh_rak": "ops_tiket_rak" in hak,
        "baris": t.baris.select_related("varian", "rak", "dari", "ke"),
        "pilihan_lokasi": svc_lokasi.pilihan(), "daftar_rak": Rak.objects.select_related("tempat"),
        "daftar_kategori": sorted(set(Item.objects.values_list("category", flat=True)), key=kunci_alami),
    })


# ---------------------------------------------------------------------------
# Barang diambil dari gudang
# ---------------------------------------------------------------------------


def _kelompok_ambil(qs):
    kelompok = {}
    for p in qs.select_related("lokasi", "varian__rak"):
        kelompok.setdefault(p.referensi, {"referensi": p.referensi, "sumber": p.get_sumber_display(),
                                          "dibuat": p.dibuat, "baris": []})["baris"].append(p)
    return list(kelompok.values())


def _konfirmasi_kelompok(request, oleh_pemilik=False):
    """Konfirmasi semua baris satu referensi dari formulir: diambil_<id>, lokasi_<id>, catatan_<id>."""
    from master.layanan import ambil
    from master.layanan import lokasi as svc_lokasi
    from master.models import PermintaanAmbil
    from django.db import transaction
    ref = request.POST.get("referensi")
    baris = list(PermintaanAmbil.objects.filter(referensi=ref, status="menunggu"))
    if not baris:
        raise Tolak("Tidak ada yang menunggu untuk nota itu.")
    with transaction.atomic():
        for p in baris:
            ambil.konfirmasi(request.user, p, jumlah=request.POST.get(f"diambil_{p.id}"),
                             lokasi=svc_lokasi.baca(request.POST.get(f"lokasi_{p.id}") or p.lokasi_id),
                             catatan=request.POST.get(f"catatan_{p.id}", ""), oleh_pemilik=oleh_pemilik)
    return ref, len(baris)


@perlu("gudang_ambil")
def ambil(request):
    tolak = _hanya_toko_stok(request)
    if tolak:
        return tolak
    from master.layanan import lokasi as svc_lokasi
    from master.models import PermintaanAmbil
    if request.method == "POST":
        try:
            ref, n = _konfirmasi_kelompok(request)
            messages.success(request, f"{ref}: {n} barang dicatat sudah diambil.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("gudang:ambil")
    return render(request, "gudang/ambil.html", {
        "menu": "ambil",
        "kelompok": _kelompok_ambil(PermintaanAmbil.objects.filter(status="menunggu")),
        "gudang": [l for l in svc_lokasi.pilihan() if l.jenis == "gudang"],
        "riwayat": PermintaanAmbil.objects.filter(status="diambil", diambil_oleh=request.user.username)
                                          .select_related("diambil_lokasi").order_by("-diambil_waktu")[:15],
    })
