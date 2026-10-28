"""Aplikasi pemilik di HP: /hp/ (lihat master/views/hp.py)."""
from django.urls import path

from master.akun.masuk import MasukHp
from master.views import hp as v

app_name = "hp"

urlpatterns = [
    path("", v.beranda, name="beranda"),
    path("masuk/", MasukHp.as_view(), name="masuk"),
    path("masuk/opsi/", v.masuk_opsi, name="masuk_opsi"),
    path("masuk/passkey/", v.masuk_passkey, name="masuk_passkey"),
    path("kunci/", v.kunci, name="kunci"),
    path("kunci/opsi/", v.kunci_opsi, name="kunci_opsi"),
    path("kunci/buka/", v.kunci_buka, name="kunci_buka"),
    path("passkey/opsi/", v.passkey_opsi_daftar, name="passkey_opsi"),
    path("passkey/daftar/", v.passkey_daftar, name="passkey_daftar"),
    path("passkey/hapus/", v.passkey_hapus, name="passkey_hapus"),
    path("detak/", v.detak, name="detak"),
    path("acc/", v.acc, name="acc"),
    path("transaksi/", v.transaksi, name="transaksi"),
    path("barang/", v.barang, name="barang"),
    path("barang/<int:item_id>/", v.barang_detail, name="barang_detail"),
    path("stok/", v.stok, name="stok"),
    path("tugas/", v.tugas, name="tugas"),
    path("tugas/baru/", v.tugas_baru, name="tugas_baru"),
    path("tugas/<int:tiket_id>/", v.tugas_detail, name="tugas_detail"),
    path("input/", v.input_, name="input"),
    path("input/beli/", v.input_beli, name="input_beli"),
    path("setelan/", v.setelan, name="setelan"),
    path("push/", v.push, name="push"),
    path("sw.js", v.service_worker, name="sw"),
    path("manifest.webmanifest", v.manifest, name="manifest"),
    path("ikon-<int:ukuran>.png", v.ikon, name="ikon"),
]
