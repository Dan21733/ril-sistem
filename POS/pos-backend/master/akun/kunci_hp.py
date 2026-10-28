"""Kunci otomatis aplikasi HP: tidak dipakai beberapa menit -> harus Face ID / sidik jari / kata sandi lagi.

Dicek di server (middleware), jadi tidak bisa diakali dengan mengetik alamat halaman langsung.
- Semua halaman /hp/ (kecuali masuk, kunci, dan berkas PWA) wajib "baru dibuka".
- Sesi yang pernah memakai aplikasi HP (ada `hp_aktif`) juga terkunci di /master/: orang yang
  memegang HP tidak bisa menghindari kunci lewat alamat master. Sesi master di PC yang tidak
  pernah membuka /hp/ tidak terpengaruh.
Lama: MASTER_HP_KUNCI_MENIT (bawaan 5).
"""
import os
import time
from urllib.parse import quote

from django.http import JsonResponse
from django.shortcuts import redirect

KUNCI_DETIK = max(1, int(os.getenv("MASTER_HP_KUNCI_MENIT", "5") or 5)) * 60
BEBAS = ("/hp/kunci/", "/hp/masuk/", "/hp/sw.js", "/hp/manifest.webmanifest", "/hp/ikon-",
         "/master/login/", "/master/logout/")


def tandai_terbuka(request):
    request.session["hp_aktif"] = int(time.time())


def terkunci(request):
    """True kalau sesi ini sedang terkunci (dipakai juga oleh halaman kunci)."""
    aktif = request.session.get("hp_aktif")
    return aktif is None or time.time() - aktif > KUNCI_DETIK


class KunciHp:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.hp_kunci_detik = KUNCI_DETIK
        path = request.path
        hp = path.startswith("/hp/")
        if ((hp or path.startswith("/master/")) and not path.startswith(BEBAS)
                and getattr(request, "user", None) is not None and request.user.is_authenticated
                and (hp or "hp_aktif" in request.session)):
            if terkunci(request):
                if request.method != "GET" or "json" in request.headers.get("Content-Type", "") \
                        or "json" in request.headers.get("Accept", ""):
                    return JsonResponse({"ok": False, "terkunci": True,
                                         "pesan": "Aplikasi terkunci. Buka dengan Face ID / sidik jari."}, status=423)
                return redirect(f"/hp/kunci/?next={quote(request.get_full_path())}")
            if time.time() - request.session["hp_aktif"] > 30:
                tandai_terbuka(request)
        return self.get_response(request)
