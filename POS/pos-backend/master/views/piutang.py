"""Piutang & pesanan pelanggan: DP, pelunasan, barang ambil nanti, ACC invoice/titip, pelanggan tempo."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from master.akun.akses import hak_pengguna, perlu
from master.layanan import piutang as svc
from master.layanan.inti import Tolak
from master.models import DaftarHitam, Pelanggan, Piutang


@perlu("piutang")
def daftar(request):
    svc.sinkron_kasir()
    tab = request.GET.get("tab") or "pesanan"
    if tab not in dict(svc.TAB):
        tab = "pesanan"
    qs = svc.daftar(tab)
    cari = (request.GET.get("q") or "").strip()
    if cari:
        from django.db.models import Q
        qs = qs.filter(Q(nama__icontains=cari) | Q(telepon__icontains=cari) | Q(nomor__icontains=cari))
    baris = list(qs[:300])
    hari_ini = timezone.localdate()
    for p in baris:
        p.umur = (hari_ini - p.tanggal).days
        p.lewat = bool(p.jatuh_tempo and p.jatuh_tempo < hari_ini and not p.lunas)
    return render(request, "master/piutang.html", {
        "menu": "piutang", "tab": tab, "tabs": svc.TAB, "hitung": svc.hitungan(), "baris": baris, "cari": cari,
        "ringkas": svc.ringkas(),
    })


@perlu("piutang")
def detail(request, piutang_id):
    svc.sinkron_kasir()
    p = get_object_or_404(Piutang.objects.select_related("toko", "pelanggan", "nota_manual"), pk=piutang_id)
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "bayar":
                svc.bayar(request.user, p, jumlah=request.POST.get("jumlah"), cara=request.POST.get("cara") or "cash",
                          catatan=request.POST.get("catatan", ""), sumber="master")
                p.refresh_from_db()
                messages.success(request, "Pembayaran tercatat." + (" Nota sudah lunas." if p.lunas else f" Sisa {svc.rp(p.sisa)}."))
            elif aksi == "serahkan":
                svc.serahkan(request.user, p)
                messages.success(request, "Barang ditandai sudah diambil.")
            elif aksi in ("setuju", "tolak"):
                if "acc_piutang" not in hak:
                    raise Tolak("Akun ini tidak boleh memberi ACC.")
                svc.acc(request.user, p, setuju=aksi == "setuju", catatan=request.POST.get("catatan", ""))
                messages.success(request, "Disetujui." if aksi == "setuju" else "Ditolak.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:piutang_detail", piutang_id=p.pk)
    return render(request, "master/piutang_detail.html", {
        "menu": "piutang", "p": p, "pembayaran": p.pembayaran.all(), "cara": [(k, v) for k, v in svc.CARA_BAYAR.items() if k != "campur"],
        "boleh_acc": "acc_piutang" in hak, "wa": svc.wa_url(p) if p.telepon else "",
        "barang": svc.barang(p),
        "lewat": bool(p.jatuh_tempo and p.jatuh_tempo < timezone.localdate() and not p.lunas),
    })


@perlu("piutang")
def cetak(request, piutang_id):
    """Tagihan selebar kertas struk (dicetak dari browser ke printer struk)."""
    p = get_object_or_404(Piutang.objects.select_related("toko", "nota_manual"), pk=piutang_id)
    return render(request, "master/piutang_cetak.html", {
        "p": p, "pembayaran": p.pembayaran.all(), "barang": svc.barang(p),
        "sekarang": timezone.localtime()})


@perlu("piutang")
def pelanggan(request):
    if request.method == "POST":
        try:
            if "acc_piutang" not in hak_pengguna(request.user):
                raise Tolak("Hanya pemilik yang boleh mengatur pelanggan tempo.")
            if request.POST.get("aksi") == "buka_hitam":
                d = get_object_or_404(DaftarHitam, pk=request.POST.get("id"))
                svc.buka_daftar_hitam(request.user, d)
                messages.success(request, f"{d.telepon} dibuka; boleh COD lagi.")
                return redirect("master:pelanggan")
            obj = Pelanggan.objects.filter(pk=request.POST.get("id")).first() if request.POST.get("id") else None
            p = svc.simpan_pelanggan(request.user, request.POST, obj)
            messages.success(request, f"{p.nama} tersimpan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:pelanggan")
    semua = list(Pelanggan.objects.all())
    for p in semua:
        p.berjalan = svc.sisa_berjalan(p)
    return render(request, "master/pelanggan.html", {"menu": "pelanggan", "semua": semua,
                                                      "hitam": DaftarHitam.objects.all()[:300],
                                                      "boleh_atur": "acc_piutang" in hak_pengguna(request.user)})
