"""Hak karyawan kasir (PIN) di kasir dan di aplikasi operasional, per hak.

Pemilik mencentang hak satu-satu per karyawan (menu Kasir > Karyawan & PIN):

- Kasir: "Jualan di kasir" (pos.pengaturan `bukan_kasir`, daftar larangan supaya
  karyawan lama tetap boleh) dan "Setujui harga di bawah floor" (`penyetuju_floor`).
- Aplikasi operasional: hak di `HAK_KARYAWAN_OPS`. Sistem membuat akun ops-<id>
  tanpa kata sandi; hak akunnya = yang dicentang + "gudang". Tanpa hak satu pun =
  akun dinonaktifkan (langsung keluar). `AkunKaryawan.peran` menyimpan daftar hak itu.
"""
from django.contrib.auth import get_user_model
from django.db import transaction

from pos import pengaturan as aturan
from pos.models import Worker

from master.akun.akses import LABEL_HAK_OPS
from master.layanan import inti as layanan
from master.layanan.inti import Tolak
from master.models import AkunKaryawan, HakAkun

HAK_KASIR = [("pos_jual", "Jualan di kasir"), ("penyetuju", "Setujui harga di bawah floor")]


def hak_ops_semua():
    """{worker_id: [hak operasional]} untuk karyawan yang akunnya aktif."""
    return {a.worker_id: [h for h in a.peran if h in LABEL_HAK_OPS]
            for a in AkunKaryawan.objects.select_related("user") if a.user.is_active and a.peran}


def atur_hak_ops(pengguna, worker_id, hak):
    w = Worker.objects.filter(id=worker_id or "").first()
    if w is None:
        raise Tolak("Karyawan tidak ditemukan.")
    hak = [h for h in LABEL_HAK_OPS if h in set(hak or [])]
    from master.akun.akses import HAK_BENTROK
    for pasangan, pesan in HAK_BENTROK:
        if pasangan <= set(hak):
            raise Tolak(pesan)
    User = get_user_model()
    with transaction.atomic():
        akun = AkunKaryawan.objects.select_related("user").filter(worker_id=w.id).first()
        lama = [h for h in akun.peran if h in LABEL_HAK_OPS] if akun and akun.user.is_active else []
        if akun is None:
            if not hak:
                return []
            nama = f"ops-{w.id.lower()}"
            user = User.objects.filter(username=nama).first()
            if user is None:
                user = User(username=nama)
                user.set_unusable_password()
            akun = AkunKaryawan(worker_id=w.id, user=user)
        user = akun.user
        user.first_name = (w.name or "")[:150]
        user.is_active = bool(hak)
        user.save()
        akun.user = user
        akun.peran = hak
        akun.save()
        HakAkun.objects.update_or_create(user=user, defaults={"semua": False, "hak": sorted(set(hak) | {"gudang"}) if hak else [],
                                                              "toko": []})
        if lama != hak:
            label = lambda hs: ", ".join(LABEL_HAK_OPS[h] for h in hs) or "tidak ada"
            layanan.catat(pengguna, objek="akun", kolom="hak operasional", lama=f"{w.name}: {label(lama)}",
                          baru=f"{w.name}: {label(hak)}")
    return hak


def atur_jual(pengguna, worker_id, boleh):
    """Boleh/tidaknya PIN karyawan dipakai jualan (dan void/retur) di tablet kasir."""
    w = Worker.objects.filter(id=worker_id or "").first()
    if w is None:
        raise Tolak("Karyawan tidak ditemukan.")
    ids = aturan.bukan_kasir()
    sebelum = w.id not in ids
    if sebelum == bool(boleh):
        return
    (ids.discard if boleh else ids.add)(w.id)
    aturan.simpan(bukan_kasir=",".join(sorted(ids)))
    layanan.catat(pengguna, objek="karyawan", kolom="jualan di kasir", lama=f"{w.name}: {'ya' if sebelum else 'tidak'}",
                  baru=f"{w.name}: {'ya' if boleh else 'tidak'}")


def atur_semua(pengguna, worker_id, hak):
    """Simpan seluruh panel Hak akses satu karyawan (kasir + operasional)."""
    from master.layanan import karyawan as svc_karyawan
    hak = set(hak or [])
    with transaction.atomic():
        hasil = atur_hak_ops(pengguna, worker_id, hak)   # dicek dulu (hak bentrok), baru yang lain
        atur_jual(pengguna, worker_id, "pos_jual" in hak)
        if ("penyetuju" in hak) != (worker_id in aturan.penyetuju_floor()):
            svc_karyawan.atur_penyetuju(pengguna, worker_id, "penyetuju" in hak)
    aturan.lupakan_cache()
    return hasil


def worker_dari_user(user):
    akun = AkunKaryawan.objects.filter(user=user).first()
    return akun.worker_id if akun else None
