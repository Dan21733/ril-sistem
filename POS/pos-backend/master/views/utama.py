import json
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Value, Count, Exists, F, Max, Min, OuterRef, Q, Sum
from django.db.models.functions import Lower
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from pos.models import Item, Transactions

from master.layanan import excel
from master.layanan import inti as layanan
from master.layanan import masalah
from master.layanan import pemasok
from master.layanan import lokasi as svc_lokasi
from master.akun.akses import hak_pengguna, perlu
from master.layanan.inti import Tolak, kunci_alami, urut_alami
from master.models import Impor, ItemInfo, Log, Lokasi, MutasiStok, Rak, Supplier, VarianInfo

PER_HALAMAN = 50


# ---------------------------------------------------------------------------
# Pembantu
# ---------------------------------------------------------------------------


def _cari(request):
    """Kata pencarian tanpa bagian harga (@2000)."""
    from master.layanan import cari as svc_cari
    return layanan.rapikan_teks(svc_cari.pisah_harga(request.GET.get("q"))[0])


def _harga_cari(request):
    from master.layanan import cari as svc_cari
    return svc_cari.pisah_harga(request.GET.get("q"))[1]


def _milik(request):
    """Item milik toko yang dipilih di menu samping (barang titipan terpisah dari barang toko sendiri)."""
    from master.layanan import titipan
    from master.layanan.toko import toko_aktif
    return titipan.saring_barang(Item.objects.all(), toko_aktif(request))


def _saring_barang(request, hak):
    """Queryset Item sesuai filter di alamat."""
    g = request.GET
    from master.layanan import titipan
    from master.layanan.toko import toko_aktif
    # Barang toko titipan hanya tampil saat toko titipan itu yang dipilih di menu samping.
    qs = titipan.saring_barang(Item.objects.all(), toko_aktif(request))

    cari = _cari(request)
    if cari:
        for kata in cari.split(" "):
            qs = qs.filter(
                Q(item_name__icontains=kata)
                | Q(variant__icontains=kata)
                | Q(brand__icontains=kata)
                | Q(sku__icontains=kata)
                | Q(item_name__in=ItemInfo.objects.filter(alias__icontains=kata).values("item_name"))
                | Exists(VarianInfo.objects.filter(
                    item_name=OuterRef("item_name"), variant=OuterRef("variant"),
                    alias_tambahan__icontains=kata,
                ))
            )
    if g.get("kategori"):
        qs = qs.filter(category=g["kategori"])
    if g.get("brand"):
        qs = qs.filter(brand=g["brand"])
    if g.get("rak"):
        info = VarianInfo.objects.filter(item_name=OuterRef("item_name"), variant=OuterRef("variant"))
        if g["rak"] == "-":
            qs = qs.filter(~Exists(info.filter(rak__isnull=False)))
        else:
            qs = qs.filter(Exists(info.filter(rak_id=g["rak"])))
    if g.get("aktif") in ("ya", "tidak"):
        nonaktif = VarianInfo.objects.filter(
            item_name=OuterRef("item_name"), variant=OuterRef("variant"), aktif=False
        )
        qs = qs.filter(Exists(nonaktif)) if g["aktif"] == "tidak" else qs.filter(~Exists(nonaktif))
    if g.get("stok") == "habis":
        qs = qs.filter(in_stock__lte=0)
    elif g.get("stok") == "minus":
        qs = qs.filter(in_stock__lt=0)
    elif g.get("stok") == "menipis":
        qs = qs.filter(stock_alert__isnull=False, in_stock__lte=F("stock_alert"))
    if g.get("supplier", "").isdigit() and pemasok.boleh_lihat(hak):
        qs = qs.filter(item_name__in=pemasok.nama_barang_dari(int(g["supplier"])))
    kunci = g.get("masalah")
    if kunci in masalah.PER_KUNCI and masalah.PER_KUNCI[kunci] in masalah.aturan_untuk(hak):
        qs = masalah.PER_KUNCI[kunci]["saring"](qs)

    return qs


def _lengkapi(barang, hak):
    """Tempelkan data master dan label masalah ke daftar dict barang."""
    nama = {b["item_name"] for b in barang}
    info = {(v.item_name, v.variant): v for v in VarianInfo.objects.filter(item_name__in=nama)}
    item_info = {i.item_name: i for i in ItemInfo.objects.filter(item_name__in=nama)}
    for b in barang:
        v = info.get((b["item_name"], b["variant"]))
        i = item_info.get(b["item_name"])
        b["id"] = v.id if v else None
        b["kode_rak"] = v.rak_id if v else None
        b["alias_tambahan"] = v.alias_tambahan if v else ""
        b["aktif"] = v.aktif if v else True
        b["modal_rata"] = v.modal_rata if v else None
        b["alias"] = i.alias if i else ""
        b["item_id"] = i.id if i else None
        b["titipan"] = bool(i and i.toko_id)
        b["masalah"] = masalah.label_masalah(b, hak)
        b["tanda"] = masalah.tanda_sel(b, b["masalah"])
        if "lihat_modal" not in hak:
            b["cost_amount"] = None
            b["modal_rata"] = None
    return barang


KOLOM_NILAI = ("item_name", "variant", "category", "brand", "sku", "min_price", "max_price",
               "cost_amount", "in_stock", "stock_alert")


def _qs_tanpa(request, *buang):
    data = {k: v for k, v in request.GET.items() if k not in buang and v != ""}
    return urlencode(data)


def halaman_alami(qs, kolom, per_halaman, hal, cari=""):
    """Paginator dengan urutan alami di SELURUH hasil, bukan per halaman.

    Database mengurutkan teks huruf demi huruf (12 MM sebelum 4 MM), jadi kunci semua
    baris diurutkan di Python dulu, baru halaman yang diminta diambil isinya.
    Kalau sedang mencari, yang cocok lewat nama didahulukan (master/layanan/cari.py).
    """
    from master.layanan import cari as svc_cari
    if cari:
        baris = qs.annotate(tingkat_cari=svc_cari.tingkat(cari)).values_list("item_name", "variant", "tingkat_cari")
        kunci = [(n, v) for n, v, _ in sorted(baris, key=lambda k: (k[2], kunci_alami(k[0]), kunci_alami(k[1])))]
    else:
        kunci = sorted(qs.values_list("item_name", "variant"), key=lambda k: (kunci_alami(k[0]), kunci_alami(k[1])))
    halaman = Paginator(kunci, per_halaman).get_page(hal)
    pilih = list(halaman.object_list)
    if pilih:
        per = {(r["item_name"], r["variant"]): r for r in qs.filter(item_name__in={n for n, _ in pilih}).values(*kolom)}
        halaman.object_list = [per[k] for k in pilih if k in per]
    return halaman


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@perlu()
def dashboard(request):
    """Beranda: widget yang bisa diatur per akun (lihat master/layanan/dasbor.py)."""
    from master.layanan import dasbor
    from master.layanan.toko import toko_aktif
    from master.models import PreferensiAkun
    hak = hak_pengguna(request.user)
    pref = PreferensiAkun.objects.filter(user=request.user).first()
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        if not isinstance(data, dict):
            return JsonResponse({"ok": False}, status=400)
        pref = pref or PreferensiAkun(user=request.user)
        pref.dasbor = [] if data.get("bawaan") else dasbor.bersihkan(data.get("susunan"), hak)
        pref.save()
        return JsonResponse({"ok": True})

    layanan.pastikan_info()
    tampil = dasbor.susunan(pref, hak)
    kunci = {w["k"] for w in tampil}
    toko = toko_aktif(request)
    jual = dasbor.penjualan(toko, hak) if kunci & {"omzet_hari", "laba_hari", "bulan", "minggu", "jam", "terlaris", "kategori"} else None
    kotak = []
    if "data_barang" in kunci:
        semua = Item.objects.all()
        kotak = [{**aturan, "jumlah": aturan["saring"](semua).count()} for aturan in masalah.aturan_untuk(hak)]
    return render(request, "master/dashboard.html", {
        "menu": "dashboard",
        "tampil": tampil, "tersembunyi": dasbor.tersembunyi(tampil, hak),
        "jual": jual, "lain": dasbor.lain(request, hak, toko), "kotak": kotak,
        "jumlah_varian": Item.objects.count(), "jumlah_item": ItemInfo.objects.count(),
        "log_terbaru": Log.objects.all()[:8] if "riwayat" in kunci else [],
    })


# ---------------------------------------------------------------------------
# Barang
# ---------------------------------------------------------------------------


@perlu()
def barang_daftar(request):
    """Daftar per barang. Varian baru terlihat saat barangnya dibuka di panel kanan."""
    hak = hak_pengguna(request.user)
    layanan.pastikan_info()
    qs = _saring_barang(request, hak)

    if request.GET.get("export") == "1":
        from master.layanan.toko import toko_aktif
        isi = excel.buat_workbook(qs, hak, toko=toko_aktif(request))
        nama = f"barang_{timezone.localtime():%Y%m%d_%H%M}.xlsx"
        respons = HttpResponse(
            isi, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        respons["Content-Disposition"] = f'attachment; filename="{nama}"'
        return respons

    jumlah_varian = qs.count()
    from master.layanan import cari as svc_cari
    cari = _cari(request)
    harga_cari = _harga_cari(request)
    kelompok_qs = qs.values("item_name").annotate(
        cocok=Count("variant"), harga_min=Min("max_price"), harga_max=Max("max_price"),
        stok=Sum("in_stock"), nama_urut=Lower("item_name"), tingkat_cari=Min(svc_cari.tingkat(cari)),
        jarak_harga=Min(svc_cari.jarak_harga(harga_cari)) if harga_cari else Min(Value(0)),
    )
    urut = {
        "harga": ("-harga_max", "nama_urut"),
        "harga_naik": ("harga_min", "nama_urut"),
        "stok": ("stok", "nama_urut"),
    }.get(request.GET.get("urut"), ("nama_urut",))
    if urut == ("nama_urut",):
        # Urutan alami di seluruh daftar (bukan huruf demi huruf dari database).
        # Saat mencari: yang cocok lewat nama dulu, yang hanya cocok lewat alias paling akhir.
        # Dengan harga (@2000): barang yang punya varian berharga pas/terdekat di atas.
        halaman = Paginator(sorted(kelompok_qs, key=lambda d: (d["jarak_harga"], d["tingkat_cari"], kunci_alami(d["item_name"]))),
                            PER_HALAMAN).get_page(request.GET.get("hal"))
    else:
        halaman = Paginator(kelompok_qs.order_by("tingkat_cari", *urut), PER_HALAMAN).get_page(request.GET.get("hal"))
    daftar = list(halaman.object_list)

    nama = [d["item_name"] for d in daftar]
    varian = _lengkapi(list(Item.objects.filter(item_name__in=nama).values(*KOLOM_NILAI)), hak)
    per_nama = {}
    for v in varian:
        per_nama.setdefault(v["item_name"], []).append(v)
    for d in daftar:
        semua = per_nama.get(d["item_name"], [])
        d["item_id"] = semua[0]["item_id"] if semua else None
        d["total"] = len(semua)
        d["category"] = " / ".join(sorted({v["category"] or "" for v in semua}, key=kunci_alami))
        d["brand"] = " / ".join(sorted({v["brand"] or "" for v in semua}, key=kunci_alami))
        d["nonaktif"] = sum(1 for v in semua if not v["aktif"])
        if harga_cari:
            dekat = min(semua, key=lambda v: svc_cari.jarak_py(harga_cari, v["max_price"], v["min_price"]), default=None)
            if dekat:
                ket = svc_cari.keterangan_harga(harga_cari, dekat["max_price"], dekat["min_price"])
                d["ket_harga"] = f"{dekat['variant']}: {ket}" if dekat["variant"] != "N/A" else ket
        label = {}
        for v in semua:
            for m in v["masalah"]:
                label[m["label"]] = label.get(m["label"], 0) + 1
        d["masalah"] = [{"label": k, "jumlah": n} for k, n in label.items()]

    from master.layanan import titipan
    from master.layanan.toko import toko_aktif
    toko_kini = toko_aktif(request)
    milik = titipan.saring_barang(Item.objects.all(), toko_kini)
    return render(request, "master/barang.html", {
        "menu": "barang",
        "daftar": daftar,
        "halaman": halaman,
        "jumlah_varian": jumlah_varian,
        "harga_cari": harga_cari,
        "ada_filter": any(request.GET.get(k) for k in ("q", "kategori", "brand", "rak", "masalah", "aktif")),
        "g": request.GET,
        "qs_halaman": _qs_tanpa(request, "hal", "buka"),
        "qs_export": _qs_tanpa(request, "hal", "export", "buka"),
        "daftar_kategori": sorted(set(milik.values_list("category", flat=True)), key=kunci_alami),
        "daftar_brand": sorted(set(milik.values_list("brand", flat=True)), key=kunci_alami),
        "daftar_supplier": Supplier.objects.filter(aktif=True) if pemasok.boleh_lihat(hak) else [],
        "daftar_rak": Rak.objects.all(),
        "daftar_masalah": masalah.aturan_untuk(hak),
        "toko_titipan": toko_kini if titipan.adalah_titipan(toko_kini) else None,
    })


@require_POST
@perlu()
def barang_ubah(request):
    """Edit satu sel dari tabel. Balasan JSON."""
    hak = hak_pengguna(request.user)
    try:
        data = json.loads(request.body.decode("utf-8"))
        info = VarianInfo.objects.get(pk=int(data["id"]))
        kolom = str(data["kolom"])
    except (ValueError, KeyError, TypeError, VarianInfo.DoesNotExist):
        return JsonResponse({"ok": False, "pesan": "Permintaan tidak dikenali."}, status=400)
    try:
        nilai, berubah = layanan.ubah_varian(request.user, info, kolom, data.get("nilai"), hak=hak,
                                             catatan=data.get("catatan") or "")
    except Tolak as tolak:
        return JsonResponse({"ok": False, "pesan": str(tolak)}, status=400)

    baris = Item.objects.filter(item_name=info.item_name, variant=info.variant).values(*KOLOM_NILAI).first()
    _lengkapi([baris], hak)
    return JsonResponse({
        "ok": True, "nilai": nilai, "berubah": berubah, "masalah": baris["masalah"], "tanda": baris["tanda"],
    })


@perlu("ubah_info")
def barang_baru(request):
    """Barang baru di panel kanan daftar barang (beberapa varian sekaligus).

    GET ?panel=1: isi panel. GET ?cek=<nama>: apakah nama itu sudah ada (JSON).
    POST (JSON): simpan; balasan JSON berisi alamat untuk membuka barangnya.
    Dibuka langsung tanpa ?panel diarahkan ke daftar barang dengan panel ini terbuka.
    """
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("varian"), list):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"ok": False, "pesan": "Permintaan tidak dikenali."}, status=400)
        try:
            from master.layanan.toko import toko_aktif
            item_info = layanan.tambah_barang(
                request.user, hak=hak, item_name=data.get("item_name"), category=data.get("category"),
                brand=data.get("brand"), alias=data.get("alias"),
                varian=[v for v in data["varian"] if isinstance(v, dict)], toko=toko_aktif(request))
        except layanan.Tolak as tolak:
            return JsonResponse({"ok": False, "pesan": str(tolak), "baris": getattr(tolak, "baris", None)}, status=400)
        n = Item.objects.filter(item_name=item_info.item_name).count()
        if data.get("dari") != "nota":   # dari halaman nota: tidak dimuat ulang, pesan ditampilkan di sana
            messages.success(request, f"Barang '{item_info.item_name}' ditambahkan ({n} varian).")
        return JsonResponse({"ok": True, "id": item_info.id, "nama": item_info.item_name, "jumlah_varian": n,
                             "url": f"{reverse('master:barang')}?buka={item_info.id}"})
    cek = request.GET.get("cek")
    if cek is not None:
        nama = layanan.rapikan_teks(cek)
        ada = ItemInfo.objects.filter(item_name__iexact=nama).first() if nama else None
        return JsonResponse({"ada": bool(ada and Item.objects.filter(item_name=ada.item_name).exists()),
                             "id": ada.id if ada else None, "nama": ada.item_name if ada else ""})
    if request.GET.get("panel") != "1":
        return redirect(f"{reverse('master:barang')}?baru=1")
    return render(request, "master/_barang_baru.html", {
        "daftar_kategori": sorted({k for k in _milik(request).values_list("category", flat=True) if k}, key=kunci_alami),
        "daftar_brand": sorted({k for k in _milik(request).values_list("brand", flat=True) if k}, key=kunci_alami),
        "daftar_rak": Rak.objects.all(),
    })


def _jawab_panel(request, *, hilang, aksi=""):
    pesan = [{"jenis": m.tags, "teks": str(m)} for m in messages.get_messages(request)]
    return JsonResponse({"ok": not any(p["jenis"] == "error" for p in pesan), "pesan": pesan,
                         "hilang": hilang, "aksi": aksi})


def _tanpa_buka(kembali, request):
    """Alamat kembali ke daftar barang tanpa membuka barang yang sudah tidak ada (filter pencarian tetap)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit
    if not kembali or not url_has_allowed_host_and_scheme(kembali, allowed_hosts={request.get_host()}):
        return ""
    u = urlsplit(kembali)
    qs = urlencode([(k, v) for k, v in parse_qsl(u.query) if k != "buka"])
    return u.path + ("?" + qs if qs else "")


@perlu("ganti_nama")
def barang_nama(request):
    """Semua nama barang (JSON) untuk saran nama tujuan saat memindah/menggabung."""
    return JsonResponse({"nama": sorted(set(Item.objects.values_list("item_name", flat=True)), key=kunci_alami)})


@require_POST
@perlu()
def barang_atur(request):
    """Atur ulang barang/varian sekaligus (JSON): susun rencana, periksa, terapkan; juga aktif/nonaktif massal."""
    from master.layanan import atur_barang
    hak = hak_pengguna(request.user)
    try:
        data = json.loads(request.body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        aksi = data.get("aksi")
        ids = [int(x) for x in data.get("varian") or []]
        item_ids = [int(x) for x in data.get("item") or []]
        baris = data.get("baris") or []
        if not isinstance(baris, list) or not all(isinstance(b, dict) and "id" in b for b in baris):
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "pesan": "Permintaan tidak dikenali."}, status=400)
    try:
        if aksi == "susun":
            if atur_barang.HAK not in hak:
                raise Tolak("Akun ini tidak boleh mengganti nama atau memindah varian.")
            rencana = atur_barang.susun(data.get("jenis"), varian_ids=ids, item_ids=item_ids, tujuan=data.get("tujuan"))
            return JsonResponse({"ok": True, "baris": atur_barang.periksa(rencana)})
        if aksi == "periksa":
            return JsonResponse({"ok": True, "baris": atur_barang.periksa(baris)})
        if aksi == "terapkan":
            n, tujuan = atur_barang.terapkan(request.user, baris, hak=hak)
            buka = ItemInfo.objects.filter(item_name__in=tujuan).order_by("item_name").values_list("pk", flat=True).first()
            return JsonResponse({"ok": True, "pesan": f"{n} varian dipindah." if n else "Tidak ada yang berubah.",
                                 "buka": buka, "jumlah_tujuan": len(tujuan)})
        if aksi in ("aktifkan", "nonaktifkan"):
            n = atur_barang.ubah_aktif(request.user, ids, aksi == "aktifkan", hak=hak)
            return JsonResponse({"ok": True, "pesan": f"{n} varian {'diaktifkan' if aksi == 'aktifkan' else 'dinonaktifkan'}."})
    except Tolak as tolak:
        return JsonResponse({"ok": False, "pesan": str(tolak)}, status=400)
    return JsonResponse({"ok": False, "pesan": "Aksi tidak dikenal."}, status=400)


@perlu()
def barang_ringkas(request, item_id):
    """Ringkasan satu barang untuk kotak referensi di formulir barang baru (JSON)."""
    hak = hak_pengguna(request.user)
    item_info = get_object_or_404(ItemInfo, pk=item_id)
    baris = sorted(Item.objects.filter(item_name=item_info.item_name).values(*KOLOM_NILAI),
                   key=lambda b: kunci_alami(b["variant"]))
    lihat_modal = "lihat_modal" in hak
    return JsonResponse({
        "nama": item_info.item_name,
        "kategori": baris[0]["category"] if baris else "",
        "brand": baris[0]["brand"] if baris else "",
        "varian": [{"varian": "" if b["variant"] == layanan.VARIAN_KOSONG else b["variant"],
                    "floor": b["min_price"], "jual": b["max_price"],
                    **({"modal": b["cost_amount"]} if lihat_modal else {})} for b in baris],
    })


@perlu()
def item_detail(request, item_id, panel=False):
    hak = hak_pengguna(request.user)
    item_info = get_object_or_404(ItemInfo, pk=item_id)

    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "simpan_item":
                ubah = []
                _, a = layanan.ubah_alias_item(request.user, item_info, request.POST.get("alias"), hak=hak)
                _, k = layanan.ubah_kolom_item(request.user, item_info.item_name, "category",
                                               request.POST.get("category"), hak=hak)
                _, b = layanan.ubah_kolom_item(request.user, item_info.item_name, "brand",
                                               request.POST.get("brand"), hak=hak)
                ubah = [n for n, ya in (("alias", a), ("kategori", k), ("brand", b)) if ya]
                messages.success(request, "Tersimpan: " + ", ".join(ubah) + "." if ubah else "Tidak ada yang berubah.")
            elif aksi == "ganti_nama_item":
                if layanan.ganti_nama_item(request.user, item_info, request.POST.get("nama_baru"), hak=hak):
                    messages.success(request, f"Nama diganti menjadi '{item_info.item_name}'.")
            elif aksi == "ganti_nama_varian":
                info = get_object_or_404(VarianInfo, pk=request.POST.get("id"), item_name=item_info.item_name)
                if layanan.ganti_nama_varian(request.user, info, request.POST.get("varian_baru"), hak=hak):
                    messages.success(request, f"Varian diganti menjadi '{info.variant}'.")
            elif aksi == "tambah_varian":
                contoh = Item.objects.filter(item_name=item_info.item_name).values("category", "brand").first() or {}
                layanan.tambah_varian(
                    request.user, hak=hak, item_name=item_info.item_name,
                    variant=request.POST.get("variant"),
                    category=contoh.get("category") or "Uncategorized", brand=contoh.get("brand") or "",
                    min_price=request.POST.get("min_price"), max_price=request.POST.get("max_price"),
                    cost_amount=request.POST.get("cost_amount"), in_stock=request.POST.get("in_stock"),
                    kode_rak=request.POST.get("kode_rak") or None,
                )
                messages.success(request, "Varian ditambahkan.")
            elif aksi == "syarat_titipan":
                from master.layanan import titipan
                if "ubah_harga" not in hak:
                    raise Tolak("Akun ini tidak boleh mengubah syarat titipan.")
                from master.layanan.pembelian import baca_tanggal
                mulai = baca_tanggal(request.POST.get("berlaku_mulai"), label="Tanggal berlaku") or timezone.localdate()
                kini = titipan.syarat_kini(list(VarianInfo.objects.filter(item_name=item_info.item_name).values_list("id", flat=True)), mulai)
                n = 0
                for info in VarianInfo.objects.filter(item_name=item_info.item_name):
                    if f"maks_{info.id}" not in request.POST:
                        continue
                    lama = kini.get(info.id)
                    pot = request.POST.get(f"pot_{info.id}") if "lihat_modal" in hak else getattr(lama, "potongan_toko", None)
                    _, berubah = titipan.simpan_syarat(request.user, info, potongan=pot,
                                                       diskon_maks=request.POST.get(f"maks_{info.id}"), berlaku_mulai=mulai)
                    n += int(berubah)
                messages.success(request, f"Syarat titipan tersimpan ({n} varian berubah)." if n else "Tidak ada yang berubah.")
            elif aksi == "samakan":
                acuan = get_object_or_404(VarianInfo, pk=request.POST.get("acuan"), item_name=item_info.item_name)
                n = layanan.samakan_varian(request.user, item_info, acuan, request.POST.getlist("kolom"), hak=hak)
                messages.success(request, f"{n} harga disamakan dengan varian {acuan.variant}." if n
                                 else "Semua varian sudah sama, tidak ada yang berubah.")
            elif aksi == "pemasok_tambah":
                pemasok.tambah(request.user, item_info, nama_supplier=request.POST.get("supplier"),
                               catatan=request.POST.get("catatan"), hak=hak)
                messages.success(request, "Catatan supplier tersimpan.")
            elif aksi == "pemasok_hapus":
                pemasok.hapus(request.user, item_info, request.POST.get("id"), hak=hak)
                messages.success(request, "Catatan supplier dihapus.")
            elif aksi in ("buang", "buang_semua"):
                from master.layanan import sampah
                if aksi == "buang":
                    target = [get_object_or_404(VarianInfo, pk=request.POST.get("id"), item_name=item_info.item_name)]
                else:
                    target = list(VarianInfo.objects.filter(item_name=item_info.item_name, sampah__isnull=True))
                    terjual = [v.variant for v in target if sampah.baris_nota_kasir(v)]
                    if terjual:
                        raise Tolak("Tidak ada yang dihapus: varian " + ", ".join(terjual) + " sudah pernah terjual di kasir "
                                    "dan belum bisa dihapus. Hapus varian lain satu per satu.")
                for info in target:
                    stok = sampah.buang(request.user, info, hak=hak, alasan=request.POST.get("alasan", ""))
                    messages.success(request, f"{info.item_name} / {info.variant} dibuang ke sampah"
                                     + (f" (stok {stok} ikut tersimpan)." if stok else "."))
                if not Item.objects.filter(item_name=item_info.item_name).exists():
                    if request.headers.get("X-Panel"):
                        return _jawab_panel(request, hilang=True)
                    return redirect(_tanpa_buka(request.POST.get("kembali", ""), request) or "master:barang")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        if request.headers.get("X-Panel"):
            # Panel di daftar barang mengirim lewat fetch: jawab JSON, halaman tidak dimuat ulang.
            return _jawab_panel(request, hilang=False, aksi=aksi)
        kembali = request.POST.get("kembali", "")
        if kembali and url_has_allowed_host_and_scheme(kembali, allowed_hosts={request.get_host()}):
            return HttpResponseRedirect(kembali)
        return redirect("master:item", item_id=item_info.id)

    barang = _lengkapi(
        list(Item.objects.filter(item_name=item_info.item_name).values(*KOLOM_NILAI)), hak
    )
    barang.sort(key=lambda b: kunci_alami(b["variant"]))
    from pos.models import TransactionDetail
    terjual = dict(TransactionDetail.objects.filter(item_name=item_info.item_name)
                   .values_list("variant").annotate(n=Count("sync_id")))
    for b in barang:
        b["terjual"] = terjual.get(b["variant"], 0)
    if not barang:
        messages.error(request, "Barang ini sudah tidak punya varian di tabel item.")
    kepala = barang[0] if barang else {}
    from master.layanan import titipan as svc_titipan
    from master.models import Pengaturan, SyaratTitipan
    pemilik = item_info.toko if svc_titipan.adalah_titipan(item_info.toko) else None
    riwayat_syarat = []
    if pemilik:
        kini = svc_titipan.syarat_kini([b["id"] for b in barang if b.get("id")])
        for b in barang:
            sy = kini.get(b.get("id"))
            b["pot_titipan"] = svc_titipan.persen_teks(sy.potongan_toko) if sy else ""
            b["maks_titipan"] = svc_titipan.persen_teks(sy.diskon_maks) if sy else ""
        for r in SyaratTitipan.objects.filter(varian__item_name=item_info.item_name).select_related("varian")[:40]:
            r.pot, r.maks = svc_titipan.persen_teks(r.potongan_toko), svc_titipan.persen_teks(r.diskon_maks)
            riwayat_syarat.append(r)
    milik_item = svc_titipan.saring_barang(Item.objects.all(), item_info.toko)
    return render(request, "master/_item_isi.html" if panel else "master/item.html", {
        "titipan": pemilik, "riwayat_syarat": riwayat_syarat, "hari_ini": timezone.localdate().isoformat(),
        "pembulatan": Pengaturan.ambil().pembulatan_diskon,
        "menu": "barang",
        "panel": panel,
        "kembali": request.GET.get("kembali", ""),
        "item_info": item_info,
        "barang": barang,
        "barang_aktif": [b for b in barang if b["aktif"]],
        "barang_nonaktif": [b for b in barang if not b["aktif"]],
        "boleh_pilih": bool({"ganti_nama", "ubah_info"} & hak),
        "kategori": kepala.get("category", ""),
        "brand": kepala.get("brand", ""),
        "kategori_campur": len({b["category"] for b in barang}) > 1,
        "brand_campur": len({b["brand"] for b in barang}) > 1,
        "baris_nota": layanan.jumlah_baris_nota(item_info.item_name),
        "stok_total": sum(b["in_stock"] or 0 for b in barang),
        "ada_terjual": any(b["terjual"] for b in barang),
        "daftar_kategori": sorted(set(milik_item.values_list("category", flat=True)), key=kunci_alami),
        "daftar_brand": sorted(set(milik_item.values_list("brand", flat=True)), key=kunci_alami),
        "daftar_rak": Rak.objects.all(),
        "log_item": Log.objects.filter(item_name=item_info.item_name)[:15] if "lihat_log" in hak else [],
        "pemasok": pemasok.untuk_barang(item_info, hak) if pemasok.boleh_lihat(hak) else None,
        "daftar_supplier": Supplier.objects.filter(aktif=True) if pemasok.boleh_lihat(hak) else [],
    })


# ---------------------------------------------------------------------------
# Harga & Modal
# ---------------------------------------------------------------------------

PER_HALAMAN_HARGA = 100


@perlu("ubah_harga")
def harga(request):
    """Tabel harga per varian, untuk mengedit cepat dan mengubah massal."""
    hak = hak_pengguna(request.user)
    layanan.pastikan_info()
    qs = _saring_barang(request, hak)
    ada_filter = any(request.GET.get(k) for k in ("q", "kategori", "brand", "rak", "masalah", "aktif"))
    pratinjau = None
    param = None

    if request.method == "POST":
        try:
            param = layanan.baca_parameter_massal(request.POST, hak)
            if request.POST.get("aksi") == "terapkan":
                jumlah, batch = layanan.terapkan_massal(request.user, qs, param, hak=hak)
                pesan = f"{jumlah} harga diubah."
                messages.success(request, pesan)
                return redirect(request.get_full_path())
            rencana = layanan.rencana_massal(qs, param)
            pratinjau = {
                "jumlah": len(rencana),
                "terbalik": sum(1 for r in rencana if r["terbalik"]),
                "contoh": rencana[:200],
                "label": layanan.KOLOM_VARIAN[param["kolom"]][0],
                "rahasia": param["kolom"] == "cost_amount" and "lihat_modal" not in hak,
            }
        except Tolak as tolak:
            messages.error(request, str(tolak))

    halaman = halaman_alami(qs, KOLOM_NILAI, PER_HALAMAN_HARGA, request.GET.get("hal"), _cari(request))
    barang = _lengkapi(list(halaman.object_list), hak)   # urutan dari halaman_alami (nama cocok dulu)
    return render(request, "master/harga.html", {
        "menu": "harga",
        "barang": barang,
        "halaman": halaman,
        "g": request.GET,
        "ada_filter": ada_filter,
        "qs_halaman": _qs_tanpa(request, "hal"),
        "pratinjau": pratinjau,
        "param": param or request.POST,
        "daftar_kategori": sorted(set(_milik(request).values_list("category", flat=True)), key=kunci_alami),
        "daftar_brand": sorted(set(_milik(request).values_list("brand", flat=True)), key=kunci_alami),
        "daftar_rak": Rak.objects.all(),
        "daftar_masalah": masalah.aturan_untuk(hak),
        "kolom_massal": [(k, layanan.KOLOM_VARIAN[k][0]) for k in layanan.KOLOM_HARGA
                         if layanan.KOLOM_VARIAN[k][2] in hak],
        "cara_massal": layanan.CARA_MASSAL.items(),
        "daftar_bulat": layanan.BULAT,
    })


# ---------------------------------------------------------------------------
# Inventory: stok, riwayat stok, supplier
# ---------------------------------------------------------------------------


@perlu("lihat_stok")
def stok(request):
    hak = hak_pengguna(request.user)
    layanan.pastikan_info()
    qs = _saring_barang(request, hak)
    halaman = halaman_alami(qs, KOLOM_NILAI, PER_HALAMAN_HARGA, request.GET.get("hal"), _cari(request))
    barang = _lengkapi(list(halaman.object_list), hak)   # urutan dari halaman_alami (nama cocok dulu)
    semua = _milik(request)
    return render(request, "master/stok.html", {
        "menu": "stok",
        "barang": barang,
        "halaman": halaman,
        "g": request.GET,
        "qs_halaman": _qs_tanpa(request, "hal"),
        "jumlah_habis": semua.filter(in_stock__lte=0).count(),
        "jumlah_menipis": semua.filter(stock_alert__isnull=False, in_stock__lte=F("stock_alert"), in_stock__gt=0).count(),
        "daftar_kategori": sorted(set(_milik(request).values_list("category", flat=True)), key=kunci_alami),
        "daftar_brand": sorted(set(_milik(request).values_list("brand", flat=True)), key=kunci_alami),
        "daftar_rak": Rak.objects.all(),
        "daftar_jenis": layanan.JENIS_MUTASI.items(),
        "pilihan_lokasi": svc_lokasi.pilihan() if Lokasi.objects.filter(jenis="gudang", aktif=True).exists() else [],
    })


@require_POST
@perlu("ubah_stok")
def stok_ubah(request):
    hak = hak_pengguna(request.user)
    try:
        data = json.loads(request.body.decode("utf-8"))
        info = VarianInfo.objects.get(pk=int(data["id"]))
    except (ValueError, KeyError, TypeError, VarianInfo.DoesNotExist):
        return JsonResponse({"ok": False, "pesan": "Permintaan tidak dikenali."}, status=400)
    try:
        if data.get("lokasi"):
            # Hitungan di satu lokasi: yang diterapkan selisih terhadap stok sistem di lokasi itu.
            from master.layanan import lokasi as svc_lokasi
            lokasi = svc_lokasi.baca(data["lokasi"])
            hitung = layanan.baca_angka(data.get("nilai"), boleh_kosong=False)
            if hitung < 0:
                raise Tolak("Hitungan tidak boleh minus.")
            beda = hitung - svc_lokasi.stok_di(info, lokasi)
            if beda == 0:
                stok_baru = Item.objects.filter(item_name=info.item_name, variant=info.variant).values_list(
                    "in_stock", flat=True).first()
                return JsonResponse({"ok": True, "stok": stok_baru, "selisih": 0})
            stok_baru, selisih = layanan.sesuaikan_stok(
                request.user, info, cara="tambah" if beda > 0 else "kurang", angka=abs(beda),
                jenis=data.get("jenis"), catatan=data.get("catatan") or "", hak=hak, lokasi=lokasi)
        else:
            stok_baru, selisih = layanan.sesuaikan_stok(
                request.user, info, cara=data.get("cara") or "tetapkan", angka=data.get("nilai"),
                jenis=data.get("jenis"), catatan=data.get("catatan") or "", hak=hak,
            )
    except Tolak as tolak:
        return JsonResponse({"ok": False, "pesan": str(tolak)}, status=400)
    return JsonResponse({"ok": True, "stok": stok_baru, "selisih": selisih})


@perlu("lihat_stok")
def stok_riwayat(request):
    """Riwayat stok: perubahan lewat master (MutasiStok) digabung dengan
    penjualan kasir (pos.MutasiKasir), diurutkan menurut waktu."""
    from django.db.models import CharField, IntegerField, Subquery, Value
    from django.db.models.functions import Cast, Coalesce, Concat
    from pos.models import MutasiKasir, Worker

    g = request.GET
    cari = layanan.rapikan_teks(g.get("q"))
    jenis = g.get("jenis") or ""
    pengguna = g.get("pengguna") or ""
    teks = CharField()
    angka = IntegerField()

    def kolom(qs, **isi):
        # Semua kolom dibuat sebagai anotasi dengan urutan yang sama di kedua
        # sisi, supaya UNION tidak menukar kolom.
        urutan = ("r_waktu", "r_oleh", "r_barang", "r_varian", "r_jenis", "r_lokasi",
                  "r_sebelum", "r_jumlah", "r_sesudah", "r_catatan")
        return qs.order_by().annotate(**{k: isi[k] for k in urutan}).values(*urutan)

    master = MutasiStok.objects.all()
    kasir = MutasiKasir.objects.all()
    if cari:
        master = master.filter(Q(item_name__icontains=cari) | Q(variant__icontains=cari) | Q(catatan__icontains=cari))
        kasir = kasir.filter(Q(item_name__icontains=cari) | Q(variant__icontains=cari)
                             | Q(transaction_id__icontains=cari))
    if pengguna:
        master = master.filter(nama_pengguna=pengguna)
        kasir = kasir.filter(worker_id__in=Worker.objects.filter(name=pengguna).values("id"))
    if jenis and jenis != "jual_kasir":
        master = master.filter(jenis=jenis)
    master = kolom(
        master, r_waktu=F("waktu"), r_oleh=F("nama_pengguna"), r_barang=F("item_name"), r_varian=F("variant"),
        r_jenis=F("jenis"), r_lokasi=Cast(F("lokasi__nama"), teks), r_sebelum=F("stok_sebelum"),
        r_jumlah=F("jumlah"), r_sesudah=F("stok_sesudah"), r_catatan=F("catatan"))
    kasir = kolom(
        kasir, r_waktu=F("waktu"),
        r_oleh=Subquery(Worker.objects.filter(id=OuterRef("worker_id")).values("name")[:1], output_field=teks),
        r_barang=F("item_name"), r_varian=F("variant"), r_jenis=Value("jual_kasir", output_field=teks),
        r_lokasi=Value(None, output_field=teks), r_sebelum=Cast(F("stok_sebelum"), angka),
        r_jumlah=Cast(F("jumlah"), angka), r_sesudah=Cast(F("stok_sesudah"), angka),
        r_catatan=Concat(Value("Nota "), Coalesce(F("transaction_id"), Value("")), output_field=teks))
    if jenis == "jual_kasir":
        qs = kasir.order_by("-r_waktu")
    elif jenis:
        qs = master.order_by("-r_waktu")
    else:
        qs = master.union(kasir, all=True).order_by("-r_waktu")
    halaman = Paginator(qs, 100).get_page(g.get("hal"))
    label = dict(MutasiStok.JENIS) | {"jual_kasir": "Penjualan kasir"}
    halaman.object_list = [{
        "waktu": m["r_waktu"], "nama_pengguna": m["r_oleh"] or "", "item_name": m["r_barang"],
        "variant": m["r_varian"], "label": label.get(m["r_jenis"], m["r_jenis"]), "lokasi_nama": m["r_lokasi"],
        "stok_sebelum": m["r_sebelum"], "jumlah": m["r_jumlah"], "stok_sesudah": m["r_sesudah"],
        "catatan": m["r_catatan"],
    } for m in halaman.object_list]
    nama = set(MutasiStok.objects.values_list("nama_pengguna", flat=True)) | set(Worker.objects.values_list("name", flat=True))
    return render(request, "master/stok_riwayat.html", {
        "menu": "stok_riwayat", "halaman": halaman, "g": g,
        "qs_halaman": _qs_tanpa(request, "hal"),
        "daftar_jenis": [("jual_kasir", "Penjualan kasir"), *MutasiStok.JENIS],
        "daftar_pengguna": sorted(n for n in nama if n),
    })


@perlu("lihat_stok")
def pergerakan(request):
    """Laporan pergerakan stok (masuk, keluar, penyesuaian) dan barang yang tidak laku."""
    from master.layanan import laporan as svc_laporan, pergerakan as svc
    g = request.GET.copy()
    if not g.get("periode"):
        g["periode"] = "bulan"
    periode = svc_laporan.baca_periode(g)
    mulai, selesai = svc_laporan.rentang(periode["awal"], periode["akhir"])
    kelompok, per_kategori, laporan, total = svc.ringkasan(mulai, selesai)
    try:
        hari = int(request.GET.get("hari") or 60)
    except ValueError:
        hari = 60
    hari = hari if hari in (30, 60, 90, 180) else 60
    lihat_modal = "lihat_modal" in hak_pengguna(request.user)
    lambat, jumlah_lambat, nilai_lambat = svc.tidak_laku(hari, lihat_modal=lihat_modal)
    return render(request, "master/pergerakan.html", {
        "menu": "pergerakan", "periode": periode, "kelompok": kelompok, "per_kategori": per_kategori,
        "laporan": laporan, "total": total, "baris": {b["kunci"]: b for b in laporan},
        "hari": hari, "pilihan_hari": [30, 60, 90, 180], "lambat": lambat, "jumlah_lambat": jumlah_lambat, "nilai_lambat": nilai_lambat,
        "lihat_modal": lihat_modal,
    })


@perlu("kelola_supplier")
def supplier(request):
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        nama = layanan.rapikan_teks(request.POST.get("nama"))
        data = {
            "kontak": layanan.rapikan_teks(request.POST.get("kontak")),
            "telepon": layanan.rapikan_teks(request.POST.get("telepon")),
            "alamat": layanan.rapikan_teks(request.POST.get("alamat")),
            "pkp": request.POST.get("pkp") == "1",
            "npwp": layanan.rapikan_teks(request.POST.get("npwp")),
            "catatan": layanan.rapikan_teks(request.POST.get("catatan")),
        }
        try:
            if aksi == "tambah":
                if not nama:
                    raise Tolak("Nama supplier wajib diisi.")
                if Supplier.objects.filter(nama__iexact=nama).exists():
                    raise Tolak("Supplier dengan nama itu sudah ada.")
                Supplier.objects.create(nama=nama, **data)
                layanan.catat(request.user, aksi="tambah", objek="supplier", kolom="supplier", baru=nama)
                messages.success(request, f"Supplier {nama} ditambahkan.")
            else:
                objek = get_object_or_404(Supplier, pk=request.POST.get("id"))
                if aksi == "simpan":
                    if not nama:
                        raise Tolak("Nama supplier wajib diisi.")
                    if Supplier.objects.filter(nama__iexact=nama).exclude(pk=objek.pk).exists():
                        raise Tolak("Supplier dengan nama itu sudah ada.")
                    lama = objek.nama
                    objek.nama = nama
                    for k, v in data.items():
                        setattr(objek, k, v)
                    objek.save()
                    layanan.catat(request.user, objek="supplier", kolom="supplier", lama=lama, baru=nama)
                    messages.success(request, f"Supplier {nama} tersimpan.")
                elif aksi == "aktif":
                    objek.aktif = not objek.aktif
                    objek.save(update_fields=["aktif"])
                    layanan.catat(request.user, objek="supplier", kolom="aktif",
                                  baru=f"{objek.nama}: {'aktif' if objek.aktif else 'nonaktif'}")
                    messages.success(request, f"{objek.nama} sekarang {'aktif' if objek.aktif else 'nonaktif'}.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:supplier")
    buka = request.GET.get("buka")
    return render(request, "master/supplier.html", {
        "menu": "supplier", "daftar": Supplier.objects.all(),
        "dibuka": Supplier.objects.filter(pk=buka).first() if buka and buka.isdigit() else None,
    })


# ---------------------------------------------------------------------------
# Kategori dan brand
# ---------------------------------------------------------------------------


@perlu()
def kelompok(request, kolom):
    hak = hak_pengguna(request.user)
    judul = "Kategori" if kolom == "category" else "Brand"
    param = "kategori" if kolom == "category" else "brand"
    if request.method == "POST":
        try:
            jumlah = layanan.ganti_nama_kelompok(
                request.user, kolom, request.POST.get("lama", ""), request.POST.get("baru", ""), hak=hak,
                qs=_milik(request),
            )
            messages.success(request, f"{judul} diganti di {jumlah} varian.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect(request.path)

    baris = list(
        _milik(request).values(kolom)
        .annotate(varian=Count("item_name"), item=Count("item_name", distinct=True))
        .order_by(kolom)
    )
    cari = layanan.rapikan_teks(request.GET.get("q")).lower()
    daftar = [
        {"nama": b[kolom] or "", "varian": b["varian"], "item": b["item"],
         "tautan": reverse("master:barang") + "?" + urlencode({param: b[kolom] or ""})}
        for b in baris
        if not cari or cari in (b[kolom] or "").lower()
    ]
    daftar.sort(key=lambda d: kunci_alami(d["nama"]))
    return render(request, "master/kelompok.html", {
        "menu": param, "judul": judul, "daftar": daftar, "cari": request.GET.get("q", ""),
    })


# ---------------------------------------------------------------------------
# Rak
# ---------------------------------------------------------------------------


@perlu()
def rak(request):
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        if "kelola_rak" not in hak:
            messages.error(request, "Akun ini tidak boleh mengelola rak.")
            return redirect("master:rak")
        aksi = request.POST.get("aksi")
        kode = layanan.rapikan_teks(request.POST.get("kode"))
        if aksi == "simpan":
            nama = layanan.rapikan_teks(request.POST.get("nama"))
            if not kode or not nama:
                messages.error(request, "Kode dan nama rak wajib diisi.")
            elif len(kode) > 20:
                messages.error(request, "Kode rak maksimal 20 karakter.")
            else:
                from master.models import Lokasi
                tempat = Lokasi.objects.filter(pk=request.POST.get("tempat") or 0).first()
                _, dibuat = Rak.objects.update_or_create(kode=kode, defaults={
                    "nama": nama, "tempat": tempat,
                    "lokasi": layanan.rapikan_teks(request.POST.get("lokasi")),
                    "keterangan": layanan.rapikan_teks(request.POST.get("keterangan")),
                })
                layanan.catat(request.user, aksi="tambah" if dibuat else "ubah", objek="rak",
                              kolom="rak", baru=f"{kode} - {nama}")
                messages.success(request, f"Rak {kode} tersimpan.")
        elif aksi == "hapus":
            objek = Rak.objects.filter(kode=kode).first()
            if objek and objek.varian.exists():
                messages.error(request, f"Rak {kode} masih dipakai {objek.varian.count()} varian.")
            elif objek:
                objek.delete()
                layanan.catat(request.user, aksi="hapus", objek="rak", kolom="rak", lama=kode)
                messages.success(request, f"Rak {kode} dihapus.")
        return redirect("master:rak")

    from master.models import Lokasi
    daftar = Rak.objects.select_related("tempat").annotate(jumlah=Count("varian", filter=Q(varian__sampah__isnull=True)))
    return render(request, "master/rak.html", {
        "menu": "rak", "daftar": daftar, "daftar_lokasi": Lokasi.objects.filter(aktif=True),
        "tanpa_rak": VarianInfo.objects.filter(rak__isnull=True, sampah__isnull=True).count(),
    })


# ---------------------------------------------------------------------------
# Impor Excel
# ---------------------------------------------------------------------------


@perlu("impor")
def impor(request):
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        berkas = request.FILES.get("berkas")
        if not berkas:
            messages.error(request, "Pilih berkas Excel dulu.")
            return redirect("master:impor")
        from master.models import Toko
        tujuan = Toko.objects.filter(pk=request.POST.get("toko") if str(request.POST.get("toko")).isdigit() else 0,
                                     aktif=True).first()
        if tujuan is None:
            messages.error(request, "Pilih dulu barang ini untuk toko mana.")
            return redirect("master:impor")
        try:
            layanan.pastikan_info()
            isi = excel.baca_berkas(berkas, hak, toko=tujuan)
        except Tolak as tolak:
            messages.error(request, str(tolak))
            return redirect("master:impor")
        objek = Impor.objects.create(pengguna=request.user, nama_berkas=berkas.name[:255], isi=isi)
        return redirect("master:impor_pratinjau", impor_id=objek.id)
    from master.layanan import toko as svc_toko
    return render(request, "master/impor.html", {
        "menu": "impor", "pilihan_toko": svc_toko.boleh_untuk(request.user),
        "riwayat": [i for i in Impor.objects.select_related("pengguna")[:60] if i.isi.get("jenis") != "stok"][:15],
    })


@perlu("impor")
def impor_pratinjau(request, impor_id):
    hak = hak_pengguna(request.user)
    objek = get_object_or_404(Impor, pk=impor_id)
    if objek.isi.get("jenis") == "stok":
        return redirect("master:stok_impor_pratinjau", impor_id=objek.id)
    if request.method == "POST" and objek.status == "pratinjau":
        if request.POST.get("aksi") == "terapkan":
            hasil = excel.terapkan(objek, request.user, hak, izinkan_baru=request.POST.get("izinkan_baru") == "1")
            messages.success(
                request,
                f"Diterapkan: {hasil['diubah']} sel, {hasil['alias']} alias, "
                f"{hasil['baru']} barang baru, {hasil['rak']} rak"
                + (f", {hasil['syarat']} syarat titipan" if hasil.get("syarat") else "") + ".",
            )
        else:
            objek.status = "batal"
            objek.save(update_fields=["status"])
            messages.info(request, "Impor dibatalkan. Tidak ada yang berubah.")
        return redirect("master:impor_pratinjau", impor_id=objek.id)

    isi = objek.isi
    per_kolom = {}
    for p in isi.get("perubahan", []):
        per_kolom[p["kolom"]] = per_kolom.get(p["kolom"], 0) + 1
    ringkas_kolom = [
        {"label": layanan.KOLOM_VARIAN[k][0], "jumlah": n} for k, n in sorted(per_kolom.items(), key=lambda x: -x[1])
    ]
    for p in isi.get("perubahan", []):
        p["label"] = layanan.KOLOM_VARIAN[p["kolom"]][0]
    return render(request, "master/impor_pratinjau.html", {
        "menu": "impor", "impor": objek, "isi": isi, "ringkas_kolom": ringkas_kolom,
        "contoh_perubahan": isi.get("perubahan", [])[:300],
        "contoh_alias": isi.get("alias", [])[:200],
        "contoh_galat": isi.get("galat", [])[:200],
    })


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------


@perlu("lihat_log")
def log(request):
    hak = hak_pengguna(request.user)
    qs = Log.objects.all()
    cari = layanan.rapikan_teks(request.GET.get("q"))
    if cari:
        qs = qs.filter(Q(item_name__icontains=cari) | Q(variant__icontains=cari)
                       | Q(lama__icontains=cari, rahasia=False) | Q(baru__icontains=cari, rahasia=False))
    if request.GET.get("kolom"):
        qs = qs.filter(kolom=request.GET["kolom"])
    if request.GET.get("pengguna"):
        qs = qs.filter(nama_pengguna=request.GET["pengguna"])
    if request.GET.get("sumber"):
        qs = qs.filter(sumber=request.GET["sumber"])
    if request.GET.get("batch"):
        qs = qs.filter(batch=request.GET["batch"])
    halaman = Paginator(qs, 100).get_page(request.GET.get("hal"))
    label = {k: v[0] for k, v in layanan.KOLOM_VARIAN.items()}
    label.update({"alias": "Alias", "item_name": "Nama barang", "variant": "Varian", "rak": "Rak"})
    for baris in halaman.object_list:
        baris.label_kolom = label.get(baris.kolom, baris.kolom)
        baris.sembunyi = baris.rahasia and "lihat_modal" not in hak
    return render(request, "master/log.html", {
        "menu": "log", "halaman": halaman, "g": request.GET,
        "qs_halaman": _qs_tanpa(request, "hal"),
        "daftar_kolom": sorted(label.items(), key=lambda x: x[1]),
        "daftar_pengguna": Log.objects.order_by("nama_pengguna").values_list("nama_pengguna", flat=True).distinct(),
    })


# ---------------------------------------------------------------------------
# Pengguna
# ---------------------------------------------------------------------------


TEMPLAT_HAK = {
    "pemilik": ("Pemilik (semua hak)", None),
    "kepala_gudang": ("Kepala gudang", ["gudang", "gudang_datang", "gudang_hitung", "gudang_buka_dus", "gudang_ambil"]),
    "admin_gudang": ("Admin gudang", ["gudang", "ops_lihat_stok", "ops_tiket_pindah", "ops_tiket_rak"]),
    "admin_barang": ("Admin barang", ["gudang", "ops_barang_harga", "ops_barang_alias", "ops_barang_aktif"]),
    "kosong": ("Kosong (centang sendiri)", []),
}


def _simpan_hak(pengguna, akun, *, semua, hak, toko=None):
    from master.akun.akses import SEMUA_HAK
    from master.models import HakAkun, Toko
    hak = sorted(set(hak) & SEMUA_HAK)
    if akun == pengguna and not akun.is_superuser and not semua and not {"master", "kelola_pengguna"} <= set(hak):
        raise Tolak("Tidak bisa mencabut hak membuka master atau mengelola pengguna dari akun sendiri.")
    nilai = {"semua": bool(semua), "hak": [] if semua else hak}
    if toko is not None:
        ada = set(Toko.objects.values_list("id", flat=True))
        nilai["toko"] = sorted({int(t) for t in toko if str(t).isdigit()} & ada)
    HakAkun.objects.update_or_create(user=akun, defaults=nilai)
    akun.__dict__.pop("_hak_master", None)
    layanan.catat(pengguna, objek="pengguna", kolom="hak",
                  baru=f"{akun.username}: " + ("semua hak (pemilik)" if semua else (", ".join(hak) or "tanpa hak")))


@perlu("kelola_pengguna")
def pengguna(request):
    from master.akun.akses import LABEL_HAK
    from master.models import HakAkun
    Pengguna = get_user_model()
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "tambah":
                nama = layanan.rapikan_teks(request.POST.get("username")).lower()
                sandi = request.POST.get("password") or ""
                templat = request.POST.get("templat")
                if not nama or " " in nama:
                    raise Tolak("Nama pengguna wajib diisi, tanpa spasi.")
                if len(sandi) < 8:
                    raise Tolak("Kata sandi minimal 8 karakter.")
                if templat not in TEMPLAT_HAK:
                    raise Tolak("Pilih templat hak.")
                if Pengguna.objects.filter(username=nama).exists():
                    raise Tolak("Nama pengguna sudah dipakai.")
                akun = Pengguna.objects.create_user(username=nama, password=sandi)
                label, hak = TEMPLAT_HAK[templat]
                _simpan_hak(request.user, akun, semua=hak is None, hak=hak or [])
                layanan.catat(request.user, aksi="tambah", objek="pengguna", kolom="pengguna", baru=f"{nama} ({label})")
                messages.success(request, f"Pengguna {nama} dibuat. Atur haknya kalau perlu.")
            else:
                akun = get_object_or_404(Pengguna, pk=request.POST.get("id"))
                if aksi == "hak":
                    if akun.is_superuser:
                        raise Tolak("Akun pemilik utama selalu punya semua hak.")
                    _simpan_hak(request.user, akun, semua=request.POST.get("semua") == "1",
                                hak=request.POST.getlist("hak"), toko=request.POST.getlist("toko"))
                    messages.success(request, f"Hak {akun.username} disimpan.")
                elif aksi == "sandi":
                    sandi = request.POST.get("password") or ""
                    if len(sandi) < 8:
                        raise Tolak("Kata sandi minimal 8 karakter.")
                    akun.set_password(sandi)
                    akun.save()
                    layanan.catat(request.user, objek="pengguna", kolom="sandi", baru=akun.username)
                    messages.success(request, f"Kata sandi {akun.username} diganti.")
                elif aksi == "aktif":
                    if akun == request.user:
                        raise Tolak("Tidak bisa menonaktifkan akun sendiri.")
                    akun.is_active = not akun.is_active
                    akun.save(update_fields=["is_active"])
                    layanan.catat(request.user, objek="pengguna", kolom="aktif",
                                  baru=f"{akun.username}: {'aktif' if akun.is_active else 'nonaktif'}")
                    messages.success(request, f"{akun.username} sekarang {'aktif' if akun.is_active else 'nonaktif'}.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:pengguna")

    from master.models import Toko
    semua_toko = list(Toko.objects.filter(aktif=True))
    daftar = []
    for akun in Pengguna.objects.order_by("username"):
        punya = hak_pengguna(akun) if akun.is_active else set()
        baris = HakAkun.objects.filter(user=akun).first()
        semua = akun.is_superuser or bool(baris and baris.semua)
        izin = [int(t) for t in (baris.toko if baris else [])]
        daftar.append({
            "akun": akun, "semua": semua, "utama": akun.is_superuser, "punya": punya, "toko_izin": izin,
            "nama_toko": ", ".join(t.nama for t in semua_toko if t.pk in izin) or "semua toko",
            "ringkas": "Pemilik utama" if akun.is_superuser else ("Pemilik (semua hak)" if semua else f"{len(punya)} hak"),
        })
    return render(request, "master/pengguna.html", {
        "menu": "pengguna", "daftar": daftar, "label_hak": LABEL_HAK, "semua_toko": semua_toko,
        "templat": [(k, v[0]) for k, v in TEMPLAT_HAK.items()],
    })


@perlu()
def tema(request):
    """Halaman Tampilan: pilih warna tema untuk akun ini."""
    from master.models import PreferensiAkun
    if request.method == "POST":
        pilihan = request.POST.get("tema")
        if pilihan in dict(PreferensiAkun.TEMA):
            PreferensiAkun.objects.update_or_create(user=request.user, defaults={"tema": pilihan})
            messages.success(request, "Tema disimpan.")
        return redirect("master:tema")
    return render(request, "master/tampilan.html", {"menu": "tampilan", "pilihan": PreferensiAkun.TEMA})


@perlu()
def pilih_toko(request):
    """Ganti toko yang sedang dikerjakan (menu samping), lalu kembali ke halaman asal."""
    from master.layanan.toko import pilih_toko as pilih
    if request.method == "POST":
        try:
            t = pilih(request, request.POST.get("toko"))
            messages.info(request, f"Sekarang mengerjakan {t.nama}.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
    kembali = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(kembali, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        kembali = reverse("master:dashboard")
    return redirect(kembali)
