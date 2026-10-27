"""Aplikasi pemilik di HP: /hp/ (lihat master/views/hp.py)."""
from django.urls import path

from master.views import hp as v

app_name = "hp"

urlpatterns = [
    path("", v.beranda, name="beranda"),
    path("acc/", v.acc, name="acc"),
    path("barang/", v.barang, name="barang"),
    path("barang/<int:item_id>/", v.barang_detail, name="barang_detail"),
    path("stok/", v.stok, name="stok"),
    path("tugas/", v.tugas, name="tugas"),
    path("tugas/baru/", v.tugas_baru, name="tugas_baru"),
    path("tugas/<int:tiket_id>/", v.tugas_detail, name="tugas_detail"),
    path("input/", v.input_, name="input"),
    path("input/nota/", v.input_nota, name="input_nota"),
    path("input/beli/", v.input_beli, name="input_beli"),
    path("manifest.webmanifest", v.manifest, name="manifest"),
    path("ikon-<int:ukuran>.png", v.ikon, name="ikon"),
]
