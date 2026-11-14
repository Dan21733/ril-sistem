"""Editor denah (14 Nov 2026): susun ruangan di lokasi, lalu rak/area/pintu di dalam ruangan."""
import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master.akun.akses import perlu
from master.layanan import denah as svc
from master.layanan.inti import Tolak
from master.models import Lokasi, Rak, Ruang


@perlu("kelola_rak")
def denah(request, lokasi_id):
    lokasi = get_object_or_404(Lokasi, pk=lokasi_id)
    ruang_pilih = None
    if request.GET.get("ruang"):
        ruang_pilih = get_object_or_404(Ruang, pk=request.GET["ruang"], lokasi=lokasi)
    if request.method == "POST":
        try:
            isi = json.loads(request.body or b"{}")
            if ruang_pilih is not None:
                svc.simpan_ruang(request.user, ruang_pilih, isi.get("blok"))
            else:
                svc.simpan_lokasi(request.user, lokasi, isi.get("ruang"))
        except (ValueError, AttributeError):
            return JsonResponse({"pesan": "Isi denah tidak terbaca."}, status=400)
        except Tolak as tolak:
            return JsonResponse({"pesan": str(tolak)}, status=409)
        return JsonResponse({"ok": True})
    daftar_ruang = list(Ruang.objects.filter(lokasi=lokasi))
    if not daftar_ruang:
        messages.info(request, "Buat ruangannya dulu di halaman Rak, baru susun denahnya.")
        return redirect(f"{reverse('master:rak')}?lokasi={lokasi.pk}")
    if ruang_pilih is not None:
        data = {"mode": "ruang", "kolom": svc.RUANG_KOLOM, "baris": svc.RUANG_BARIS,
                "barang": svc.isi_ruang(ruang_pilih), "letak": (ruang_pilih.denah or {}).get("blok", [])}
    else:
        data = {"mode": "lokasi", "kolom": svc.LOKASI_KOLOM, "baris": svc.LOKASI_BARIS,
                "barang": [{"t": "ruang", "id": r.pk, "nama": r.nama} for r in daftar_ruang],
                "letak": [{"t": "ruang", "id": r.pk, "x": r.x, "y": r.y, "w": r.w, "h": r.h}
                          for r in daftar_ruang if r.w]}
    return render(request, "master/denah.html", {
        "menu": "rak", "lokasi": lokasi, "daftar_ruang": daftar_ruang, "ruang_pilih": ruang_pilih, "data": data,
    })
