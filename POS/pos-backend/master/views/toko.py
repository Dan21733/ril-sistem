from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from master.layanan.toko import toko_aktif
from master.layanan import toko as svc_toko
from master.layanan import lokasi as svc_lokasi
from master.layanan import transfer as svc
from master.akun.akses import perlu
from master.layanan.inti import Tolak
from master.models import Pembayaran, PelunasanSaldo, Toko, Transfer
from master.views.utama import _qs_tanpa


@perlu("kelola_toko")
def toko(request):
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "tambah":
                t = svc_toko.simpan(request.user, request.POST)
                messages.success(request, f"{t.nama} ditambahkan.")
            else:
                t = get_object_or_404(Toko, pk=request.POST.get("id"))
                if aksi == "simpan":
                    svc_toko.simpan(request.user, request.POST, t)
                    messages.success(request, f"{t.nama} tersimpan.")
                elif aksi == "aktif":
                    svc_toko.ubah_aktif(request.user, t)
                    messages.success(request, f"{t.nama} sekarang {'aktif' if t.aktif else 'nonaktif'}.")
                elif aksi == "stok_kasir":
                    svc_toko.jadikan_stok_kasir(request.user, t)
                    messages.success(request, f"Stok di kasir sekarang dihitung sebagai stok {t.nama}.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:toko")
    return render(request, "master/toko.html", {"menu": "toko", "semua": Toko.objects.all()})


@perlu("transfer")
def transfer(request):
    qs = Transfer.objects.select_related("dari", "ke").prefetch_related("baris")
    g = request.GET
    if g.get("status") in dict(Transfer.STATUS):
        qs = qs.filter(status=g["status"])
    aktif_kini = toko_aktif(request)
    qs = qs.filter(dari=aktif_kini) | qs.filter(ke=aktif_kini)
    halaman = Paginator(qs.order_by("-dibuat_waktu", "-id"), 50).get_page(g.get("hal"))
    return render(request, "master/transfer.html", {
        "menu": "transfer", "halaman": halaman, "g": g, "qs_halaman": _qs_tanpa(request, "hal"),
        "status": Transfer.STATUS, "saldo": svc.saldo(), "semua_toko": Toko.objects.all(),
        "perjalanan": Transfer.objects.filter(status="dikirim").count(),
    })


@perlu("transfer")
def transfer_baru(request):
    aktif = svc_toko.aktif()
    if request.method == "POST":
        try:
            tr = svc.buat(request.user, dari_id=request.POST.get("dari"), ke_id=request.POST.get("ke"),
                          tanggal=request.POST.get("tanggal"), catatan=request.POST.get("catatan"))
            return redirect("master:transfer_detail", transfer_id=tr.id)
        except Tolak as tolak:
            messages.error(request, str(tolak))
    return render(request, "master/transfer_baru.html", {
        "menu": "transfer", "aktif": aktif, "hari_ini": timezone.localdate(), "kirim": request.POST,
        "utama": toko_aktif(request),
    })


@perlu("transfer")
def transfer_detail(request, transfer_id):
    tr = get_object_or_404(Transfer.objects.select_related("dari", "ke"), pk=transfer_id)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "tambah_baris":
                svc.tambah_baris(tr, varian_id=request.POST.get("varian_id"), jumlah=request.POST.get("jumlah"))
            elif aksi == "simpan_baris":
                svc.simpan_baris(tr, request.POST)
                messages.success(request, "Tersimpan.")
            elif aksi == "kirim":
                svc.kirim(request.user, tr)
                messages.success(request, "Barang dikirim." + (f" Stok {tr.dari.nama} sudah dikurangi." if tr.dari.stok_di_kasir else ""))
            elif aksi == "terima":
                svc.terima(request.user, tr, request.POST)
                messages.success(request, "Barang diterima. Saldo antar toko diperbarui.")
            elif aksi == "batal":
                svc.batal(request.user, tr, request.POST.get("alasan"))
                messages.info(request, "Transfer dibatalkan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:transfer_detail", transfer_id=tr.id)
    baris = list(tr.baris.select_related("varian__rak"))
    for b in baris:
        b.nilai = (b.jumlah_terima if b.jumlah_terima is not None else b.jumlah_kirim) * b.modal_satuan
    return render(request, "master/transfer_detail.html", {
        "menu": "transfer", "tr": tr, "baris": baris, "nilai_kirim": sum(b.jumlah_kirim * b.modal_satuan for b in baris),
        "pilihan_lokasi": svc_lokasi.pilihan(),
    })


@perlu("transfer")
def transfer_cetak(request, transfer_id):
    tr = get_object_or_404(Transfer.objects.select_related("dari", "ke"), pk=transfer_id)
    if tr.status == "draf":
        messages.error(request, "Surat jalan baru bisa dicetak setelah barang dikirim.")
        return redirect("master:transfer_detail", transfer_id=tr.id)
    return render(request, "master/transfer_cetak.html", {"tr": tr, "baris": tr.baris.select_related("varian__rak")})


@perlu("transfer")
def saldo(request):
    if request.method == "POST":
        try:
            svc.lunasi(request.user, pembayar_id=request.POST.get("pembayar"), penerima_id=request.POST.get("penerima"),
                       tanggal=request.POST.get("tanggal"), jumlah=request.POST.get("jumlah"), cara=request.POST.get("cara"),
                       catatan=request.POST.get("catatan"))
            messages.success(request, "Pelunasan dicatat.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:saldo_toko")
    return render(request, "master/saldo_toko.html", {
        "menu": "transfer", "saldo": svc.saldo(), "riwayat": PelunasanSaldo.objects.select_related("pembayar", "penerima")[:50],
        "cara_bayar": Pembayaran.CARA, "hari_ini": timezone.localdate(),
    })
