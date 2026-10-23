from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from master.layanan import sampah as svc
from master.akun.akses import hak_pengguna, perlu
from master.layanan.inti import Tolak
from master.models import ItemInfo, Sampah


@perlu(svc.HAK)
def daftar(request):
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        objek = get_object_or_404(Sampah.objects.select_related("varian"), pk=request.POST.get("id"))
        nama = f"{objek.varian.item_name} / {objek.varian.variant}"
        try:
            if request.POST.get("aksi") == "pulihkan":
                info = svc.pulihkan(request.user, objek, hak=hak)
                item = ItemInfo.objects.filter(item_name=info.item_name).first()
                messages.success(request, f"{nama} dipulihkan dan muncul lagi di kasir.")
                if item and request.POST.get("buka") == "1":
                    return redirect("master:item", item_id=item.id)
            elif request.POST.get("aksi") == "hapus":
                svc.hapus_permanen(request.user, objek, hak=hak, password=request.POST.get("password"))
                messages.success(request, f"{nama} dihapus permanen.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:sampah")

    q = (request.GET.get("q") or "").strip().lower()
    daftar = []
    for s in Sampah.objects.select_related("varian"):
        v = s.varian
        if q and q not in f"{v.item_name} {v.variant} {s.data_item.get('sku') or ''}".lower():
            continue
        riwayat = svc.riwayat(v)
        daftar.append({
            "id": s.id, "item_name": v.item_name, "variant": v.variant, "waktu": s.waktu, "oleh": s.oleh,
            "alasan": s.alasan, "kategori": s.data_item.get("category") or "", "brand": s.data_item.get("brand") or "",
            "stok": s.data_item.get("in_stock") or 0, "harga": s.data_item.get("max_price"),
            "riwayat": riwayat,
        })
    return render(request, "master/sampah.html", {
        "menu": "sampah",
        "daftar": daftar,
        "q": request.GET.get("q", ""),
        "jumlah": Sampah.objects.count(),
    })
