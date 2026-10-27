from django.contrib import messages
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from pos.models import Item

from master.layanan.toko import saring as saring_toko, toko_aktif
from master.layanan import inti as layanan
from master.layanan import lokasi as svc_lokasi
from master.layanan import nota_manual as svc
from master.akun.akses import perlu
from master.layanan.inti import Tolak
from master.models import FotoNota, ItemInfo, NotaManual, VarianInfo
from master.views.utama import _qs_tanpa, kunci_alami


@perlu("laporan")
def daftar(request):
    if request.method == "POST":
        try:
            n, lewat = svc.ubah_tanggal_massal(request.user, request.POST.getlist("pilih"), request.POST.get("tanggal"),
                                               request.POST.get("alasan"))
            messages.success(request, f"Tanggal {n} nota diganti." + (f" Dilewati karena batal: {', '.join(lewat)}." if lewat else ""))
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect(request.get_full_path())
    qs = NotaManual.objects.select_related("toko").prefetch_related("baris")
    g = request.GET
    qs = saring_toko(qs, request)
    if g.get("status") == "batal":
        qs = qs.filter(batal=True)
    elif g.get("status") != "semua":
        qs = qs.filter(batal=False)
    if g.get("tanggal"):
        try:
            from master.layanan.pembelian import baca_tanggal
            qs = qs.filter(tanggal=baca_tanggal(g.get("tanggal"), wajib=True, label="Tanggal"))
        except Tolak as tolak:
            messages.error(request, str(tolak))
    cari = layanan.rapikan_teks(g.get("q"))
    for kata in cari.split(" ") if cari else []:
        qs = qs.filter(Q(nomor__icontains=kata) | Q(nomor_tulisan__icontains=kata) | Q(pembeli__icontains=kata)
                       | Q(baris__item_name__icontains=kata)).distinct()
    halaman = Paginator(qs, 50).get_page(g.get("hal"))
    return render(request, "master/nota_manual.html", {
        "menu": "nota_manual", "halaman": halaman, "g": g, "qs_halaman": _qs_tanpa(request, "hal"),
        "hari_ini": timezone.localdate().isoformat(),
    })


@perlu("laporan")
def baru(request):
    if request.method == "POST":
        try:
            n = svc.buat(request.user, request.POST, request.FILES.getlist("foto"))
            messages.success(request, f"Nota {n.nomor} tersimpan, total " + f"{n.total:,}".replace(",", ".") + ".")
            if request.POST.get("lagi") == "1":
                return redirect(f"{request.path}?toko={n.toko_id}&tanggal={n.tanggal:%Y-%m-%d}")
            return redirect("master:nota_manual_detail", nota_id=n.id)
        except Tolak as tolak:
            messages.error(request, str(tolak))
    return render(request, "master/nota_manual_baru.html", {
        "menu": "nota_manual", "kirim": request.POST, "cara": NotaManual.CARA, "maks_foto": svc.MAKS_FOTO,
        "tanggal": request.POST.get("tanggal") or request.GET.get("tanggal") or timezone.localdate().isoformat(),
        # Tanggal terbawa dari nota sebelumnya ("Simpan dan input nota berikutnya"): disorot supaya tidak lupa diganti.
        "tanggal_terbawa": not request.POST.get("tanggal") and bool(request.GET.get("tanggal")),
        "toko_pilih": request.POST.get("toko") or request.GET.get("toko") or "",
        "pilihan_lokasi": svc_lokasi.pilihan(),
    })


@perlu("laporan")
def cari(request):
    """Cari barang untuk nota manual: harga jual, floor, dan stok kasir."""
    teks = layanan.rapikan_teks(request.GET.get("q"))
    if len(teks) < 2:
        return JsonResponse({"hasil": []})
    qs = Item.objects.all()
    for kata in teks.split(" "):
        qs = qs.filter(Q(item_name__icontains=kata) | Q(variant__icontains=kata) | Q(brand__icontains=kata)
                       | Q(item_name__in=ItemInfo.objects.filter(alias__icontains=kata).values("item_name")))
    from master.layanan import cari as svc_cari
    # Yang cocok lewat nama didahulukan SEBELUM dipotong, supaya tidak tergeser hasil alias.
    barang = list(qs.annotate(t=svc_cari.tingkat(teks, lain=("brand",))).order_by("t", "item_name", "variant")
                  .values("item_name", "variant", "in_stock", "min_price", "max_price", "t")[:60])
    barang.sort(key=lambda b: (b["t"], kunci_alami(b["item_name"]), kunci_alami(b["variant"])))
    info = {(v.item_name, v.variant): v for v in VarianInfo.objects.filter(item_name__in={b["item_name"] for b in barang[:25]})}
    hasil = []
    for b in barang[:25]:
        v = info.get((b["item_name"], b["variant"]))
        if v is None or not v.aktif:
            continue
        hasil.append({"id": v.id, "nama": b["item_name"], "varian": "" if b["variant"] == "N/A" else b["variant"],
                      "stok": b["in_stock"], "harga": b["max_price"], "floor": b["min_price"]})
    return JsonResponse({"hasil": hasil})


@perlu("laporan")
def detail(request, nota_id):
    n = get_object_or_404(NotaManual.objects.select_related("toko"), pk=nota_id)
    if request.method == "POST":
        try:
            if request.POST.get("aksi") == "ubah":
                data = {k: request.POST.get(k, "") for k in svc.LABEL_UBAH}
                kolom = svc.ubah(request.user, n, data, request.POST.get("alasan"))
                if kolom:
                    messages.success(request, "Tersimpan: " + ", ".join(svc.LABEL_UBAH[k].lower() for k in kolom) + ".")
                else:
                    messages.info(request, "Tidak ada yang berubah.")
            else:
                svc.batalkan(request.user, n, request.POST.get("alasan"))
                messages.info(request, "Nota dibatalkan." + (" Stok sudah dikembalikan." if n.toko.stok_di_kasir else ""))
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:nota_manual_detail", nota_id=n.id)
    baris = list(n.baris.all())
    floor = {(i, v): f for i, v, f in Item.objects.filter(item_name__in={b.item_name for b in baris})
             .values_list("item_name", "variant", "min_price")}
    for b in baris:
        b.modal = b.modal_satuan * b.jumlah if b.modal_satuan is not None else None
        b.laba = b.subtotal - b.modal if b.modal is not None else None
        b.di_bawah_floor = floor.get((b.item_name, b.variant)) is not None and b.harga < floor[(b.item_name, b.variant)]
    modal = sum(b.modal or 0 for b in baris)
    return render(request, "master/nota_manual_detail.html", {
        "menu": "nota_manual", "n": n, "baris": baris, "modal": modal, "laba": n.total_barang - modal,
        "modal_lengkap": all(b.modal is not None for b in baris), "foto": n.foto.all(),
        "cara": NotaManual.CARA, "hari_ini": timezone.localdate().isoformat(),
    })


@perlu("laporan")
def foto(request, foto_id):
    berkas = get_object_or_404(FotoNota, pk=foto_id)
    if not default_storage.exists(berkas.object_key):
        raise Http404("Berkas foto tidak ada di server ini.")
    return FileResponse(default_storage.open(berkas.object_key, "rb"), content_type=berkas.content_type)
