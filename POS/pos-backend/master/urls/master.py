from django.contrib.auth import views as auth_views
from django.urls import path

from master.akun.masuk import MasukMaster

from master.views import utama as views
from master.views import faktur as views_faktur
from master.views import kasir as views_kasir
from master.views import hapus_uji as views_hapus_uji
from master.views import laporan as views_laporan
from master.views import nota_manual as views_nota_manual
from master.views import opname as views_opname
from master.views import lokasi as views_lokasi
from master.views import pembelian as views_pembelian
from master.views import sampah as views_sampah
from master.views import toko as views_toko
from master.views import titipan as views_titipan
from master.views import piutang as views_piutang

app_name = "master"

urlpatterns = [
    path("login/", MasukMaster.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("tampilan/", views.tema, name="tema"),
    path("kasir/karyawan/", views_kasir.karyawan, name="karyawan"),
    path("kasir/karyawan/slip/", views_kasir.karyawan_slip, name="karyawan_slip"),
    path("kasir/perangkat-ops/", views_kasir.perangkat_ops, name="perangkat_ops"),
    path("kasir/tablet/", views_kasir.tablet, name="tablet"),
    path("kasir/aturan/", views_kasir.aturan_kasir, name="aturan_kasir"),
    path("kasir/nota-acc/", views_kasir.nota_tinjau, name="nota_tinjau"),
    path("toko/pilih/", views.pilih_toko, name="pilih_toko"),
    path("barang/", views.barang_daftar, name="barang"),
    path("barang/ubah/", views.barang_ubah, name="barang_ubah"),
    path("barang/baru/", views.barang_baru, name="barang_baru"),
    path("barang/atur/", views.barang_atur, name="barang_atur"),
    path("barang/nama/", views.barang_nama, name="barang_nama"),
    path("barang/item/<int:item_id>/ringkas/", views.barang_ringkas, name="barang_ringkas"),
    path("barang/item/<int:item_id>/", views.item_detail, name="item"),
    path("barang/item/<int:item_id>/panel/", views.item_detail, {"panel": True}, name="item_panel"),
    path("harga/", views.harga, name="harga"),
    path("stok/", views.stok, name="stok"),
    path("stok/ubah/", views.stok_ubah, name="stok_ubah"),
    path("stok/riwayat/", views.stok_riwayat, name="stok_riwayat"),
    path("stok/lokasi/", views_lokasi.stok, name="stok_lokasi"),
    path("stok/impor/", views_lokasi.stok_impor, name="stok_impor"),
    path("stok/impor/<uuid:impor_id>/", views_lokasi.stok_impor_pratinjau, name="stok_impor_pratinjau"),
    path("stok/lokasi/<int:varian_id>/", views_lokasi.pindah, name="stok_lokasi_pindah"),
    path("lokasi/", views_lokasi.daftar, name="lokasi"),
    path("hitung-acak/", views_lokasi.hitung_acak, name="hitung_acak"),
    path("tiket/", views_lokasi.tiket_daftar, name="tiket"),
    path("tiket/baru/", views_lokasi.tiket_daftar, {"baru": True}, name="tiket_baru"),
    path("ambil/", views_lokasi.ambil, name="ambil"),
    path("laporan-selisih/", views_lokasi.laporan_selisih, name="laporan_selisih"),
    path("tiket/<int:tiket_id>/", views_lokasi.tiket_detail, name="tiket_detail"),
    path("hitung-acak/<int:h_id>/", views_lokasi.hitung_acak_detail, name="hitung_acak_detail"),
    path("faktur/", views_faktur.daftar, name="faktur"),
    path("faktur/baru/", views_faktur.baru, name="faktur_baru"),
    path("faktur/<int:faktur_id>/", views_faktur.detail, name="faktur_detail"),
    path("faktur/foto/<int:foto_id>/", views_faktur.foto, name="faktur_foto"),
    path("hutang/", views_faktur.hutang, name="hutang"),
    path("toko/", views_toko.toko, name="toko"),
    path("transfer/", views_toko.transfer, name="transfer"),
    path("transfer/baru/", views_toko.transfer_baru, name="transfer_baru"),
    path("transfer/saldo/", views_toko.saldo, name="saldo_toko"),
    path("transfer/<int:transfer_id>/", views_toko.transfer_detail, name="transfer_detail"),
    path("transfer/<int:transfer_id>/cetak/", views_toko.transfer_cetak, name="transfer_cetak"),
    path("opname/", views_opname.daftar, name="opname"),
    path("opname/<int:opname_id>/", views_opname.detail, name="opname_detail"),
    path("supplier/", views.supplier, name="supplier"),
    path("pembelian/", views_pembelian.daftar, name="pembelian"),
    path("pembelian/baru/", views_pembelian.baru, name="pembelian_baru"),
    path("pembelian/cari/", views_pembelian.cari_barang, name="pembelian_cari"),
    path("pembelian/foto/<int:foto_id>/", views_pembelian.foto, name="pembelian_foto"),
    path("pembelian/<int:po_id>/", views_pembelian.detail, name="pembelian_detail"),
    path("nota/", views_laporan.nota, name="nota"),
    path("nota/retur/", views_laporan.retur, name="retur"),
    path("nota/<uuid:sync_id>/panel/", views_laporan.nota_panel, name="nota_panel"),
    path("nota/bukti/<uuid:proof_id>/", views_laporan.nota_bukti, name="nota_bukti"),
    path("laporan/", views_laporan.halaman, name="laporan"),
    path("laporan/pergerakan-stok/", views.pergerakan, name="pergerakan"),
    path("titipan/", views_titipan.daftar, name="titipan"),
    path("titipan/<int:toko_id>/", views_titipan.toko, name="titipan_toko"),
    path("titipan/setoran/<int:setoran_id>/", views_titipan.setoran, name="titipan_setoran"),
    path("piutang/", views_piutang.daftar, name="piutang"),
    path("piutang/<int:piutang_id>/", views_piutang.detail, name="piutang_detail"),
    path("piutang/<int:piutang_id>/cetak/", views_piutang.cetak, name="piutang_cetak"),
    path("pelanggan/", views_piutang.pelanggan, name="pelanggan"),
    path("nota-manual/", views_nota_manual.daftar, name="nota_manual"),
    path("nota-manual/baru/", views_nota_manual.baru, name="nota_manual_baru"),
    path("nota-manual/cari/", views_nota_manual.cari, name="nota_manual_cari"),
    path("nota-manual/<int:nota_id>/", views_nota_manual.detail, name="nota_manual_detail"),
    path("nota-manual/<int:nota_id>/panel/", views_laporan.nota_manual_panel, name="nota_manual_panel"),
    path("nota-manual/foto/<int:foto_id>/", views_nota_manual.foto, name="nota_manual_foto"),
    path("buka-dus/", views_pembelian.buka_dus, name="buka_dus"),
    path("pengaturan/", views_pembelian.pengaturan, name="pengaturan"),
    path("kategori/", views.kelompok, {"kolom": "category"}, name="kategori"),
    path("brand/", views.kelompok, {"kolom": "brand"}, name="brand"),
    path("rak/", views.rak, name="rak"),
    path("hapus-uji/", views_hapus_uji.halaman, name="hapus_uji"),
    path("sampah/", views_sampah.daftar, name="sampah"),
    path("impor/", views.impor, name="impor"),
    path("impor/<uuid:impor_id>/", views.impor_pratinjau, name="impor_pratinjau"),
    path("log/", views.log, name="log"),
    path("pengguna/", views.pengguna, name="pengguna"),
]
