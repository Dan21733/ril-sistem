from io import BytesIO

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from openpyxl import Workbook
from openpyxl.styles import Font

from pos.models import Device, Item, PaymentProof, Transactions, Worker

from master.layanan.toko import toko_aktif
from master.layanan import laporan
from master.layanan import retur_jual
from master.layanan.inti import Tolak
from master.layanan import inti as layanan
from master.akun.akses import hak_pengguna, perlu
from master.views.utama import _qs_tanpa, kunci_alami

JENIS = [
    ("ringkasan", "Ringkasan"),
    ("barang", "Per barang"),
    ("kategori", "Per kategori"),
    ("brand", "Per brand"),
    ("kasir", "Per karyawan"),
    ("bayar", "Per cara bayar"),
]


def _saring(request):
    periode = laporan.baca_periode(request.GET)
    mulai, selesai = laporan.rentang(periode["awal"], periode["akhir"])
    toko = toko_aktif(request)
    sumber = request.GET.get("sumber") if request.GET.get("sumber") in ("kasir", "manual") else None
    saring = dict(kasir=request.GET.get("kasir") or None, metode=request.GET.get("metode") or None,
                  perangkat=request.GET.get("perangkat") or None, toko=toko, sumber=sumber)
    nota, baris = laporan.ambil(mulai, selesai, **saring)
    # Refund/retur/void dalam periode: mengurangi penjualan dan laba di ringkasan.
    request._kembali = laporan.ambil_kembali(mulai, selesai, **saring)
    return periode, nota, baris


def _konteks_filter():
    return {
        "daftar_kasir": Worker.objects.order_by("name").values_list("id", "name"),
        "daftar_metode": [(k, laporan.label_metode(k)) for k in sorted(
            set(Transactions.objects.values_list("payment_method", flat=True).distinct()) | {"cash", "bank_transfer", "edc"})],
        "daftar_perangkat": Device.objects.order_by("device_code").values_list("device_code", flat=True),
    }


@perlu("laporan")
def nota(request):
    periode, daftar, baris = _saring(request)
    cari = layanan.rapikan_teks(request.GET.get("q")).lower()
    if cari:
        daftar = [n for n in daftar if cari in (n["transaction_id"] or "").lower()
                  or any(cari in b["item_name"].lower() or cari in b["variant"].lower() for b in n["baris"])]
    bukti = set(PaymentProof.objects.filter(sync_id__in=[n["sync_id"] for n in daftar[:400] if not n.get("manual")])
                .values_list("sync_id", flat=True))
    for n in daftar:
        n["ada_bukti"] = n["sync_id"] in bukti
    return render(request, "master/nota.html", {
        "menu": "nota", "periode": periode, "daftar": _tandai_retur(daftar[:400]), "terpotong": len(daftar) > 400,
        "ringkas": laporan.ringkas(daftar, request._kembali), "kembali": request._kembali, "g": request.GET, **_konteks_filter(),
    })


@perlu("laporan")
def nota_panel(request, sync_id):
    tx = get_object_or_404(Transactions, pk=sync_id)
    waktu = tx.transaction_time
    daftar, _ = laporan.ambil(waktu, waktu + timezone.timedelta(microseconds=1))
    n = next((x for x in daftar if x["sync_id"] == tx.sync_id), None)
    if n is None:
        raise Http404
    from pos.models import NotaPersetujuan, NotaTinjau, Worker
    setuju = NotaPersetujuan.objects.filter(sync_id=tx.sync_id).first()
    return render(request, "master/_nota_isi.html", {
        "n": n, "bukti": PaymentProof.objects.filter(sync_id=tx.sync_id).order_by("uploaded_at"),
        "retur": retur_jual.untuk_nota(tx.sync_id), "lokasi_retur": retur_jual.pilihan_lokasi(),
        "penyetuju": Worker.objects.filter(id=setuju.approver_worker_id).values_list("name", flat=True).first()
        if setuju else None,
        "tinjau": NotaTinjau.objects.filter(sync_id=tx.sync_id, status="diterima").first(),
    })


def _tandai_retur(daftar):
    status = retur_jual.status_nota(n["sync_id"] for n in daftar if not n.get("manual"))
    for n in daftar:
        n["retur"] = status.get(n["sync_id"])
    return daftar


@perlu("laporan")
def retur(request):
    """Barang retur/void dari kasir yang menunggu diterima, dan yang sudah diterima."""
    if request.method == "POST":
        try:
            p = retur_jual.terima(request.user, request.POST.get("refund_id"), request.POST, hak=hak_pengguna(request.user))
            messages.success(request, f"Retur diterima: {sum(b.masuk for b in p.baris.all())} barang masuk stok.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        kembali = request.POST.get("kembali") or ""
        if not url_has_allowed_host_and_scheme(kembali, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            kembali = reverse("master:retur")
        return redirect(kembali)
    from master.models import PenerimaanRetur
    sudah = list(PenerimaanRetur.objects.prefetch_related("baris__lokasi")[:30])
    return render(request, "master/retur.html", {
        "menu": "retur", "menunggu": retur_jual.menunggu(), "lokasi_retur": retur_jual.pilihan_lokasi(),
        "sudah": sudah, "tersedia": retur_jual.tersedia(),
    })


@perlu("laporan")
def nota_bukti(request, proof_id):
    """Bukti transfer hanya bisa dibuka lewat master (perlu login), tidak lewat /media/."""
    bukti = get_object_or_404(PaymentProof, pk=proof_id)
    if not default_storage.exists(bukti.object_key):
        raise Http404("Berkas bukti tidak ada di server ini.")
    return FileResponse(default_storage.open(bukti.object_key, "rb"), content_type=bukti.content_type)


@perlu("laporan")
def nota_manual_panel(request, nota_id):
    from master.models import NotaManual
    m = get_object_or_404(NotaManual, pk=nota_id)
    daftar, _ = laporan._ambil_manual(*laporan.rentang(m.tanggal, m.tanggal), toko=m.toko)
    n = next((x for x in daftar if x["manual_id"] == m.pk), None)
    if n is None:
        raise Http404
    return render(request, "master/_nota_isi.html", {"n": n, "foto_nota": m.foto.all()})


@perlu("laporan")
def halaman(request):
    periode, daftar, baris = _saring(request)
    jenis = request.GET.get("lap")
    if jenis not in dict(JENIS):
        jenis = "ringkasan"
    urut = request.GET.get("urut") or "omzet"

    if request.GET.get("kategori"):
        baris = [b for b in baris if b["kategori"] == request.GET["kategori"]]
    if request.GET.get("brand"):
        baris = [b for b in baris if b["brand"] == request.GET["brand"]]

    tabel, per_hari = [], []
    if jenis == "ringkasan":
        per_hari = laporan.kelompok_nota(daftar, "hari")
        tabel = laporan.kelompok_baris(baris, "barang", "omzet")[:10]
    elif jenis == "barang":
        tabel = laporan.kelompok_baris(baris, "varian" if request.GET.get("rinci") == "1" else "barang", urut)
    elif jenis in ("kategori", "brand"):
        tabel = laporan.kelompok_baris(baris, jenis, urut)
    else:
        tabel = laporan.kelompok_nota(daftar, jenis, urut)

    if request.GET.get("export") == "1":
        return _export(jenis, periode, tabel)

    return render(request, "master/laporan.html", {
        "menu": "laporan", "periode": periode, "jenis": jenis, "daftar_jenis": JENIS, "urut": urut,
        "ringkas": laporan.ringkas(daftar, request._kembali), "kembali": request._kembali, "tabel": tabel, "per_hari": per_hari, "g": request.GET,
        "qs_tanpa_lap": _qs_tanpa(request, "lap", "urut", "export", "rinci"),
        "qs_tanpa_urut": _qs_tanpa(request, "urut", "export"),
        "qs_export": _qs_tanpa(request, "export"),
        "saring_barang": jenis in ("barang", "kategori", "brand"),
        "daftar_kategori": sorted(set(Item.objects.values_list("category", flat=True)), key=kunci_alami),
        "daftar_brand": sorted(set(Item.objects.values_list("brand", flat=True)), key=kunci_alami),
        "satuan_qty": "Nota" if jenis in ("kasir", "bayar") else "Terjual",
        **_konteks_filter(),
    })


def _export(jenis, periode, tabel):
    wb = Workbook()
    ws = wb.active
    ws.title = jenis
    ws.append([f"Laporan {dict(JENIS)[jenis]}", f"{periode['awal']:%d-%m-%Y} s.d. {periode['akhir']:%d-%m-%Y}"])
    ws.append(["Nama", "Jumlah", "Nota", "Penjualan", "Laba kotor", "Margin %"])
    for sel in ws[2]:
        sel.font = Font(bold=True)
    for h in tabel:
        ws.append([str(h["nama"]), h["qty"], h["nota"], h["omzet"], h["laba"], h["margin"]])
    ws.column_dimensions["A"].width = 44
    for kolom in "BCDEF":
        ws.column_dimensions[kolom].width = 16
    isi = BytesIO()
    wb.save(isi)
    respons = HttpResponse(isi.getvalue(),
                           content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respons["Content-Disposition"] = f'attachment; filename="laporan_{jenis}_{periode["awal"]:%Y%m%d}_{periode["akhir"]:%Y%m%d}.xlsx"'
    return respons
