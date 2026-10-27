"""Aplikasi pemilik di HP: /hp/ (lihat master/views/hp.py)."""
from django.urls import path

from master.akun.masuk import MasukHp
from master.views import hp as v

app_name = "hp"

urlpatterns = [
    path("", v.beranda, name="beranda"),
    path("masuk/", MasukHp.as_view(), name="masuk"),
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
