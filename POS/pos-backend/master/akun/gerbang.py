"""Gerbang aplikasi operasional (/gudang/): hanya perangkat yang disetujui pemilik.

Dipasang di MIDDLEWARE master (server gudang ikut karena memakai pengaturan yang
sama). Hanya memeriksa alamat /gudang/; master (/master/) tidak tersentuh.

Selain perangkat, sesi yang masuk dengan PIN karyawan diikat ke perangkatnya dan
ikut keluar kalau karyawannya dinonaktifkan atau perangkatnya dicabut.
"""
from django.contrib.auth import logout
from django.shortcuts import render

from master.layanan import perangkat_ops

AWALAN = "/gudang/"


def karyawan_aktif(worker_id):
    from django.db.models import Q
    from django.utils import timezone

    from pos.models import Worker, WorkerPin
    sekarang = timezone.now()
    riwayat = WorkerPin.objects.filter(worker_id=worker_id)
    if not riwayat.exists():
        return Worker.objects.filter(id=worker_id).exists()
    return riwayat.filter(Q(berlaku_sampai__isnull=True) | Q(berlaku_sampai__gt=sekarang)).exists()


class GerbangPerangkat:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(AWALAN) or not perangkat_ops.wajib():
            return self.get_response(request)
        p, kunci_baru = perangkat_ops.kenali(request)
        if p is None or p.status != "disetujui":
            if request.user.is_authenticated:
                logout(request)
            response = render(request, "gudang/perangkat.html", {"p": p}, status=403)
        else:
            request.perangkat_ops = p
            if request.user.is_authenticated:
                terikat = request.session.get("ops_perangkat")
                wid = request.session.get("ops_worker")
                if (terikat is not None and terikat != p.pk) or (wid and not karyawan_aktif(wid)):
                    logout(request)
                elif terikat is None:
                    request.session["ops_perangkat"] = p.pk
            response = self.get_response(request)
        if kunci_baru:
            perangkat_ops.pasang_cookie(response, kunci_baru)
        response["Cache-Control"] = "no-store"
        return response
