"""Halaman superuser: hapus nota kasir dan karyawan uji coba (lihat layanan/hapus_uji.py)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from master.akun.akses import perlu
from master.layanan import hapus_uji as svc
from master.layanan import laporan
from master.layanan.inti import Tolak


@perlu()
def halaman(request):
    if not svc.boleh(request.user):
        raise PermissionDenied("Hanya superuser.")
    if request.method == "POST":
        try:
            if request.POST.get("aksi") == "nota":
                n, k = svc.hapus_nota(request.user, request.POST.getlist("pilih"),
                                      kembalikan_stok=request.POST.get("stok") == "1", alasan=request.POST.get("alasan"))
                messages.success(request, f"{n} nota dihapus." + (f" Stok {k} varian dikembalikan." if k else ""))
            elif request.POST.get("aksi") == "karyawan":
                nama = svc.hapus_karyawan(request.user, request.POST.get("id"), alasan=request.POST.get("alasan"))
                messages.success(request, f"Karyawan {nama} dihapus.")
        except Tolak as e:
            messages.error(request, str(e))
        return redirect(request.get_full_path())
    periode = laporan.baca_periode(request.GET)
    from master.views.laporan import _konteks_filter
    nota = svc.daftar_nota(awal=periode["awal"], akhir=periode["akhir"], kasir=request.GET.get("kasir") or "",
                           perangkat=request.GET.get("perangkat") or "", cari=(request.GET.get("q") or "").strip())
    return render(request, "master/hapus_uji.html", {
        "menu": "hapus_uji", "periode": periode, "nota": nota, "batas": svc.BATAS, "g": request.GET,
        "karyawan": svc.daftar_karyawan(), **_konteks_filter(),
    })
