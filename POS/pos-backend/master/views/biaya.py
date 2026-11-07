"""Biaya toko di master: catat pengeluaran, ACC uang keluar kasir, kategori (rancangan 7 Nov 2026)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from master.akun.akses import hak_pengguna, perlu
from master.layanan import biaya as svc
from master.layanan import kas as svc_kas
from master.layanan.inti import Tolak
from master.layanan.toko import toko_aktif
from master.models import Biaya

AKSI_HAK = {"catat": "biaya", "kategori": "biaya", "batal": "biaya", "diganti": "biaya", "belum_diganti": "biaya",
            "terima": "acc_biaya", "tolak": "acc_biaya"}


def _boleh(request):
    hak = hak_pengguna(request.user)
    if not hak & {"biaya", "acc_biaya"}:
        raise PermissionDenied("Akun ini tidak punya hak untuk biaya toko.")
    return hak


@perlu()
def halaman(request):
    hak = _boleh(request)
    toko = toko_aktif(request)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        kembali = request.POST.get("kembali") or ""
        try:
            if AKSI_HAK.get(aksi) not in hak:
                raise Tolak("Akun ini tidak boleh melakukan itu.")
            if aksi == "catat":
                b = svc.catat(request.user, toko, request.POST, request.FILES.get("foto"))
                messages.success(request, f"{b.kategori} {svc.rp(b.jumlah)} tercatat.")
            elif aksi in ("terima", "tolak"):
                b = svc.acc(request.user, request.POST.get("id"), aksi == "terima", request.POST.get("alasan"))
                messages.success(request, "Disetujui." if aksi == "terima" else
                                 f"Ditolak. {b.dicatat_oleh} perlu mengganti {svc.rp(b.jumlah)}." if b.sumber == "laci"
                                 else "Ditolak.")
            elif aksi in ("diganti", "belum_diganti"):
                b = svc.tandai_diganti(request.user, request.POST.get("id"), aksi == "diganti")
                messages.success(request, f"Uang {b.dicatat_oleh} ditandai {'sudah' if b.diganti else 'belum'} diganti.")
            elif aksi == "batal":
                svc.batal(request.user, request.POST.get("id"), request.POST.get("alasan"))
                messages.success(request, "Catatan biaya dibatalkan.")
            elif aksi == "kategori":
                k = svc.kategori_simpan(request.user, request.POST)
                messages.success(request, f"Kategori {k.nama} tersimpan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect(kembali if kembali.startswith("/master/biaya/") else "master:biaya")

    svc.tarik_dari_kas()
    bulan = request.GET.get("bulan") or ""
    awal, akhir = svc.rentang_bulan(bulan)
    saring = request.GET.get("status") or ""
    kategori = request.GET.get("kategori") or ""
    daftar = svc.daftar(toko, awal, akhir, saring, kategori)
    # Yang menunggu ACC dan yang belum diganti tampil dari bulan mana pun.
    menunggu = svc.terbuka(toko).filter(status="menunggu")
    perlu_diganti = svc.terbuka(toko).filter(status="ditolak", sumber="laci", diganti=False)
    sebelum = (awal - timezone.timedelta(days=1)).replace(day=1)
    return render(request, "master/biaya.html", {
        "menu": "biaya", "toko": toko, "awal": awal, "akhir": akhir, "bulan": f"{awal:%Y-%m}",
        "bulan_lalu": f"{sebelum:%Y-%m}", "bulan_depan": f"{akhir:%Y-%m}" if akhir <= svc.bulan_ini() else "",
        "saring": saring, "kategori_saring": kategori, "daftar": daftar[:300], "jumlah_daftar": daftar.count(),
        "r": svc.ringkas(toko, awal, akhir), "menunggu": menunggu, "perlu_diganti": perlu_diganti,
        "kategori": svc.kategori_semua(), "kategori_aktif": svc.kategori_aktif(),
        "boleh_laci": svc_kas.punya_kas(toko), "hari_ini": timezone.localdate(),
        "bisa_catat": "biaya" in hak, "bisa_acc": "acc_biaya" in hak, "status_pilihan": Biaya.STATUS,
    })


@perlu()
def foto(request, biaya_id):
    _boleh(request)
    b = get_object_or_404(Biaya, pk=biaya_id)
    if not b.foto_key:
        raise Http404
    try:
        berkas = svc.buka_foto(b)
    except (FileNotFoundError, OSError):
        raise Http404
    return FileResponse(berkas, content_type=b.foto_type or "application/octet-stream")
