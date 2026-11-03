"""Kas laci toko di master: keadaan kas sekarang, buka/tutup oleh pemilik, uang keluar, ACC tutup kas."""
import uuid

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from master.akun.akses import perlu
from master.layanan import kas as svc
from master.layanan.inti import Tolak
from master.layanan.toko import toko_aktif


@perlu("kas")
def halaman(request):
    toko = toko_aktif(request)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "buka":
                svc.buka(request.user, toko, request.POST)
                messages.success(request, "Kas dibuka.")
            elif aksi == "tutup":
                s = svc.tutup(request.user, request.POST.get("sesi_id"), request.POST)
                messages.success(request, "Kas ditutup. Selisih " + svc.rp(s.selisih) + ".")
                return redirect("master:kas_detail", sesi_id=s.pk)
            elif aksi == "gerak":
                g = svc.catat_gerak(request.user, toko, request.POST)
                messages.success(request, f"{g.get_jenis_display()} {svc.rp(g.jumlah)} tercatat.")
            elif aksi == "batal":
                svc.batal_gerak(request.user, request.POST.get("gerak_id"), request.POST.get("alasan"))
                messages.success(request, "Catatan kas dibatalkan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:kas")
    return render(request, "master/kas.html", {
        "menu": "kas", "toko": toko, "k": svc.ringkas(toko), "riwayat": svc.riwayat(toko),
        "kategori": svc.KATEGORI_KELUAR, "gerak_baru": uuid.uuid4(),
    })


@perlu("kas")
def detail(request, sesi_id):
    d = svc.detail(sesi_id)
    if d is None:
        raise Http404
    if request.method == "POST":
        try:
            svc.acc(request.user, sesi_id, request.POST.get("aksi") == "terima", request.POST.get("catatan") or "")
            messages.success(request, "Tutup kas diterima." if request.POST.get("aksi") == "terima" else "Tutup kas ditolak.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:kas_detail", sesi_id=sesi_id)
    return render(request, "master/kas_detail.html", {"menu": "kas", **d})
