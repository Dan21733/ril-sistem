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

@perlu("gudang")
def panduan(request):
    """Cara kerja tiap menu (SOP), hanya bagian yang sesuai hak akun ini. Halaman lain dibuat tanpa penjelasan panjang."""
    return render(request, "gudang/panduan.html", {"menu": "panduan"})


@perlu("gudang_datang")
def datang(request):
    return redirect("gudang:tiket")


def _pesanan_ditunggu(toko, user):
    """PO yang ditugaskan ke akun ini untuk diterima (penerima = PIC lokasi tujuan atau pilihan pemilik)."""
    pesanan = list(Pembelian.objects.filter(status="dibuat", toko=toko, penerima=user).prefetch_related("baris")
                   .annotate(n=Count("baris")).filter(n__gt=0).order_by("tanggal_pesan", "id"))
    for p in pesanan:
        # Nama supplier dirahasiakan dari gudang: pesanan dikenali dari nomor dan isinya.
        nama = list(dict.fromkeys(b.item_name for b in p.baris.all()))
        p.ringkas = ", ".join(nama[:3]) + (f", dan {len(nama) - 3} barang lain" if len(nama) > 3 else "")
    return pesanan


@perlu("gudang_datang")
def datang_detail(request, po_id):
    """Terima barang tanpa melihat jumlah pesanan (terima buta), lalu hitung ulang yang beda."""
    po = get_object_or_404(Pembelian, pk=po_id, status="dibuat", penerima=request.user)
    if request.method == "POST":
        aksi = request.POST.get("aksi") or "catat"
        try:
            if aksi == "tambah_lain":
                svc.tambah_di_luar_pesanan(request.user, po, varian_id=request.POST.get("varian_id"),
                                           jumlah=request.POST.get("jumlah"))
                messages.success(request, "Ditambahkan sebagai barang di luar pesanan.")
                return redirect("gudang:datang_detail", po_id=po.id)
            if aksi == "hapus_lain":
                svc.hapus_di_luar_pesanan(request.user, po, request.POST.get("id"))
                return redirect("gudang:datang_detail", po_id=po.id)
            hasil = svc.catat_datang_gudang(request.user, po, request.POST)
            if hasil == "ulang":
                messages.warning(request, "Beberapa barang perlu dihitung ulang. Hitung sekali lagi dengan teliti.")
                return redirect("gudang:datang_detail", po_id=po.id)
            messages.success(request, f"Barang datang untuk {po.nomor} sudah dicatat. Menunggu ACC pemilik.")
            return redirect("gudang:tiket")
        except Tolak as tolak:
            messages.error(request, str(tolak))
    from master.layanan import lokasi as svc_lokasi
    po.refresh_from_db()
    ulang = set(po.hitung_ulang or [])
    baris = list(po.baris.select_related("varian__rak__tempat"))
    if ulang:
        baris = [b for b in baris if b.id in ulang]
    for b in baris:
        # Isian tidak pernah diisi dari angka pesanan (terima buta). Barang di luar pesanan
        # diisi jumlah yang dicatat gudang sendiri.
        if request.method == "POST":
            b.isi = request.POST.get(f"terima_{b.id}", "")
        else:
            b.isi = b.jumlah_terima if (b.di_luar_pesanan and not ulang) else ""
        rak = b.varian.rak
        b.usul = request.POST.get(f"lokasi_{b.id}") or (str(b.lokasi_id) if b.lokasi_id else
                                                        (str(rak.tempat_id) if rak and rak.tempat_id else
                                                         (str(po.lokasi_id) if po.lokasi_id else "")))
    cari = layanan.rapikan_teks(request.GET.get("q"))
    hasil_cari = _cari_varian(cari, "")[0][:20] if (len(cari) >= 2 and not ulang) else []
    return render(request, "gudang/datang_detail.html", {
        "menu": "tiket", "po": po, "baris": baris, "ulang": bool(ulang), "hari_ini": timezone.localdate(),
        "kirim": request.POST, "cari": cari, "hasil_cari": hasil_cari,
        "pilihan_lokasi": svc_lokasi.pilihan() if svc.stok_ikut(po) else [],
    })


def _cari_varian(cari, rak, lokasi=None, qs=None):
    """Varian untuk dihitung: dicari lewat nama, varian, brand, alias, atau dibatasi rak (dan lingkup `qs`)."""
    qs = (qs if qs is not None else VarianInfo.objects.filter(aktif=True, sampah__isnull=True)).select_related("rak")
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
    from master.layanan import cari as svc_cari
    # Yang cocok lewat nama didahulukan SEBELUM dipotong, supaya tidak tergeser hasil alias.
    qs = qs.annotate(t=svc_cari.tingkat(cari, lain=("alias_tambahan",))).order_by("t", "item_name", "variant")
    hasil = list(qs[:BATAS_HASIL + 1])
    terpotong = len(hasil) > BATAS_HASIL
    hasil = hasil[:BATAS_HASIL]
    hasil.sort(key=lambda v: (v.t, kunci_alami(v.item_name), kunci_alami(v.variant)))
    return hasil, terpotong


@perlu("gudang_hitung")
def hitung(request):
    """Hitung stok hanya lewat tugas dari pemilik."""
    messages.info(request, "Hitung stok sekarang lewat tugas dari pemilik.")
    return redirect("gudang:tiket")


@perlu("gudang_hitung")
def tiket_hitung(request, tiket_id):
    """Tugas hitung di tablet: daftar barang dimuat sekali, isian dikirim berupa antrian JSON.

    Berlaku untuk tugas sesi opname (toko tutup) dan tugas hitung biasa (toko buka). Tanpa sinyal tetap bisa
    cari, pilih rak, dan mengetik; antrian terkirim saat sinyal kembali. Stok sistem tidak pernah dikirim ke
    halaman ini (hitungan buta). Tugas biasa boleh mengetik kode rak baru; raknya dibuat saat ACC.
    """
    import json

    from django.http import JsonResponse

    from master.layanan import sesi_opname as svc_sesi
    t = get_object_or_404(Tiket.objects.select_related("sesi"), pk=tiket_id, untuk=request.user, jenis="hitung")
    if request.method == "POST":
        if request.content_type == "application/json":
            try:
                isi = json.loads(request.body or b"{}")
                entri = isi.get("entri")
                hasil = svc_sesi.simpan(request.user, t, entri) if t.sesi_id else \
                    opname.simpan_entri(request.user, t, entri)
            except (ValueError, AttributeError):
                return JsonResponse({"pesan": "Isi kiriman tidak terbaca."}, status=400)
            except Tolak as tolak:
                return JsonResponse({"pesan": str(tolak)}, status=409)
            return JsonResponse(hasil)
        try:
            if request.POST.get("aksi") == "kirim":
                if t.sesi_id:
                    n = svc_sesi.kirim(request.user, t, request.POST.get("dihitung_oleh", ""))
                    messages.success(request, f"Tugas #{t.id} dikirim ({n} hitungan). Terima kasih.")
                else:
                    op = opname.kirim_tugas(request.user, t, dihitung_oleh=request.POST.get("dihitung_oleh", ""))
                    messages.success(request, f"Tugas #{t.id} dikirim ({op.baris.count()} barang). "
                                              "Stok dan rak berubah setelah pemilik ACC.")
                return redirect("gudang:tiket")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("gudang:tiket_hitung", t.id)
    lokasi = opname.lokasi_tugas(t)
    return render(request, "gudang/sesi_hitung.html", {
        "menu": "tiket", "t": t, "lokasi": lokasi, "lingkup_teks": opname.lingkup_teks(t),
        "barang": svc_sesi.barang_tablet(t) if t.bisa_diisi else [],
        "hitungan": svc_sesi.hitungan_tugas(t) if t.sesi_id else opname.hitungan_tablet(t),
        "daftar_rak": [{"kode": r.kode, "nama": r.nama} for r in svc_sesi.rak_lokasi(lokasi)],
        "boleh_rak_baru": not t.sesi_id,
        "pindah_rak": not t.sesi_id and opname.perbarui_posisi(t),
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
            hasil = bukadus.ajukan(request.user, k, jumlah=request.POST.get("jumlah"),
                                   isi_total=request.POST.get("isi_total"), catatan=request.POST.get("catatan"),
                                   lokasi_dari=svc_lokasi.baca(request.POST.get("lokasi_dari")),
                                   lokasi_ke=svc_lokasi.baca(request.POST.get("lokasi_ke")))
            messages.success(request, f"Tercatat: {hasil.jumlah} {k.dari.item_name} menjadi {hasil.isi_total} {k.ke.item_name}. "
                                      "Menunggu ACC pemilik; stok berubah setelah di-ACC.")
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
        "riwayat": BukaDus.objects.filter(oleh__in={request.user.username, request.user.first_name} - {""}).select_related("konversi__dari", "konversi__ke")[:10],
    })


# ---------------------------------------------------------------------------
# Tugas: tiket dari pemilik (pindah lokasi/rak, data barang, hitung stok)
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


@perlu("gudang")
def tiket(request):
    """Tugas: pekerjaan otomatis (ambil barang, terima PO) dan tugas dari pemilik, dengan sisa waktunya."""
    from master.akun.akses import hak_pengguna
    from master.layanan.toko import toko_gudang, utama
    from master.models import PermintaanAmbil, Tiket
    hak = hak_pengguna(request.user)
    if not hak & HAK_TUGAS:
        return render(request, "gudang/kosong.html", {"menu": "tiket"})
    toko = toko_gudang(request)
    toko_stok = utama()
    di_toko_stok = toko is not None and toko_stok is not None and toko.pk == toko_stok.pk
    jumlah_ambil = PermintaanAmbil.objects.filter(status="menunggu").count() if (
        "gudang_ambil" in hak and di_toko_stok) else 0
    pesanan = _pesanan_ditunggu(toko, request.user) if ("gudang_datang" in hak and toko) else []
    semua = _tiket_saya(request.user, 40)
    return render(request, "gudang/tiket.html", {
        "menu": "tiket", "aktif": [t for t in semua if t.bisa_diisi],
        "terjadwal": sorted([t for t in semua if t.belum_mulai], key=lambda t: t.mulai),
        "ada_riwayat": any(not t.bisa_diisi and not t.belum_mulai for t in semua),
        "jumlah_ambil_tugas": jumlah_ambil, "pesanan": pesanan,
        "akan_datang": svc.akan_datang(request.user, toko) if ("gudang_datang" in hak and toko) else [],
        "boleh_buka_dus": "gudang_buka_dus" in hak,
    })


def _tiket_saya(user, batas):
    from master.layanan import tiket_barang
    semua = list(Tiket.objects.filter(untuk=user).prefetch_related("baris", "usulan")[:batas])
    sekarang = timezone.now()
    for t in semua:
        t.jumlah_usulan = (len(t.usulan.all()) if t.jenis == "barang" else
                           t.hitungan.count() if t.sesi_id else
                           (t.opname.baris.count() if t.jenis == "hitung" and hasattr(t, "opname") else len(t.baris.all())))
        t.sisa_menit = max(0, int((t.batas - sekarang).total_seconds() // 60))
        if t.jenis == "barang":
            t.izin_teks, t.lingkup_teks = tiket_barang.izin_teks(t), tiket_barang.lingkup_teks(t)
        elif t.jenis == "hitung":
            t.lingkup_teks = opname.lingkup_teks(t)
    return semua


@perlu("gudang")
def riwayat(request):
    """Tugas yang sudah selesai, ditolak, dibatalkan, atau lewat batas."""
    from master.akun.akses import hak_pengguna
    if not hak_pengguna(request.user) & HAK_TUGAS:
        return render(request, "gudang/kosong.html", {"menu": "riwayat"})
    lain = [t for t in _tiket_saya(request.user, 120) if not t.bisa_diisi and not t.belum_mulai][:80]
    return render(request, "gudang/riwayat.html", {"menu": "riwayat", "lain": lain})


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
    kategori = request.GET.get("kategori", "")
    brand = request.GET.get("brand", "")
    status = request.GET.get("status", "") if "aktif" in izin else ""
    kelompok, nomor, jumlah_halaman, total = (svc_barang.halaman(t, cari, nomor, izin, kategori, brand, status)
                                              if t.bisa_diisi and izin else ([], 1, 1, 0))
    daftar_kategori, daftar_brand = svc_barang.pilihan_saring(t) if t.bisa_diisi and izin else ([], [])
    from urllib.parse import urlencode
    saring = urlencode({k: v for k, v in (("q", cari), ("kategori", kategori), ("brand", brand), ("status", status)) if v})
    return render(request, "gudang/tiket_barang.html", {
        "menu": "tiket", "t": t, "cari": cari, "kelompok": kelompok, "nomor": nomor,
        "kategori": kategori, "brand": brand, "status": status, "saring": saring,
        "daftar_kategori": daftar_kategori, "daftar_brand": daftar_brand,
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
    if t.jenis == "hitung":
        return redirect("gudang:tiket_hitung", t.id)
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
    # Jumlah stok tidak pernah ditampilkan di aplikasi gudang (keputusan pemilik 9 Nov 2026).
    lihat_stok = False
    gudang = []
    return render(request, "gudang/tiket_isi.html", {
        "menu": "tiket", "t": t, "cari": cari, "hasil": hasil, "terpotong": terpotong,
        "lihat_stok": lihat_stok, "gudang": gudang,
        "boleh_pindah": "ops_tiket_pindah" in hak, "boleh_rak": "ops_tiket_rak" in hak,
        "baris": t.baris.select_related("varian", "rak", "dari", "ke"),
        "pilihan_lokasi": svc_lokasi.pilihan(), "daftar_rak": Rak.objects.select_related("tempat"),
        "daftar_kategori": sorted(set(Item.objects.values_list("category", flat=True).distinct()), key=kunci_alami),
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
        "menu": "tiket",
        "kelompok": _kelompok_ambil(PermintaanAmbil.objects.filter(status="menunggu")),
        "gudang": [l for l in svc_lokasi.pilihan() if l.jenis == "gudang"],
        "riwayat": PermintaanAmbil.objects.filter(status="diambil", diambil_oleh=request.user.username)
                                          .select_related("diambil_lokasi").order_by("-diambil_waktu")[:15],
    })


def _boleh_siapkan(request):
    """Siapkan tanpa login: cukup perangkat yang disetujui pemilik (gerbang). Yang mengikat orang = PIN."""
    from master.akun.akses import hak_pengguna
    from master.layanan import perangkat_ops
    if getattr(request, "perangkat_ops", None) is not None:
        return True
    if request.user.is_authenticated and "gudang" in hak_pengguna(request.user):
        return True
    return not perangkat_ops.wajib()


def _perangkat_siapkan(request):
    p = getattr(request, "perangkat_ops", None)
    if p is not None:
        return (p.nama or p.kode), f"ops.perangkat-{p.pk}"
    ip = request.META.get("REMOTE_ADDR", "")
    nama = f"akun {request.user.username}" if request.user.is_authenticated else f"ip {ip}"
    return nama, f"ops.ip-{ip}"


def siapkan(request):
    """Siapkan barang (transaksi berlangsung): nota kasir yang barangnya sedang disiapkan.

    Tanpa login (tablet bisa dipinjam siapa saja); halaman hanya terbuka di perangkat yang disetujui.
    Diserahkan dan Menolak wajib PIN karyawan. Aksi dikirim lewat fetch dan daftar diperbarui tiap
    beberapa detik tanpa memuat ulang halaman. Lihat master/layanan/siapkan.py.
    """
    from django.http import HttpResponseForbidden, JsonResponse
    from django.template.loader import render_to_string

    from master.layanan import siapkan as svc_siapkan
    from master.layanan.toko import utama
    if not _boleh_siapkan(request):
        return HttpResponseForbidden("Perangkat ini belum disetujui pemilik.")
    toko = utama()
    if toko is None or not toko.stok_di_kasir:
        return HttpResponseForbidden("Belum ada toko yang memegang stok kasir.")
    nama_perangkat, kunci_pin = _perangkat_siapkan(request)
    lewat_fetch = request.headers.get("X-Siapkan") == "1"
    if request.method == "POST":
        p = request.POST
        aksi, pesan, ok = p.get("aksi"), "", True
        try:
            if aksi == "tugaskan":
                t = svc_siapkan.tugaskan(p.get("tugas"), p.get("karyawan"), perangkat=nama_perangkat)
                pesan = f"{t.pengambil.nama} mengambil barang nota {t.siapkan.nomor}."
            elif aksi == "tambah":
                svc_siapkan.tambah_anggota(p.get("tugas"), p.get("karyawan"), p.get("peran") or "muat",
                                           perangkat=nama_perangkat)
            elif aksi == "keluarkan":
                svc_siapkan.hapus_anggota(p.get("tugas"), p.get("karyawan"))
            elif aksi == "mulai":
                t = svc_siapkan.mulai(p.get("tugas"), perangkat=nama_perangkat)
                pesan = f"Tim mulai menyiapkan barang nota {t.siapkan.nomor} ({t.anggota.count()} orang)."
            elif aksi == "lewati":
                svc_siapkan.lewati(p.get("tugas"), p.get("karyawan"), p.get("alasan"), perangkat=nama_perangkat,
                                   pin=p.get("pin", ""), kunci_pin=kunci_pin)
            elif aksi == "sendiri":
                svc_siapkan.sendiri(p.get("tugas"), perangkat=nama_perangkat)
            elif aksi == "ulang":
                svc_siapkan.ulang(p.get("tugas"))
            elif aksi == "serahkan":
                t = svc_siapkan.serahkan(p.get("tugas"), pin=p.get("pin", ""), perangkat=nama_perangkat, kunci_pin=kunci_pin)
                pesan = f"Diserahkan oleh {t.selesai_oleh}."
            elif aksi == "serahkan_semua":
                s = svc_siapkan.serahkan_semua(p.get("siapkan"), pin=p.get("pin", ""), perangkat=nama_perangkat,
                                               kunci_pin=kunci_pin)
                pesan = f"Nota {s.nomor} selesai diserahkan oleh {s.selesai_oleh}."
            else:
                raise Tolak("Aksi tidak dikenal.")
        except Tolak as tolak:
            ok, pesan = False, str(tolak)
        if lewat_fetch:
            return JsonResponse({"ok": ok, "pesan": pesan})
        if pesan:
            (messages.success if ok else messages.error)(request, pesan)
        return redirect(request.get_full_path())
    svc_siapkan.tarik_nota()
    daftar = svc_siapkan.daftar_buka()
    kasir = (request.GET.get("kasir") or "").strip().upper()
    if not kasir and request.user.is_authenticated:
        kasir = request.user.username.removeprefix("ops-").upper()
    for s in daftar:
        s.punyaku = bool(kasir) and s.kasir_id.upper() == kasir
    punyaku = [s for s in daftar if s.punyaku]
    semua = request.GET.get("semua") == "1" or not punyaku
    konteks = {
        "menu": "siapkan", "toko_gudang": toko, "daftar": daftar if semua else punyaku, "semua": semua,
        "kasir": kasir, "n_semua": len(daftar), "n_punyaku": len(punyaku),
        "selesai": svc_siapkan.selesai_hari_ini(),
        "alasan_lewat": [("tidak_ada", "Tidak ada"), ("sibuk", "Sibuk"), ("menolak", "Menolak")],
    }
    if lewat_fetch:
        return JsonResponse({"html": render_to_string("gudang/_siapkan_daftar.html", konteks, request=request)})
    return render(request, "gudang/siapkan.html", konteks)
