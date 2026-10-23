from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from master.layanan.toko import saring as saring_toko
from master.layanan import opname as svc
from master.akun.akses import hak_pengguna, perlu
from master.layanan.inti import Tolak
from master.models import Opname


@perlu("opname")
def daftar(request):
    qs = saring_toko(Opname.objects.exclude(status="draf"), request).select_related("toko").annotate(n=Count("baris"))
    saring = request.GET.get("status", "")
    if saring in dict(Opname.STATUS):
        qs = qs.filter(status=saring)
    return render(request, "master/opname.html", {
        "menu": "opname", "daftar": qs[:100], "saring": saring, "status": [s for s in Opname.STATUS if s[0] != "draf"],
        "sedang": Opname.objects.filter(status="draf").annotate(n=Count("baris")).filter(n__gt=0),
    })


@perlu("opname")
def detail(request, opname_id):
    op = get_object_or_404(Opname.objects.exclude(status="draf"), pk=opname_id)
    if request.method == "POST":
        try:
            if request.POST.get("aksi") == "acc":
                svc.acc(request.user, op, request.POST.getlist("pilih"), hak=hak_pengguna(request.user))
                messages.success(request, "Opname di-ACC. Stok sudah disesuaikan dan tercatat di riwayat stok.")
            elif request.POST.get("aksi") == "tolak":
                svc.tolak(request.user, op, request.POST.get("alasan"), hak=hak_pengguna(request.user))
                messages.info(request, "Opname ditolak.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:opname_detail", opname_id=op.id)
    baris = svc.rincian(op)
    beda = [b for b in baris if b.selisih]
    return render(request, "master/opname_detail.html", {
        "menu": "opname", "op": op, "baris": baris, "jumlah_beda": len(beda),
        "punya_stok": op.toko is None or op.toko.stok_di_kasir,
        "nilai_hitung": sum(b.nilai_hitung or 0 for b in baris),
        "nilai_selisih": sum(b.nilai_selisih or 0 for b in beda),
    })
