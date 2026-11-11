"""Gaji karyawan di master (hanya pemilik, hak `gaji`)."""
from django.contrib import messages
from django.shortcuts import redirect, render

from master.akun.akses import hak_pengguna, perlu
from master.layanan import gaji as svc
from master.layanan.inti import Tolak


@perlu("gaji")
def daftar(request):
    from master.layanan.toko import boleh_untuk, toko_aktif
    from master.models import GajiKaryawan
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        p = request.POST
        try:
            if p.get("aksi") == "simpan":
                g = svc.simpan(request.user, p.get("kunci"), p, hak=hak)
                messages.success(request, f"Gaji {g.nama} disimpan: {svc.ringkas_aturan(g)}.")
            elif p.get("aksi") == "bayar":
                b = svc.bayar(request.user, p.get("gaji"), p.get("tanggal"), hak=hak, sumber=p.get("sumber") or "luar_laci",
                              hari_masuk=p.get("hari_masuk"), toko=toko_aktif(request))
                messages.success(request, f"Gaji {b.gaji.nama} {svc.rp(b.jumlah)} dicatat dibayar (Biaya kategori Gaji).")
        except Tolak as e:
            messages.error(request, str(e))
        return redirect("master:gaji")
    return render(request, "master/gaji.html", {
        "menu": "gaji", "daftar": svc.daftar(), "tempo": svc.jatuh_tempo(), "riwayat": svc.riwayat(),
        "periode": GajiKaryawan.PERIODE, "cara": GajiKaryawan.CARA_TUNJANGAN, "hari": GajiKaryawan.HARI,
        "toko_pilih": [t for t in boleh_untuk(request.user) if not t.titip_di_id],
    })
