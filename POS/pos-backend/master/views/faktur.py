from django.contrib import messages
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from master.layanan.toko import saring as saring_toko, toko_aktif
from master.layanan import faktur as svc
from master.layanan import pembelian as svc_beli
from master.akun.akses import perlu
from master.layanan.inti import Tolak, rapikan_teks
from master.models import FakturManual, FotoFaktur, Pembayaran, Supplier
from master.views.utama import _qs_tanpa


@perlu("pembelian")
def daftar(request):
    qs = FakturManual.objects.select_related("supplier", "toko")
    hari_ini = timezone.localdate()
    qs = saring_toko(qs, request)
    status = request.GET.get("status", "")
    if status == "belum":
        qs = qs.filter(terbayar__lt=F("total")).order_by(F("jatuh_tempo").asc(nulls_last=True), "id")
    elif status == "lewat":
        qs = qs.filter(terbayar__lt=F("total"), jatuh_tempo__lt=hari_ini).order_by("jatuh_tempo", "id")
    elif status == "lunas":
        qs = qs.filter(terbayar__gte=F("total"))
    if request.GET.get("supplier"):
        qs = qs.filter(supplier_id=request.GET["supplier"])
    cari = rapikan_teks(request.GET.get("q"))
    for kata in cari.split(" ") if cari else []:
        qs = qs.filter(Q(nomor_faktur__icontains=kata) | Q(keterangan__icontains=kata)
                       | Q(supplier__nama__icontains=kata) | Q(catatan__icontains=kata))
    halaman = Paginator(qs, 50).get_page(request.GET.get("hal"))
    return render(request, "master/faktur.html", {
        "menu": "faktur", "halaman": halaman, "g": request.GET, "qs_halaman": _qs_tanpa(request, "hal"),
        "daftar_supplier": Supplier.objects.filter(faktur_manual__isnull=False).distinct(), "hari_ini": hari_ini,
    })


@perlu("pembelian")
def baru(request):
    if request.method == "POST":
        try:
            f = svc.buat(request.user, request.POST, request.FILES.getlist("foto"))
            messages.success(request, f"Faktur {f.nomor_faktur} tersimpan.")
            if request.POST.get("lagi") == "1":
                return redirect("master:faktur_baru")
            return redirect("master:faktur_detail", faktur_id=f.id)
        except Tolak as tolak:
            messages.error(request, str(tolak))
    return render(request, "master/faktur_baru.html", {
        "menu": "faktur", "kirim": request.POST, "hari_ini": timezone.localdate(),
        "daftar_supplier": Supplier.objects.filter(aktif=True), "cara_bayar": Pembayaran.CARA,
        "maks_foto": svc.MAKS_FOTO,
    })


@perlu("pembelian")
def detail(request, faktur_id):
    f = get_object_or_404(FakturManual.objects.select_related("supplier"), pk=faktur_id)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "ubah":
                messages.success(request, "Tersimpan." if svc.ubah(request.user, f, request.POST) else "Tidak ada yang berubah.")
            elif aksi == "bayar":
                svc.bayar(request.user, f, tanggal=request.POST.get("tanggal"), jumlah=request.POST.get("jumlah"),
                          cara=request.POST.get("cara"), catatan=request.POST.get("catatan"))
                messages.success(request, "Pembayaran dicatat.")
            elif aksi == "foto":
                svc.tambah_foto(request.user, f, request.FILES.getlist("foto"))
                messages.success(request, "Foto ditambahkan.")
            elif aksi == "hapus_foto":
                svc.hapus_foto(request.user, f, request.POST.get("id"))
                messages.info(request, "Foto dihapus.")
            elif aksi == "hapus":
                svc.hapus(request.user, f)
                messages.info(request, "Faktur dihapus.")
                return redirect("master:faktur")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:faktur_detail", faktur_id=f.id)
    hari_ini = timezone.localdate()
    return render(request, "master/faktur_detail.html", {
        "menu": "faktur", "f": f, "foto": f.foto.all(),
        "isian": {"toko": f.toko_id, "supplier": f.supplier.nama, "nomor_faktur": f.nomor_faktur, "tanggal_faktur": f.tanggal_faktur.isoformat(),
                  "jatuh_tempo": f.jatuh_tempo.isoformat() if f.jatuh_tempo else "", "total": f"{f.total:,}".replace(",", "."),
                  "keterangan": f.keterangan, "catatan": f.catatan}, "pembayaran": f.pembayaran.all(),
        "daftar_supplier": Supplier.objects.filter(aktif=True), "cara_bayar": Pembayaran.CARA,
        "hari_ini": hari_ini, "maks_foto": svc.MAKS_FOTO,
        "terlambat": bool(f.sisa > 0 and f.jatuh_tempo and f.jatuh_tempo < hari_ini),
    })


@perlu("pembelian")
def foto(request, foto_id):
    berkas = get_object_or_404(FotoFaktur, pk=foto_id)
    if not default_storage.exists(berkas.object_key):
        raise Http404("Berkas foto tidak ada di server ini.")
    return FileResponse(default_storage.open(berkas.object_key, "rb"), content_type=berkas.content_type)


@perlu("pembelian")
def hutang(request):
    hari_ini = timezone.localdate()
    from master.models import Toko
    toko = toko_aktif(request)
    daftar = svc_beli.hutang_terbuka(toko)
    for h in daftar:
        h.manual = isinstance(h, FakturManual)
        h.lewat = bool(h.jatuh_tempo and h.jatuh_tempo < hari_ini)
    return render(request, "master/hutang.html", {
        "menu": "hutang", "daftar": daftar, "ringkas": svc_beli.ringkasan_hutang(hari_ini, toko), "hari_ini": hari_ini,
        "toko": toko,
    })
