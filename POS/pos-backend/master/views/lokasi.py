from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Exists, F, IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Lower
from django.shortcuts import get_object_or_404, redirect, render

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan import lokasi as svc
from master.layanan import stok_excel
from master.akun.akses import hak_pengguna, perlu
from master.layanan.inti import Tolak
from master.models import Impor, Lokasi, MutasiLokasi, Rak, StokLokasi, VarianInfo
from master.views.utama import KOLOM_NILAI, _qs_tanpa, _saring_barang, halaman_alami, kunci_alami

PER_HALAMAN = 100


def _kirim_excel(isi, awalan):
    respons = HttpResponse(isi, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respons["Content-Disposition"] = f'attachment; filename="{awalan}_{timezone.localtime():%Y%m%d_%H%M}.xlsx"'
    return respons


@perlu("lihat_stok")
def stok_impor(request):
    """Unggah berkas export stok, lalu tampilkan pratinjaunya."""
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        berkas = request.FILES.get("berkas")
        if not berkas:
            messages.error(request, "Pilih berkas Excel dulu.")
            return redirect("master:stok_impor")
        try:
            isi = stok_excel.baca_berkas(berkas, hak)
        except Tolak as tolak:
            messages.error(request, str(tolak))
            return redirect("master:stok_impor")
        objek = Impor.objects.create(pengguna=request.user, nama_berkas=berkas.name[:255], isi=isi)
        return redirect("master:stok_impor_pratinjau", impor_id=objek.id)
    riwayat = [i for i in Impor.objects.select_related("pengguna")[:60] if i.isi.get("jenis") == stok_excel.JENIS][:15]
    return render(request, "master/stok_impor.html", {"menu": "stok_lokasi", "riwayat": riwayat})


@perlu("lihat_stok")
def stok_impor_pratinjau(request, impor_id):
    hak = hak_pengguna(request.user)
    objek = get_object_or_404(Impor, pk=impor_id)
    if objek.isi.get("jenis") != stok_excel.JENIS:
        return redirect("master:impor_pratinjau", impor_id=objek.id)
    if request.method == "POST" and objek.status == "pratinjau":
        try:
            if request.POST.get("aksi") == "terapkan":
                hasil = stok_excel.terapkan(objek, request.user, hak)
                messages.success(request, f"Diterapkan: {hasil['total']} stok total dan {hasil['gudang']} jumlah gudang diubah.")
            else:
                objek.status = "batal"
                objek.save(update_fields=["status"])
                messages.info(request, "Impor dibatalkan. Tidak ada yang berubah.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:stok_impor_pratinjau", impor_id=objek.id)
    baris = stok_excel.pratinjau(objek.isi) if objek.status == "pratinjau" else []
    return render(request, "master/stok_impor_pratinjau.html", {
        "menu": "stok_lokasi", "impor": objek, "isi": objek.isi, "baris": baris[:500], "jumlah": len(baris),
        "toko_minus": sum(1 for b in baris if b["toko_baru"] < 0 <= b["toko_lama"]),
        "gudang_minus": sum(1 for b in baris if b["gudang_minus"]),
    })


@perlu("lihat_stok")
def daftar(request):
    hak = hak_pengguna(request.user)
    svc.lokasi_toko()
    if request.method == "POST":
        try:
            aksi = request.POST.get("aksi")
            if aksi == "simpan":
                svc.simpan_lokasi(request.user, hak=hak, id=request.POST.get("id") or None,
                                  kode=request.POST.get("kode"), nama=request.POST.get("nama"),
                                  keterangan=request.POST.get("keterangan"), urutan=request.POST.get("urutan"))
                messages.success(request, "Lokasi tersimpan.")
            elif aksi == "aktif":
                svc.ubah_aktif(request.user, get_object_or_404(Lokasi, pk=request.POST.get("id")), hak=hak)
                messages.success(request, "Status lokasi diubah.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:lokasi")
    return render(request, "master/lokasi.html", {"menu": "lokasi", "daftar": svc.ringkasan_lokasi()})


def _total_gudang():
    """Jumlah yang secara sistem ada di gudang, dikurangi yang sudah terjual tapi belum diambil.
    Stok Toko = total - nilai ini."""
    from master.models import PermintaanAmbil
    gudang = Coalesce(Subquery(
        StokLokasi.objects.filter(varian__item_name=OuterRef("item_name"), varian__variant=OuterRef("variant"))
        .values("varian").annotate(s=Sum("jumlah")).values("s")[:1],
        output_field=IntegerField()), Value(0))
    pesan = Coalesce(Subquery(
        PermintaanAmbil.objects.filter(status="menunggu", varian__item_name=OuterRef("item_name"),
                                       varian__variant=OuterRef("variant"))
        .values("varian").annotate(s=Sum("jumlah")).values("s")[:1],
        output_field=IntegerField()), Value(0))
    return gudang - pesan


@perlu("lihat_stok")
def stok(request):
    hak = hak_pengguna(request.user)
    layanan.pastikan_info()
    toko = svc.lokasi_toko()
    gudang = svc.gudang_aktif()
    if request.GET.get("export") == "1":
        return _kirim_excel(stok_excel.buat_workbook(_saring_barang(request, hak)), "stok")
    qs = _saring_barang(request, hak).annotate(di_gudang=_total_gudang())
    g = request.GET
    if g.get("lokasi"):
        if g["lokasi"] == str(toko.id):
            qs = qs.filter(in_stock__gt=F("di_gudang"))
        else:
            qs = qs.filter(Exists(StokLokasi.objects.filter(
                varian__item_name=OuterRef("item_name"), varian__variant=OuterRef("variant"),
                lokasi_id=g["lokasi"]).exclude(jumlah=0)))
    if g.get("toko") == "minus":
        qs = qs.filter(in_stock__lt=F("di_gudang"))
    elif g.get("toko") == "semua_gudang":
        qs = qs.filter(di_gudang__gt=0, in_stock=F("di_gudang"))
    halaman = halaman_alami(qs, (*KOLOM_NILAI, "di_gudang"), PER_HALAMAN, g.get("hal"))
    barang = list(halaman.object_list)
    info = {(v.item_name, v.variant): v for v in VarianInfo.objects.filter(
        item_name__in={b["item_name"] for b in barang}).select_related("rak__tempat")}
    per_varian = svc.jumlah_gudang(v.id for v in info.values())
    for b in barang:
        v = info.get((b["item_name"], b["variant"]))
        b["id"] = v.id if v else None
        b["rak"] = v.rak if v else None
        jumlah = per_varian.get(v.id, {}) if v else {}
        b["kolom"] = [jumlah.get(l.id, 0) for l in gudang]
        b["toko"] = (b["in_stock"] or 0) - b["di_gudang"]
    barang.sort(key=lambda b: (kunci_alami(b["item_name"]), kunci_alami(b["variant"])))
    semua = Item.objects.annotate(di_gudang=_total_gudang())
    return render(request, "master/stok_lokasi.html", {
        "menu": "stok_lokasi", "barang": barang, "halaman": halaman, "g": g, "gudang": gudang, "toko": toko,
        "qs_halaman": _qs_tanpa(request, "hal"),
        "jumlah_minus": semua.filter(in_stock__lt=F("di_gudang")).count(),
        "daftar_lokasi": Lokasi.objects.filter(aktif=True),
        "daftar_kategori": sorted(set(Item.objects.values_list("category", flat=True)), key=kunci_alami),
        "daftar_rak": Rak.objects.select_related("tempat"),
    })


@perlu("lihat_stok")
def pindah(request, varian_id):
    hak = hak_pengguna(request.user)
    info = get_object_or_404(VarianInfo, pk=varian_id, sampah__isnull=True)
    if request.method == "POST":
        try:
            svc.pindah(request.user, info, hak=hak,
                       dari=get_object_or_404(Lokasi, pk=request.POST.get("dari")),
                       ke=get_object_or_404(Lokasi, pk=request.POST.get("ke")),
                       jumlah=request.POST.get("jumlah"), catatan=request.POST.get("catatan", ""))
            messages.success(request, "Stok dipindah.")
            kembali = request.POST.get("kembali") or ""
            if kembali.startswith("/") and not kembali.startswith("//"):
                return redirect(kembali)
            return redirect("master:stok_lokasi")
        except Tolak as tolak:
            messages.error(request, str(tolak))
    baris = Item.objects.filter(item_name=info.item_name, variant=info.variant).values("in_stock").first()
    if baris is None:
        messages.error(request, "Barang ini tidak ada di tabel kasir.")
        return redirect("master:stok_lokasi")
    rinci = svc.rincian(info, total=baris["in_stock"] or 0)
    daftar = [{"lokasi": l, "jumlah": rinci.get(l.id, 0)} for l in Lokasi.objects.all()
              if l.aktif or rinci.get(l.id)]
    return render(request, "master/stok_lokasi_pindah.html", {
        "menu": "stok_lokasi", "info": info, "total": baris["in_stock"] or 0, "daftar": daftar,
        "aktif": [d for d in daftar if d["lokasi"].aktif],
        "kosong": not any(d["jumlah"] for d in daftar),
        "riwayat": MutasiLokasi.objects.filter(varian=info).select_related("dari", "ke")[:30],
        "kembali": request.GET.get("kembali", ""),
    })


# ---------------------------------------------------------------------------
# Hitung acak oleh pemilik
# ---------------------------------------------------------------------------


@perlu("opname")
def hitung_acak(request):
    from master.layanan import hitung_acak as svc_acak
    from master.models import HitungAcak
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        try:
            h = svc_acak.mulai(request.user, lokasi=svc.baca(request.POST.get("lokasi")),
                               jumlah=request.POST.get("jumlah"), hak=hak)
            return redirect("master:hitung_acak_detail", h.id)
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:hitung_acak")
    daftar = list(HitungAcak.objects.select_related("lokasi").prefetch_related("baris")[:50])
    for h in daftar:
        semua = list(h.baris.all())
        h.n = len(semua)
        h.beda = sum(1 for b in semua if b.selisih)
        h.kurang = sum(-b.selisih for b in semua if b.selisih and b.selisih < 0)
    return render(request, "master/hitung_acak.html", {
        "menu": "hitung_acak", "daftar": daftar, "pilihan": svc.pilihan(),
    })


@perlu("opname")
def hitung_acak_detail(request, h_id):
    from master.layanan import hitung_acak as svc_acak
    from master.models import HitungAcak
    hak = hak_pengguna(request.user)
    h = get_object_or_404(HitungAcak.objects.select_related("lokasi"), pk=h_id)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "simpan":
                svc_acak.simpan(request.user, h, request.POST)
                messages.success(request, "Hitungan tersimpan.")
            elif aksi == "selesai":
                svc_acak.selesai(request.user, h, request.POST)
                messages.success(request, "Selesai. Bandingkan dengan stok sistem di bawah.")
            elif aksi == "terapkan":
                n = svc_acak.terapkan(request.user, h, request.POST.getlist("baris"), hak=hak)
                messages.success(request, f"{n} baris diterapkan ke stok.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:hitung_acak_detail", h.id)
    baris = list(h.baris.select_related("varian__rak"))
    baris.sort(key=lambda b: (kunci_alami(b.varian.item_name), kunci_alami(b.varian.variant)))
    return render(request, "master/hitung_acak_detail.html", {"menu": "hitung_acak", "h": h, "baris": baris})


# ---------------------------------------------------------------------------
# Tiket admin (sisi pemilik)
# ---------------------------------------------------------------------------


@perlu("kelola_lokasi")
def tiket_daftar(request):
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from master.layanan import tiket as svc_tiket
    from master.models import Tiket
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        try:
            untuk = get_user_model().objects.filter(pk=request.POST.get("untuk") or 0, is_active=True).first()
            t = svc_tiket.buka(request.user, untuk=untuk, tujuan=request.POST.get("tujuan"),
                               lingkup=request.POST.get("lingkup"), batas=request.POST.get("batas"), hak=hak,
                               jenis=request.POST.get("jenis") or "lokasi", izin=request.POST.getlist("izin"),
                               kategori=request.POST.getlist("kategori"), brand=request.POST.getlist("brand"))
            messages.success(request, f"Tiket #{t.id} dibuka untuk {t.untuk.first_name or t.untuk.username}.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:tiket")
    from pos.models import Item
    from master.layanan.inti import kunci_alami
    admin = []
    for u in get_user_model().objects.filter(is_active=True).order_by("first_name", "username"):
        h = hak_pengguna(u)
        jenis = svc_tiket.jenis_untuk(h) if "gudang" in h and not u.is_superuser else []
        if jenis:
            admin.append({"u": u, "jenis": " ".join(jenis), "izin": " ".join(svc_tiket.izin_barang_untuk(h))})
    daftar = list(Tiket.objects.select_related("untuk").prefetch_related("baris", "usulan")[:60])
    for t in daftar:
        t.jumlah_usulan = len(t.usulan.all()) if t.jenis == "barang" else len(t.baris.all())
    return render(request, "master/tiket.html", {
        "menu": "tiket", "daftar": daftar,
        "admin": admin, "izin_barang": Tiket.IZIN_BARANG,
        "daftar_kategori": sorted({k for k in Item.objects.values_list("category", flat=True) if k}, key=kunci_alami),
        "daftar_brand": sorted({b for b in Item.objects.values_list("brand", flat=True) if b}, key=kunci_alami),
        "batas_awal": (timezone.localtime() + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
    })


@perlu("kelola_lokasi")
def tiket_detail(request, tiket_id):
    from master.layanan import tiket as svc_tiket
    from master.models import Tiket
    hak = hak_pengguna(request.user)
    t = get_object_or_404(Tiket.objects.select_related("untuk"), pk=tiket_id)
    if t.jenis == "barang":
        return _tiket_barang_detail(request, t, hak)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "terapkan":
                ok, gagal = svc_tiket.terapkan(request.user, t, request.POST, hak=hak)
                (messages.warning if gagal else messages.success)(
                    request, f"{ok} usulan diterapkan." + (f" {gagal} gagal, lihat kolom hasil." if gagal else ""))
            elif aksi == "tolak":
                svc_tiket.tolak(request.user, t, request.POST.get("alasan"), hak=hak)
                messages.info(request, "Tiket ditolak.")
            elif aksi == "batal":
                svc_tiket.batalkan(request.user, t, hak=hak)
                messages.info(request, "Tiket dibatalkan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:tiket_detail", t.id)
    baris = list(t.baris.select_related("varian__rak", "rak__tempat", "dari", "ke"))
    for b in baris:
        if b.jenis == "pindah":
            b.stok_asal = svc.stok_di(b.varian, b.dari)
    return render(request, "master/tiket_detail.html", {
        "menu": "tiket", "t": t, "baris": baris, "pilihan_lokasi": svc.pilihan(),
    })


def _tiket_barang_detail(request, t, hak):
    """ACC tiket data barang: semua usulan tercentang di awal, dengan tanda yang perlu dilihat."""
    from master.layanan import tiket as svc_tiket
    from master.layanan import tiket_barang as svc_barang
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "terapkan":
                ok, gagal, tolak = svc_barang.terapkan(request.user, t, request.POST, hak=hak)
                (messages.warning if gagal else messages.success)(
                    request, f"{ok} usulan diterapkan, {tolak} tidak di-ACC." + (f" {gagal} gagal, lihat kolom hasil." if gagal else ""))
            elif aksi == "tolak":
                svc_tiket.tolak(request.user, t, request.POST.get("alasan"), hak=hak)
                messages.info(request, "Tiket ditolak.")
            elif aksi == "batal":
                svc_tiket.batalkan(request.user, t, hak=hak)
                messages.info(request, "Tiket dibatalkan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:tiket_detail", t.id)
    try:
        batas_persen = max(1, min(500, int(request.GET.get("persen") or svc_barang.BATAS_PERSEN)))
    except ValueError:
        batas_persen = svc_barang.BATAS_PERSEN
    baris = svc_barang.rincian(t, lihat_modal="lihat_modal" in hak, batas_persen=batas_persen)
    return render(request, "master/tiket_barang.html", {
        "menu": "tiket", "t": t, "baris": baris, "batas_persen": batas_persen,
        "lihat_modal": "lihat_modal" in hak, "jumlah_tanda": sum(1 for b in baris if b["tanda"]),
        "izin_teks": svc_barang.izin_teks(t), "lingkup_teks": svc_barang.lingkup_teks(t),
    })


# ---------------------------------------------------------------------------
# Barang diambil dari gudang (pemilik)
# ---------------------------------------------------------------------------


@perlu("lihat_stok")
def ambil(request):
    from django.db.models import F as F_
    from master.models import PermintaanAmbil
    from master.views.gudang import _kelompok_ambil, _konfirmasi_kelompok
    if request.method == "POST":
        if "ubah_stok" not in hak_pengguna(request.user):
            messages.error(request, "Akun ini tidak boleh mengonfirmasi barang diambil.")
        else:
            try:
                ref, n = _konfirmasi_kelompok(request, oleh_pemilik=True)
                messages.success(request, f"{ref}: {n} barang dikonfirmasi diambil (oleh pemilik).")
            except Tolak as tolak:
                messages.error(request, str(tolak))
        return redirect("master:ambil")
    riwayat = PermintaanAmbil.objects.exclude(status="menunggu").select_related("lokasi", "diambil_lokasi")
    if request.GET.get("beda") == "1":
        riwayat = riwayat.filter(status="diambil").exclude(diambil_jumlah=F_("jumlah"))
    return render(request, "master/ambil.html", {
        "menu": "ambil", "g": request.GET,
        "kelompok": _kelompok_ambil(PermintaanAmbil.objects.filter(status="menunggu")),
        "gudang": [l for l in svc.pilihan() if l.jenis == "gudang"],
        "riwayat": riwayat.order_by("-diambil_waktu", "-id")[:200],
        "jumlah_beda": PermintaanAmbil.objects.filter(status="diambil").exclude(diambil_jumlah=F_("jumlah")).count(),
    })


@perlu("opname")
def laporan_selisih(request):
    from datetime import timedelta
    from django.utils import timezone
    from master.layanan import selisih
    from master.layanan.pembelian import baca_tanggal
    hari_ini = timezone.localdate()
    try:
        dari = baca_tanggal(request.GET.get("dari"), label="Dari") or hari_ini - timedelta(days=90)
        sampai = baca_tanggal(request.GET.get("sampai"), label="Sampai") or hari_ini
    except Tolak as tolak:
        messages.error(request, str(tolak))
        dari, sampai = hari_ini - timedelta(days=90), hari_ini
    return render(request, "master/laporan_selisih.html", {
        "menu": "laporan_selisih", "dari": dari, "sampai": sampai, "r": selisih.ringkas(dari, sampai),
        "lihat_nilai": "lihat_modal" in hak_pengguna(request.user),
    })
