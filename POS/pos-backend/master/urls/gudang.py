from django.contrib.auth import views as auth_views
from django.urls import path

from master.akun.masuk import MasukGudang, masuk_pin

from master.views import gudang as v

app_name = "gudang"

urlpatterns = [
    path("masuk/", masuk_pin, name="masuk"),
    path("masuk/sandi/", MasukGudang.as_view(), name="masuk_sandi"),
    path("keluar/", auth_views.LogoutView.as_view(next_page="gudang:masuk"), name="keluar"),
    path("", v.beranda, name="beranda"),
    path("panduan/", v.panduan, name="panduan"),
    path("datang/", v.datang, name="datang"),
    path("datang/<int:po_id>/", v.datang_detail, name="datang_detail"),
    path("hitung/", v.hitung, name="hitung"),
    path("buka-dus/", v.buka_dus, name="buka_dus"),
    path("stok/", v.stok_lokasi, name="stok"),
    path("ambil/", v.ambil, name="ambil"),
    path("tiket/", v.tiket, name="tiket"),
    path("tiket/<int:tiket_id>/", v.tiket_isi, name="tiket_isi"),
    path("tiket/<int:tiket_id>/barang/", v.tiket_barang, name="tiket_barang"),
]
