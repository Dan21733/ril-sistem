from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from pos.models import Item

from master.layanan.toko import saring as saring_toko, toko_aktif
from master.layanan import bukadus
from master.layanan import inti as layanan
from master.layanan import lokasi as svc_lokasi
from master.layanan import pembelian as svc
from master.akun.akses import perlu
from master.layanan.inti import Tolak
from master.models import (
    BukaDus,
    ItemInfo,
    Konversi,
    Pembayaran,
    Pembelian,
    Pengaturan,
    Supplier,
    TarifPPN,
    Toko,
    VarianInfo,
)
from master.views.utama import _qs_tanpa, kunci_alami


@perlu("pembelian")
def daftar(request):
    qs = Pembelian.objects.select_related("supplier", "toko")
    saring = request.GET.get("status", "")
    qs = saring_toko(qs, request)
    if saring == "hutang":
        qs = qs.filter(status="diterima", terbayar__lt=F("total") - F("diretur"))
    elif saring in dict(Pembelian.STATUS):
        qs = qs.filter(status=saring)
    if request.GET.get("supplier"):
        qs = qs.filter(supplier_id=request.GET["supplier"])
    cari = layanan.rapikan_teks(request.GET.get("q"))
    if cari:
        qs = qs.filter(Q(nomor__icontains=cari) | Q(nomor_faktur__icontains=cari)
                       | Q(supplier__nama__icontains=cari) | Q(baris__item_name__icontains=cari)).distinct()
    if saring == "hutang":
        qs = qs.order_by(F("jatuh_tempo").asc(nulls_last=True), "id")
    halaman = Paginator(qs, 50).get_page(request.GET.get("hal"))
    return render(request, "master/pembelian.html", {
        "menu": "pembelian", "halaman": halaman, "g": request.GET,
        "qs_halaman": _qs_tanpa(request, "hal"),
        "hutang": svc.ringkasan_hutang(toko=toko_aktif(request)),
        "menunggu": saring_toko(Pembelian.objects.filter(status="menunggu"), request).count(),
        "daftar_supplier": Supplier.objects.filter(aktif=True),
        "hari_ini": timezone.localdate(),
    })


@perlu("pembelian")
def baru(request):
    if request.method == "POST":
        try:
            po = svc.buat(request.user, supplier_id=request.POST.get("supplier"), toko=request.POST.get("toko"),
                          tanggal_pesan=request.POST.get("tanggal_pesan"), catatan=request.POST.get("catatan"))
            return redirect("master:pembelian_detail", po_id=po.id)
        except Tolak as tolak:
            messages.error(request, str(tolak))
    return render(request, "master/pembelian_baru.html", {
        "menu": "pembelian", "daftar_supplier": Supplier.objects.filter(aktif=True),
        "hari_ini": timezone.localdate(),
    })


def _buka_dus_menunggu():
    daftar = list(BukaDus.objects.filter(status="menunggu").select_related(
        "konversi__dari", "konversi__ke", "lokasi_dari", "lokasi_ke").order_by("waktu"))
    kunci = {(v.item_name, v.variant) for b in daftar for v in (b.konversi.dari, b.konversi.ke)}
    stok = {(r["item_name"], r["variant"]): r["in_stock"] for r in Item.objects.filter(
        item_name__in={n for n, _ in kunci}).values("item_name", "variant", "in_stock")}
    for b in daftar:
        b.stok_dari = stok.get((b.konversi.dari.item_name, b.konversi.dari.variant))
        b.stok_ke = stok.get((b.konversi.ke.item_name, b.konversi.ke.variant))
        b.standar = b.jumlah * b.konversi.isi
    return daftar


@perlu("ubah_stok")
def buka_dus(request):
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "tambah":
                bukadus.buat_konversi(
                    request.user, dari_id=request.POST.get("dari_id"), ke_id=request.POST.get("ke_id"),
                    isi=request.POST.get("isi"), isi_tidak_tetap=request.POST.get("isi_tidak_tetap") == "1",
                    catatan=request.POST.get("catatan"))
                messages.success(request, "Pasangan buka dus ditambahkan.")
            elif aksi in ("acc", "tolak"):
                bd = get_object_or_404(BukaDus.objects.select_related("konversi__dari", "konversi__ke"), pk=request.POST.get("bd"))
                if aksi == "acc":
                    bukadus.acc(request.user, bd)
                    messages.success(request, f"Buka dus dari {bd.oleh} diterapkan: {bd.jumlah} {bd.konversi.dari.item_name} "
                                              f"menjadi {bd.isi_total} {bd.konversi.ke.item_name}.")
                else:
                    bukadus.tolak(request.user, bd, request.POST.get("alasan"))
                    messages.success(request, "Buka dus ditolak. Stok tidak berubah.")
            else:
                k = get_object_or_404(Konversi.objects.select_related("dari", "ke"), pk=request.POST.get("id"))
                if aksi == "buka":
                    hasil = bukadus.buka(request.user, k, jumlah=request.POST.get("jumlah"),
                                         isi_total=request.POST.get("isi_total"), catatan=request.POST.get("catatan"),
                                         lokasi_dari=svc_lokasi.baca(request.POST.get("lokasi_dari")),
                                         lokasi_ke=svc_lokasi.baca(request.POST.get("lokasi_ke")))
                    messages.success(request, f"{hasil.jumlah} {k.dari.item_name} dibuka menjadi {hasil.isi_total} {k.ke.item_name}.")
                elif aksi == "ubah":
                    bukadus.ubah_konversi(request.user, k, isi=request.POST.get("isi"),
                                          isi_tidak_tetap=request.POST.get("isi_tidak_tetap") == "1",
                                          aktif=request.POST.get("aktif") == "1", catatan=request.POST.get("catatan"))
                    messages.success(request, "Pasangan tersimpan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:buka_dus")

    daftar = list(Konversi.objects.select_related("dari__rak", "ke__rak"))
    kunci = {(k.dari.item_name, k.dari.variant) for k in daftar} | {(k.ke.item_name, k.ke.variant) for k in daftar}
    stok = {(b["item_name"], b["variant"]): b for b in Item.objects.filter(
        item_name__in={n for n, _ in kunci}).values("item_name", "variant", "in_stock", "cost_amount")}
    for k in daftar:
        k.stok_dari = (stok.get((k.dari.item_name, k.dari.variant)) or {}).get("in_stock")
        k.stok_ke = (stok.get((k.ke.item_name, k.ke.variant)) or {}).get("in_stock")
    return render(request, "master/buka_dus.html", {
        "menu": "buka_dus", "daftar": daftar, "pilihan_lokasi": svc_lokasi.pilihan(),
        "riwayat": BukaDus.objects.exclude(status="menunggu").select_related("konversi__dari", "konversi__ke")[:30],
        "menunggu": _buka_dus_menunggu(),
    })


@perlu("ubah_stok")
def cari_barang(request):
    """Pencarian barang untuk menambah baris PO. Balasan JSON."""
    cari = layanan.rapikan_teks(request.GET.get("q"))
    if len(cari) < 2:
        return JsonResponse({"hasil": []})
    qs = Item.objects.all()
    for kata in cari.split(" "):
        qs = qs.filter(
            Q(item_name__icontains=kata) | Q(variant__icontains=kata) | Q(brand__icontains=kata)
            | Q(item_name__in=ItemInfo.objects.filter(alias__icontains=kata).values("item_name"))
        )
    barang = list(qs.values("item_name", "variant", "in_stock")[:60])
    barang.sort(key=lambda b: (kunci_alami(b["item_name"]), kunci_alami(b["variant"])))
    barang = barang[:25]
    info = {(v.item_name, v.variant): v for v in VarianInfo.objects.filter(item_name__in={b["item_name"] for b in barang})}
    hasil = []
    for b in barang:
        v = info.get((b["item_name"], b["variant"]))
        if v is None:
            continue
        hasil.append({
            "id": v.id, "nama": b["item_name"], "varian": "" if b["variant"] == "N/A" else b["variant"],
            "stok": b["in_stock"], "harga": svc.harga_beli_terakhir(v),
        })
    return JsonResponse({"hasil": hasil})


@perlu("pembelian")
def detail(request, po_id):
    po = get_object_or_404(Pembelian.objects.select_related("supplier"), pk=po_id)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "tambah_baris":
                svc.tambah_baris(po, varian_id=request.POST.get("varian_id"),
                                 jumlah=request.POST.get("jumlah"), harga=request.POST.get("harga"))
            elif aksi == "simpan_baris":
                svc.simpan_baris(po, request.POST)
                messages.success(request, "Tersimpan.")
            elif aksi == "datang":
                svc.catat_datang(request.user, po, request.POST)
                messages.success(request, "Barang datang dicatat. Menunggu ACC.")
            elif aksi == "faktur":
                svc.lengkapi_faktur(request.user, po, request.POST)
                messages.success(request, "Data faktur tersimpan.")
            elif aksi == "kembalikan":
                svc.kembalikan(request.user, po)
                messages.info(request, "Dikembalikan untuk diperbaiki.")
            elif aksi == "acc":
                hasil_acc = svc.acc(request.user, po, sisa=request.POST.get("sisa"))
                lanjut = getattr(hasil_acc, "lanjutan_baru", None)
                messages.success(request, "Di-ACC. Stok, modal, dan hutang sudah diperbarui."
                                 + (f" Sisa yang belum datang dibuatkan {lanjut.nomor}." if lanjut else ""))
            elif aksi == "batal":
                svc.batal(request.user, po, request.POST.get("alasan"))
                messages.info(request, "PO dibatalkan.")
            elif aksi == "bayar":
                svc.bayar(request.user, po, tanggal=request.POST.get("tanggal"), jumlah=request.POST.get("jumlah"),
                          cara=request.POST.get("cara"), catatan=request.POST.get("catatan"))
                messages.success(request, "Pembayaran dicatat.")
            elif aksi == "retur":
                dok = svc.retur(request.user, po, request.POST)
                messages.success(request, f"Retur dicatat. Stok berkurang dan hutang turun {dok.nilai:,}.".replace(",", "."))
            elif aksi == "uang_kembali":
                svc.uang_kembali(request.user, po, tanggal=request.POST.get("tanggal"), jumlah=request.POST.get("jumlah"),
                                 cara=request.POST.get("cara"), catatan=request.POST.get("catatan"))
                messages.success(request, "Uang kembali dari supplier dicatat.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:pembelian_detail", po_id=po.id)

    hasil = svc.hitung(po)
    daftar_retur = []
    if po.status == "diterima":
        boleh = svc.sisa_retur(po)
        for h in hasil["baris"]:
            h["boleh_retur"] = boleh.get(h["objek"].id, 0)
            h["sudah_retur"] = h["qty"] - h["boleh_retur"]
        daftar_retur = list(po.retur.prefetch_related("baris__baris"))
    tanggal = po.tanggal_faktur or po.tanggal_terima or timezone.localdate()
    return render(request, "master/pembelian_detail.html", {
        "menu": "pembelian", "po": po, "hasil": hasil,
        "pembayaran": po.pembayaran.all() if po.status == "diterima" else [],
        "daftar_retur": daftar_retur, "bisa_retur": any(h.get("boleh_retur") for h in hasil["baris"]),
        "mode_ppn": Pembelian.MODE_PPN, "cara_bayar": Pembayaran.CARA,
        "hari_ini": timezone.localdate(),
        "tarif_sekarang": svc.tarif_pada(tanggal),
        "terlambat": bool(po.status == "diterima" and po.sisa > 0 and po.jatuh_tempo and po.jatuh_tempo < timezone.localdate()),
        "pilihan_lokasi": svc_lokasi.pilihan() if svc.stok_ikut(po) else [],
        "kurang": svc.sisa_belum_datang(po) if po.status == "menunggu" else [],
        "lanjutan": list(po.lanjutan.all()),
        "tanpa_harga": [b for b in po.baris.all() if b.di_luar_pesanan and not b.harga] if po.status == "menunggu" else [],
    })


@perlu("pengaturan")
def pengaturan(request):
    atur = Pengaturan.ambil()
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "tarif":
                mulai = svc.baca_tanggal(request.POST.get("mulai"), wajib=True, label="Tanggal mulai berlaku")
                try:
                    persen = Decimal(str(request.POST.get("persen") or "").replace(",", ".").replace("%", "").strip())
                except InvalidOperation:
                    raise Tolak("Persen harus angka, misalnya 11.") from None
                if not Decimal(0) < persen < Decimal(50):
                    raise Tolak("Persen di luar batas wajar.")
                TarifPPN.objects.update_or_create(mulai=mulai, defaults={
                    "persen": persen, "catatan": layanan.rapikan_teks(request.POST.get("catatan"))[:200]})
                layanan.catat(request.user, objek="pengaturan", kolom="tarif_ppn", baru=f"{persen}% sejak {mulai}")
                messages.success(request, "Tarif tersimpan.")
            elif aksi == "hapus_tarif":
                tarif = get_object_or_404(TarifPPN, pk=request.POST.get("id"))
                layanan.catat(request.user, aksi="hapus", objek="pengaturan", kolom="tarif_ppn",
                              lama=f"{tarif.persen}% sejak {tarif.mulai}")
                tarif.delete()
                messages.success(request, "Tarif dihapus.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:pengaturan")
    return render(request, "master/pengaturan.html", {
        "menu": "pengaturan", "atur": atur, "daftar_tarif": TarifPPN.objects.all(),
        "semua_toko": Toko.objects.all(),
    })
