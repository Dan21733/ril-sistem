"""Halaman masuk (login) master (nama pengguna + kata sandi) dan aplikasi operasional (PIN kasir).

Setelah BATAS kali salah kata sandi untuk satu nama pengguna, nama itu dikunci
selama KUNCI detik. Hitungan disimpan di cache bersama (FileBasedCache milik POS),
jadi berlaku untuk server master dan server gudang sekaligus.
"""
import hashlib

from django import forms
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache

from master.akun.akses import hak_pengguna

BATAS = 5
KUNCI = 15 * 60


def _kunci(username):
    nama = (username or "").strip().lower()
    return "masuk_gagal:" + hashlib.sha256(nama.encode()).hexdigest()[:32]


def gagal(username):
    return cache.get(_kunci(username), 0)


class FormMasuk(AuthenticationForm):
    perlu_hak = None  # diisi subclass: akun wajib punya hak ini
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Nama pengguna atau kata sandi salah.",
        "inactive": "Akun ini nonaktif.",
    }

    def clean(self):
        username = self.cleaned_data.get("username")
        if gagal(username) >= BATAS:
            raise forms.ValidationError(
                f"Terlalu banyak percobaan salah. Coba lagi {KUNCI // 60} menit lagi.", code="dikunci")
        try:
            hasil = super().clean()
        except forms.ValidationError:
            k = _kunci(username)
            cache.set(k, cache.get(k, 0) + 1, KUNCI)
            raise
        cache.delete(_kunci(username))
        return hasil

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if self.perlu_hak and self.perlu_hak not in hak_pengguna(user):
            raise forms.ValidationError("Akun ini tidak punya akses ke aplikasi ini.", code="tanpa_hak")


class MasukMaster(auth_views.LoginView):
    template_name = "master/login.html"
    authentication_form = FormMasuk
    redirect_authenticated_user = True


class MasukHp(MasukMaster):
    """Halaman masuk aplikasi HP (/hp/masuk/). Berada di dalam cakupan PWA /hp/ dan memuat manifest,
    supaya Chrome bisa memasang aplikasinya (halaman masuk master di luar cakupan itu)."""
    template_name = "hp/masuk.html"

    def get_default_redirect_url(self):
        from django.urls import reverse
        return reverse("hp:beranda")



# ---------------------------------------------------------------------------
# Aplikasi operasional: masuk dengan PIN kasir
# ---------------------------------------------------------------------------

SESI_OPS = 10 * 3600  # sesi PIN berakhir 10 jam setelah masuk


@never_cache
@csrf_protect
def masuk_pin(request):
    """Masuk dengan PIN kasir (6 angka). Hanya karyawan yang diberi peran di master.

    Percobaan dibatasi per perangkat (10 salah = tunggu 5 menit). Pesan salahnya
    sama untuk PIN keliru dan PIN tanpa akses, supaya tidak bisa dipakai menebak PIN kasir.
    """
    from pos.pin_throttle import PinThrottled, check_pin_allowed, clear_pin_attempts, register_pin_failure
    from pos.worker_verification import InvalidWorkerCredentials, InvalidWorkerPinFormat, verify_worker_pin

    from master.models import AkunKaryawan, PerangkatOps

    if request.user.is_authenticated and "gudang" in hak_pengguna(request.user):
        return redirect("gudang:beranda")
    perangkat = getattr(request, "perangkat_ops", None)
    galat = ""
    if request.method == "POST":
        pin = (request.POST.get("pin") or "").strip()
        kunci = "ops." + (f"perangkat-{perangkat.pk}" if perangkat else f"ip-{request.META.get('REMOTE_ADDR', '')}")
        try:
            check_pin_allowed(kunci)
            worker_id = verify_worker_pin(pin)
            akun = AkunKaryawan.objects.select_related("user").filter(worker_id=worker_id).first()
            if akun is None or not akun.user.is_active or "gudang" not in hak_pengguna(akun.user):
                raise InvalidWorkerCredentials("tanpa akses")
        except PinThrottled as e:
            galat = str(e)
        except InvalidWorkerPinFormat:
            galat = "PIN harus 6 angka."
        except InvalidWorkerCredentials:
            register_pin_failure(kunci)
            galat = "PIN salah, atau belum diberi akses aplikasi ini."
        else:
            clear_pin_attempts(kunci)
            login(request, akun.user, backend="django.contrib.auth.backends.ModelBackend")
            request.session["ops_worker"] = worker_id
            if perangkat:
                request.session["ops_perangkat"] = perangkat.pk
                PerangkatOps.objects.filter(pk=perangkat.pk).update(terakhir_akun=akun.user.first_name or akun.user.username)
            request.session.set_expiry(SESI_OPS)
            lanjut = request.POST.get("next") or ""
            if lanjut and url_has_allowed_host_and_scheme(lanjut, {request.get_host()}, require_https=request.is_secure()):
                return redirect(lanjut)
            return redirect("gudang:beranda")
    return render(request, "gudang/masuk_pin.html", {
        "galat": galat, "next": request.GET.get("next") or request.POST.get("next") or "",
        "perangkat": perangkat,
    })


def csrf_gagal(request, reason=""):
    """Token formulir basi: arahkan ke halaman yang wajar dengan pesan, bukan halaman 403.

    Permintaan dari JavaScript (fetch/JSON) tetap mendapat 403 berbentuk JSON.
    """
    from django.contrib import messages
    from django.http import JsonResponse
    from django.urls import reverse
    jenis = request.headers.get("Content-Type", "") + " " + request.headers.get("Accept", "")
    if "json" in jenis or request.headers.get("X-CSRFToken") or request.headers.get("X-Requested-With"):
        return JsonResponse({"ok": False, "galat": "Halaman sudah kedaluwarsa. Muat ulang halaman lalu coba lagi."},
                            status=403)
    masuk = reverse("gudang:masuk")
    if request.path.startswith(masuk[:-len("masuk/")]):
        beranda = reverse("gudang:beranda")
    else:   # server master (server gudang tidak memuat URL master)
        masuk, beranda = reverse("master:login"), reverse("master:dashboard")
    if request.path.rstrip("/").endswith("keluar"):
        tujuan = masuk
    elif request.path == masuk and request.user.is_authenticated:
        tujuan = beranda
    else:
        tujuan = request.path
        messages.warning(request, "Halaman tadi sudah kedaluwarsa, jadi isiannya tidak terkirim. Silakan ulangi.")
    return redirect(tujuan)
