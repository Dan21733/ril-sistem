"""Aplikasi pemilik di HP (/hp/): ringkas, besar, untuk iPhone dan Android (PWA, bukan kiosk).

Semua aturan dan hak sama dengan master: halaman ini hanya tampilan lain di atas layanan yang
sama (master/layanan/*). Tidak ada aturan bisnis baru di sini.
"""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from pos.models import Item, Worker

from master.akun.akses import hak_pengguna, perlu
from master.layanan import inti as layanan
from master.layanan.inti import Tolak, kunci_alami
from master.models import BukaDus, ItemInfo, Pembelian, Supplier, Tiket, VarianInfo


def perlu_hp(hak=None):
    """Sama dengan `perlu`, tetapi yang belum masuk diarahkan ke halaman masuk HP (dalam cakupan PWA)."""
    from functools import wraps
    from urllib.parse import quote

    def bungkus(view):
        dijaga = perlu(hak)(view)

        @wraps(view)
        def dalam(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.urls import reverse
                if request.method != "GET" or request.headers.get("Content-Type", "").startswith("application/json"):
                    return JsonResponse({"ok": False, "pesan": "Sesi habis. Muat ulang halaman lalu masuk lagi."}, status=401)
                return redirect(f"{reverse('hp:masuk')}?next={quote(request.get_full_path())}")
            return dijaga(request, *args, **kwargs)
        return dalam
    return bungkus


PERIODE = [("hari", "Hari ini"), ("kemarin", "Kemarin"), ("7hari", "7 hari"), ("bulan", "Bulan ini")]


def _periode(request, *, boleh_tanggal=True):
    """(kode, awal, akhir, judul) dari ?p= atau ?dari=&sampai= (pemilih tanggal). Bawaan: hari ini."""
    from datetime import date
    hari_ini = timezone.localdate()
    kode = request.GET.get("p") or "hari"
    dari, sampai = request.GET.get("dari") or request.GET.get("tanggal"), request.GET.get("sampai") or request.GET.get("tanggal")
    if boleh_tanggal and dari:
        try:
            a, b = min(date.fromisoformat(dari), hari_ini), min(date.fromisoformat(sampai or dari), hari_ini)
        except ValueError:
            a = b = None
        if a:
            if b < a:
                a, b = b, a
            a = max(a, b - timedelta(days=366))
            if a == b == hari_ini:
                return "hari", a, b, "Hari ini"
            if a == b == hari_ini - timedelta(days=1):
                return "kemarin", a, b, "Kemarin"
            if b == hari_ini and a == hari_ini - timedelta(days=6):
                return "7hari", a, b, "7 hari terakhir"
            if b == hari_ini and a == hari_ini.replace(day=1):
                return "bulan", a, b, "Bulan ini"
            if a == b:
                return "tanggal", a, b, f"{a.day} {_BULAN[a.month - 1]} {a.year}"
            awal_teks = f"{a.day} {_BULAN[a.month - 1]}" + ("" if a.year == b.year else f" {a.year}")
            return "tanggal", a, b, f"{awal_teks} – {b.day} {_BULAN[b.month - 1]} {b.year}"
    if kode == "kemarin":
        k = hari_ini - timedelta(days=1)
        return kode, k, k, "Kemarin"
    if kode == "7hari":
        return kode, hari_ini - timedelta(days=6), hari_ini, "7 hari terakhir"
    if kode == "bulan":
        return kode, hari_ini.replace(day=1), hari_ini, "Bulan ini"
    return "hari", hari_ini, hari_ini, "Hari ini"


_BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _judul_pendek(kode, awal, akhir):
    """Label pendek untuk pil tanggal (muat di layar HP 360px)."""
    if kode != "tanggal":
        return {"hari": "Hari ini", "kemarin": "Kemarin", "7hari": "7 hari", "bulan": "Bulan ini"}.get(kode, "Hari ini")
    if awal == akhir:
        return f"{awal.day} {_BULAN[awal.month - 1]}"
    if awal.month == akhir.month and awal.year == akhir.year:
        return f"{awal.day}–{akhir.day} {_BULAN[akhir.month - 1]}"
    return f"{awal.day} {_BULAN[awal.month - 1]}–{akhir.day} {_BULAN[akhir.month - 1]}"


def _pembanding(kode):
    return {"hari": "kemarin", "kemarin": "hari sebelumnya", "7hari": "7 hari sebelumnya",
            "bulan": "periode sama sebelumnya", "tanggal": "periode sebelumnya"}.get(kode, "sebelumnya")


def _json_badan(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _kembali(request, bawaan):
    """Alamat kembali hanya boleh di dalam aplikasi HP (bukan alamat luar atau javascript:)."""
    from django.urls import reverse
    k = request.GET.get("kembali") or ""
    return k if k.startswith("/hp/") and "//" not in k else reverse(bawaan)


def _acc_semua(request):
    """Semua yang menunggu ACC akun ini (dihitung sekali per permintaan)."""
    if not hasattr(request, "_acc_hp"):
        from master.layanan import acc_hp
        request._acc_hp = acc_hp.daftar(request.user)
    return request._acc_hp


def _jumlah_acc(request):
    """Angka di tab Beranda: semua yang menunggu keputusan pemilik (semua toko)."""
    return len(_acc_semua(request))


def _toko_hp(request):
    """Toko satuan yang sedang dibuka di HP dan apakah beranda sedang "Semua toko"."""
    from master.layanan.toko import toko_aktif
    return toko_aktif(request), bool(request.session.get("hp_semua"))


def _daftar_toko_hp(request):
    """Isi lembar Pindah toko: toko yang boleh dibuka akun ini + keadaan singkatnya."""
    from master.layanan import acc_hp
    from master.layanan import kas as svc_kas
    from master.layanan.toko import boleh_untuk
    hak = hak_pengguna(request.user)
    hitung = acc_hp.per_toko(_acc_semua(request))
    hasil = []
    for t in boleh_untuk(request.user):
        if t.titip_di_id:
            continue
        bagian = []
        if "kas" in hak and svc_kas.punya_kas(t):
            h = svc_kas.angka_hari_ini(t)
            bagian.append("Kas dibuka" if h["buka"] else "Kas sudah ditutup" if h["terakhir_hari_ini"] else "Kas belum dibuka")
        if hitung.get(t.pk):
            bagian.append(f"{hitung[t.pk]} perlu ACC")
        hasil.append({"t": t, "status": " · ".join(bagian), "acc": hitung.get(t.pk, 0)})
    return hasil


def mode_hp(request):
    """'simpel' atau 'lanjutan' untuk akun ini (dihitung sekali per permintaan). Hanya tampilan."""
    if not hasattr(request, "_mode_hp"):
        from master.models import SetelanHp
        request._mode_hp = SetelanHp.objects.filter(user=request.user).values_list("mode", flat=True).first() or "lanjutan"
    return request._mode_hp


def _tampil(request, nama, isi):
    isi.setdefault("mode_hp", mode_hp(request))
    toko, semua = _toko_hp(request)
    isi.setdefault("jumlah_acc", _jumlah_acc(request))
    isi.setdefault("toko_hp", toko)
    isi.setdefault("hp_semua", semua)
    isi.setdefault("daftar_toko_hp", _daftar_toko_hp(request))
    isi["acc_toko_lain"] = sum(d["acc"] for d in isi["daftar_toko_hp"] if toko is None or d["t"].pk != toko.pk)
    return render(request, nama, isi)


def _jawab(ok=True, pesan="", status=200, **lain):
    return JsonResponse({"ok": ok, "pesan": pesan, **lain}, status=status)


# ---------------------------------------------------------------------------
# Beranda
# ---------------------------------------------------------------------------


def _uang_toko(toko, hak, awal, akhir, kode):
    """Penjualan satu toko + baris uang di bawahnya (tunai/laci, transfer, EDC)."""
    from master.layanan import dasbor
    from master.layanan import kas as svc_kas
    jual = dasbor.periode(toko, hak, awal, akhir) if "laporan" in hak else None
    kas = svc_kas.angka_hari_ini(toko) if "kas" in hak and kode == "hari" else None
    return jual, kas


@perlu_hp("laporan")
def transfer(request):
    """Nota bayar transfer + bukti fotonya; pemilik menandai bukti yang sudah dicek di mutasi rekening."""
    from master.layanan import bukti as svc
    from master.layanan import laporan
    from master.layanan.toko import toko_aktif
    if request.method == "POST":
        d = _json_badan(request)
        try:
            svc.tandai(request.user, d.get("proof_id"), dicek=d.get("aksi") != "batal")
        except (Tolak, ValueError, ValidationError) as e:
            return _jawab(False, str(e) if isinstance(e, Tolak) else "Bukti tidak dikenali.", 400)
        return _jawab(pesan="Ditandai sudah dicek." if d.get("aksi") != "batal" else "Tanda cek dibatalkan.")
    kode, awal, akhir, judul = _periode(request)
    nota = svc.nota_transfer(*laporan.rentang(awal, akhir), toko_aktif(request))
    return _tampil(request, "hp/transfer.html", {
        "tab": "beranda", "periode": PERIODE, "p": kode, "judul_periode": judul, "awal": awal, "akhir": akhir,
        "judul_pendek": _judul_pendek(kode, awal, akhir), "hari_ini": timezone.localdate(),
        "nota": nota, "ringkas": svc.ringkas(nota),
    })


@perlu_hp()
def beranda(request):
    from master.layanan import acc_hp
    hak = hak_pengguna(request.user)
    toko, semua = _toko_hp(request)
    kode, awal, akhir, judul = _periode(request)
    jam = timezone.localtime().hour
    sapa = "Selamat pagi" if jam < 11 else "Selamat siang" if jam < 15 else "Selamat sore" if jam < 18 else "Selamat malam"
    items = _acc_semua(request)
    isi = {
        "tab": "beranda", "periode": PERIODE, "p": kode, "judul_periode": judul, "awal": awal, "akhir": akhir,
        "judul_pendek": _judul_pendek(kode, awal, akhir),
        "hari_ini": timezone.localdate(), "pembanding": _pembanding(kode), "sapa": sapa,
        "pekerja": list(Worker.objects.order_by("name").values_list("id", "name")),
    }
    if semua:
        isi["acc"] = items
        isi["per_toko"] = []
        for d in _daftar_toko_hp(request):
            jual, kas = _uang_toko(d["t"], hak, awal, akhir, kode)
            isi["per_toko"].append({**d, "jual": jual, "kas": kas})
        return _tampil(request, "hp/beranda_semua.html", isi)
    jual, kas = _uang_toko(toko, hak, awal, akhir, kode)
    piutang_ringkas = None
    if "piutang" in hak:
        from master.layanan import piutang as svc_piutang
        piutang_ringkas = svc_piutang.ringkas()
    bukti_ringkas = None
    if jual and jual["per_metode"]["jumlah_transfer"]:
        from master.layanan import bukti as svc_bukti
        from master.layanan import laporan
        bukti_ringkas = svc_bukti.ringkas(svc_bukti.nota_transfer(*laporan.rentang(awal, akhir), toko))
    po_belum = 0
    if "pembelian" in hak:
        from master.models import Pembelian
        po_belum = Pembelian.objects.filter(status="dibuat", toko=toko, penerima=None, baris__isnull=False).distinct().count()
    opname_ulang = 0
    if "opname" in hak:
        from master.layanan import sesi_opname as svc_sesi
        sesi = svc_sesi.sesi_buka()
        opname_ulang = svc_sesi.jumlah_menunggu(sesi) if sesi else 0
    gaji_tempo = []
    if "gaji" in hak:
        from master.layanan import gaji as svc_gaji
        gaji_tempo = svc_gaji.jatuh_tempo()
    isi.update({"jual": jual, "kas_hari": kas, "acc": acc_hp.saring(items, toko), "piutang_ringkas": piutang_ringkas,
                "bukti_ringkas": bukti_ringkas, "po_belum": po_belum, "opname_ulang": opname_ulang,
                "gaji_tempo": gaji_tempo})
    return _tampil(request, "hp/beranda.html", isi)


@require_POST
@perlu_hp()
def pilih_toko(request):
    """Pindah toko di HP. "semua" hanya untuk Beranda; menu lain tetap memakai toko satuan terakhir."""
    from master.layanan.toko import pilih_toko as pilih
    nilai = request.POST.get("toko") or ""
    if nilai == "semua":
        request.session["hp_semua"] = True
        return redirect("hp:beranda")
    try:
        pilih(request, nilai)
    except Tolak:
        return redirect("hp:beranda")
    request.session["hp_semua"] = False
    k = request.POST.get("next") or ""
    return redirect(k if k.startswith("/hp/") and "//" not in k else "hp:beranda")


@require_POST
@perlu_hp()
def acc(request):
    """ACC/tolak dari beranda (JSON): nota di bawah floor dan buka dus."""
    hak = hak_pengguna(request.user)
    d = _json_badan(request)
    jenis, aksi = d.get("jenis"), d.get("aksi")
    try:
        if jenis == "nota":
            if "kelola_kasir" not in hak:
                raise Tolak("Akun ini tidak boleh memutuskan nota.")
            from master.layanan import nota_tinjau
            if aksi == "terima":
                n = nota_tinjau.terima(request.user, d.get("id"), d.get("worker_id"), d.get("catatan") or "")
                return _jawab(pesan=f"Nota {n.transaction_id or ''} diterima.")
            n = nota_tinjau.tolak(request.user, d.get("id"), d.get("alasan"))
            return _jawab(pesan=f"Nota {n.transaction_id or ''} ditolak.")
        if jenis == "piutang":
            if "acc_piutang" not in hak:
                raise Tolak("Akun ini tidak boleh memberi ACC.")
            from master.layanan import piutang as svc_piutang
            from master.models import Piutang
            p = get_object_or_404(Piutang, pk=d.get("id"))
            svc_piutang.acc(request.user, p, setuju=aksi == "terima", catatan=d.get("alasan") or "")
            return _jawab(pesan=f"{p.nomor} {'disetujui' if aksi == 'terima' else 'ditolak'}.")
        if jenis == "kas":
            if "kas" not in hak:
                raise Tolak("Akun ini tidak boleh memberi ACC kas.")
            from master.layanan import kas as svc_kas
            svc_kas.acc(request.user, d.get("id"), aksi == "terima", d.get("alasan") or "")
            return _jawab(pesan="Tutup kas diterima." if aksi == "terima" else "Tutup kas ditolak.")
        if jenis == "biaya":
            if "acc_biaya" not in hak:
                raise Tolak("Akun ini tidak boleh memberi ACC biaya.")
            from master.layanan import biaya as svc_biaya
            b = svc_biaya.acc(request.user, d.get("id"), aksi == "terima", d.get("alasan") or "")
            return _jawab(pesan="Uang keluar disetujui." if aksi == "terima" else
                          f"Ditolak. {b.dicatat_oleh} perlu mengganti {svc_biaya.rp(b.jumlah)}." if b.sumber == "laci" else "Ditolak.")
        if jenis == "dus":
            if "ubah_stok" not in hak:
                raise Tolak("Akun ini tidak boleh mengubah stok.")
            from master.layanan import bukadus
            bd = get_object_or_404(BukaDus, pk=d.get("id"))
            if aksi == "terima":
                bukadus.acc(request.user, bd)
                return _jawab(pesan="Buka dus diterapkan.")
            bukadus.tolak(request.user, bd, d.get("alasan") or "")
            return _jawab(pesan="Buka dus ditolak.")
    except Tolak as e:
        return _jawab(False, str(e), 400)
    return _jawab(False, "Permintaan tidak dikenali.", 400)


# ---------------------------------------------------------------------------
# Transaksi per hari
# ---------------------------------------------------------------------------


@perlu_hp("laporan")
def transaksi(request):
    """Nota kasir dan nota tulis tangan untuk satu periode (bawaan hari ini), dikelompokkan per hari."""
    from collections import defaultdict
    from master.layanan import laporan
    from master.layanan.toko import toko_aktif
    hak = hak_pengguna(request.user)
    kode, awal, akhir, judul = _periode(request)
    mulai, selesai = laporan.rentang(awal, akhir)
    toko = toko_aktif(request)
    nota, _ = laporan.ambil(mulai, selesai, toko=toko)
    kembali = laporan.ambil_kembali(mulai, selesai, toko=toko)
    per_metode = defaultdict(lambda: [0, 0])
    for n in nota:
        per_metode[n["metode"]][0] += 1
        per_metode[n["metode"]][1] += n["total_amount"]
    lihat_modal = "lihat_modal" in hak
    laba = (sum(n["item_amount"] - n["modal"] for n in nota) - sum(k["uang"] - k["modal_kembali"] for k in kembali)) \
        if lihat_modal else None
    if not lihat_modal:
        for n in nota:
            n["laba"] = None
    omzet = sum(n["item_amount"] for n in nota) - sum(k["uang"] for k in kembali)
    BATAS = 300
    zona = timezone.get_current_timezone()
    hari = []
    for n in nota[:BATAS]:
        t = n["transaction_time"].astimezone(zona).date()
        if not hari or hari[-1]["tanggal"] != t:
            hari.append({"tanggal": t, "nota": [], "total": 0})
        hari[-1]["nota"].append(n)
        hari[-1]["total"] += n["total_amount"]
    return _tampil(request, "hp/transaksi.html", {
        "tab": "beranda", "periode": PERIODE, "p": kode, "judul_periode": judul, "awal": awal, "akhir": akhir,
        "judul_pendek": _judul_pendek(kode, awal, akhir),
        "hari_ini": timezone.localdate(), "satu_hari": awal == akhir, "hari": hari, "jumlah_nota": len(nota),
        "terpotong": len(nota) > BATAS, "batas": BATAS, "kembali": kembali,
        "omzet": omzet, "ongkir": sum(n["shipping_amount"] for n in nota), "lihat_modal": lihat_modal, "laba": laba,
        "margin": round(laba / omzet * 100) if lihat_modal and omzet else None,
        "per_metode": sorted(per_metode.items(), key=lambda x: -x[1][1]),
        "manual": sum(1 for n in nota if n.get("manual")),
    })


# ---------------------------------------------------------------------------
# Barang: harga, modal, stok
# ---------------------------------------------------------------------------


@perlu_hp()
def barang(request):
    from master.layanan import cari as svc_cari
    from django.db.models import Count, Max, Min, Sum, Value
    q = layanan.rapikan_teks(request.GET.get("q"))
    teks, harga = svc_cari.pisah_harga(q)
    teks = layanan.rapikan_teks(teks)
    daftar = []
    if teks or harga:
        qs = Item.objects.all()
        for kata in svc_cari.kata(teks):
            qs = qs.filter(Q(item_name__icontains=kata) | Q(variant__icontains=kata) | Q(brand__icontains=kata)
                           | Q(item_name__in=ItemInfo.objects.filter(alias__icontains=kata).values("item_name")))
        kelompok = (qs.values("item_name").annotate(n=Count("variant"), hmin=Min("max_price"), hmax=Max("max_price"),
                                                    stok=Sum("in_stock"), t=Min(svc_cari.tingkat(teks)),
                                                    j=Min(svc_cari.jarak_harga(harga)) if harga else Min(Value(0)))
                    .order_by("j", "t", "item_name")[:60])
        daftar = sorted(kelompok, key=lambda d: (d["j"], d["t"], kunci_alami(d["item_name"])))
        ids = dict(ItemInfo.objects.filter(item_name__in=[d["item_name"] for d in daftar]).values_list("item_name", "pk"))
        dekat = {}
        if harga:
            for r in Item.objects.filter(item_name__in=[d["item_name"] for d in daftar]).values("item_name", "variant", "max_price", "min_price"):
                lama = dekat.get(r["item_name"])
                if lama is None or svc_cari.jarak_py(harga, r["max_price"], r["min_price"]) < svc_cari.jarak_py(harga, lama["max_price"], lama["min_price"]):
                    dekat[r["item_name"]] = r
        from master.layanan import satuan as svc_satuan
        satuan = {v.item_name: svc_satuan.varian_satuan(v) for v in
                  VarianInfo.objects.filter(item_name__in=[d["item_name"] for d in daftar]).exclude(satuan="")}
        for d in daftar:
            d["id"] = ids.get(d["item_name"])
            sat, k = satuan.get(d["item_name"], ("", 1))
            if k > 1:
                # Barang per meter/kg: stok dan harga tampil per satuan (semua varian satu barang sama satuannya).
                d["stok_teks"] = svc_satuan.format_jumlah(d["stok"], sat, k)
            r = dekat.get(d["item_name"])
            if r:
                ket = svc_cari.keterangan_harga(harga, r["max_price"], r["min_price"])
                d["ket_harga"] = f"{r['variant']}: {ket}" if r["variant"] != "N/A" else ket
    hak = hak_pengguna(request.user)
    return _tampil(request, "hp/barang.html", {
        "tab": "barang", "q": q, "daftar": daftar, "harga_cari": harga,
        "boleh_tambah": {"ubah_info", "ubah_harga"} <= hak, "lihat_modal": "lihat_modal" in hak,
        "daftar_kategori": sorted({k for k in Item.objects.values_list("category", flat=True).distinct() if k}, key=kunci_alami)
        if {"ubah_info", "ubah_harga"} <= hak else [],
        "daftar_brand": sorted({k for k in Item.objects.values_list("brand", flat=True).distinct() if k}, key=kunci_alami)
        if {"ubah_info", "ubah_harga"} <= hak else [],
    })


@perlu_hp()
def barang_detail(request, item_id):
    from master.layanan import lokasi as svc_lokasi
    hak = hak_pengguna(request.user)
    info_item = get_object_or_404(ItemInfo, pk=item_id)
    baris = list(Item.objects.filter(item_name=info_item.item_name).values(
        "item_name", "variant", "category", "brand", "min_price", "max_price", "cost_amount", "in_stock"))
    baris.sort(key=lambda b: kunci_alami(b["variant"]))
    info = {v.variant: v for v in VarianInfo.objects.filter(item_name=info_item.item_name).select_related("rak")}
    gudang = svc_lokasi.jumlah_gudang([v.pk for v in info.values()])
    from master.models import Lokasi
    nama_lokasi = dict(Lokasi.objects.values_list("pk", "nama"))
    lihat_modal = "lihat_modal" in hak
    for b in baris:
        v = info.get(b["variant"])
        b["id"] = v.pk if v else None
        b["aktif"] = v.aktif if v else True
        b["rak"] = v.rak_id if v else None
        per = gudang.get(v.pk, {}) if v else {}
        b["gudang"] = [{"nama": nama_lokasi.get(l, "?"), "jumlah": n} for l, n in per.items()]
        b["toko"] = (b["in_stock"] or 0) - sum(per.values())
        if not lihat_modal:
            b["cost_amount"] = None
        _per_satuan_hp(b, v)
        b["margin"] = (round((b["max_price"] - b["cost_amount"]) / b["max_price"] * 100, 1)
                       if lihat_modal and b["max_price"] and b["cost_amount"] else None)
    aktif = [b for b in baris if b["aktif"]]
    nonaktif = [b for b in baris if not b["aktif"]]
    return _tampil(request, "hp/barang_detail.html", {
        "tab": "barang", "item": info_item, "aktif": aktif, "nonaktif": nonaktif,
        "kategori": baris[0]["category"] if baris else "", "brand": baris[0]["brand"] if baris else "",
        "lihat_modal": lihat_modal, "jenis_stok": list(layanan.JENIS_MUTASI.items()),
        "kembali": _kembali(request, "hp:barang"),
        "boleh_tambah": {"ubah_info", "ubah_harga"} <= hak,
        "satuan_barang": next((b["satuan"] for b in baris if b.get("satuan")), ""),
    })


def _per_satuan_hp(b, v):
    """Barang meteran/kiloan: harga per satuan, stok sebagai teks ("12,5 m")."""
    from master.layanan import satuan as svc_satuan
    sat, k = svc_satuan.varian_satuan(v) if v else ("", 1)
    b["k"], b["satuan"] = k, sat if k > 1 else ""
    if k <= 1:
        return
    for kolom in ("min_price", "max_price", "cost_amount"):
        if b.get(kolom) is not None:
            b[kolom] = b[kolom] * k
    b["akhiran"] = f"/{sat}"
    b["stok_teks"] = svc_satuan.format_jumlah(b["in_stock"] or 0, sat, k)
    b["toko_teks"] = svc_satuan.format_jumlah(b["toko"], sat, k)
    for g in b["gudang"]:
        g["teks"] = svc_satuan.format_jumlah(g["jumlah"], sat, k)


@require_POST
@perlu_hp("ubah_stok")
def stok(request):
    """Sesuaikan stok satu varian dari HP (JSON)."""
    hak = hak_pengguna(request.user)
    d = _json_badan(request)
    info = VarianInfo.objects.filter(pk=d.get("id") or 0, sampah__isnull=True).first()
    if info is None:
        return _jawab(False, "Barang tidak ditemukan.", 404)
    from master.layanan import satuan as svc_satuan
    sat, k = svc_satuan.varian_satuan(info)
    angka = d.get("angka")
    if k > 1:
        # Diketik per satuan (12,5 m) -> jumlah tersimpan (125).
        angka = svc_satuan.baca_jumlah(angka, k)
        if angka is None:
            return _jawab(False, f"Jumlah harus kelipatan {svc_satuan.format_jumlah(1, sat, k)}, misalnya 12,5.", 400)
    try:
        baru, selisih = layanan.sesuaikan_stok(request.user, info, cara=d.get("cara") or "tetapkan", angka=angka,
                                               jenis=d.get("jenis") or "hitung", catatan=d.get("catatan") or "", hak=hak)
    except Tolak as e:
        return _jawab(False, str(e), 400)
    teks = svc_satuan.format_jumlah(baru, sat, k)
    beda = svc_satuan.format_jumlah(abs(selisih), sat, k)
    tanda = f"+{beda}" if selisih > 0 else f"-{beda}"
    return _jawab(pesan=f"Stok jadi {teks} ({tanda})." if selisih else "Stok tidak berubah.", stok=baru, stok_teks=teks)


@require_POST
@perlu_hp("ubah_info")
def barang_tambah(request):
    """Barang baru dari HP (JSON): nama, kategori, brand, satu atau beberapa varian (10 Nov 2026)."""
    from django.urls import reverse

    from master.layanan.toko import toko_aktif
    d = _json_badan(request)
    varian = [v for v in d.get("varian") or [] if isinstance(v, dict)]
    try:
        info = layanan.tambah_barang(request.user, hak=hak_pengguna(request.user), item_name=d.get("item_name"),
                                     category=d.get("category"), brand=d.get("brand"), alias=d.get("alias"),
                                     varian=varian, toko=toko_aktif(request))
    except Tolak as e:
        return _jawab(False, str(e), 400, baris=getattr(e, "baris", None))
    return _jawab(pesan=f"'{info.item_name}' ditambahkan.", url=reverse("hp:barang_detail", args=[info.pk]))


@require_POST
@perlu_hp("ubah_info")
def varian_tambah(request, item_id):
    """Satu varian baru untuk barang yang sudah ada, dari HP (JSON). Ikut satuan barangnya."""
    info_item = get_object_or_404(ItemInfo, pk=item_id)
    try:
        layanan.tambah_varian_ke(request.user, info_item, _json_badan(request), hak=hak_pengguna(request.user))
    except Tolak as e:
        return _jawab(False, str(e), 400)
    return _jawab(pesan="Varian ditambahkan.")


# ---------------------------------------------------------------------------
# Tugas karyawan
# ---------------------------------------------------------------------------


def _karyawan_tugas():
    """Akun karyawan yang bisa diberi tugas, dengan jenis tugas dan izin data barangnya."""
    from master.layanan import tiket as svc_tiket
    hasil = []
    for u in get_user_model().objects.filter(is_active=True).order_by("first_name", "username"):
        h = hak_pengguna(u)
        jenis = svc_tiket.jenis_untuk(h) if "gudang" in h and not u.is_superuser else []
        if jenis:
            hasil.append({"u": u, "jenis": jenis, "izin": svc_tiket.izin_barang_untuk(h)})
    return hasil


JENIS_TUGAS = [("barang", "Data barang", "Harga jual, floor, alias, aktif/nonaktif"),
               ("hitung", "Hitung stok", "Hitung buta per lokasi; hasilnya diperiksa di Stok opname"),
               ("lokasi", "Pindah barang / rak", "Usul pindah stok antar lokasi atau ganti rak")]


@perlu_hp("kelola_lokasi")
def tugas(request):
    semua = list(Tiket.objects.select_related("untuk").prefetch_related("baris", "usulan").order_by("-id")[:80])
    for t in semua:
        t.jumlah = (len(t.usulan.all()) if t.jenis == "barang" else len(t.baris.all()) if t.jenis == "lokasi" else None)
    return _tampil(request, "hp/tugas.html", {
        "tab": "tugas",
        "kelompok": [("Menunggu ACC kamu", [t for t in semua if t.status == "dikirim"]),
                     ("Sedang dikerjakan", [t for t in semua if t.status == "terbuka" and not t.lewat_batas]),
                     ("Lewat batas", [t for t in semua if t.status == "terbuka" and t.lewat_batas]),
                     ("Selesai", [t for t in semua if t.status in ("selesai", "ditolak", "batal")][:20])],
    })


@perlu_hp("kelola_lokasi")
def tugas_baru(request):
    from master.layanan import lokasi as svc_lokasi
    from master.layanan import tiket as svc_tiket
    from master.models import Rak
    hak = hak_pengguna(request.user)
    jenis = request.POST.get("jenis") or request.GET.get("jenis") or "barang"
    if jenis not in dict((k, l) for k, l, _ in JENIS_TUGAS):
        jenis = "barang"
    galat = ""
    if request.method == "POST":
        untuk = get_user_model().objects.filter(pk=request.POST.get("untuk") or 0, is_active=True).first()
        jam = request.POST.get("jam") or "4"
        try:
            batas = timezone.now() + timedelta(hours=int(jam)) if jam.isdigit() else None
            t = svc_tiket.buka(request.user, untuk=untuk, tujuan=request.POST.get("tujuan"), batas=batas, hak=hak,
                               jenis=jenis, izin=request.POST.getlist("izin"), lingkup=request.POST.get("lingkup") or "",
                               kategori=request.POST.getlist("kategori"), brand=request.POST.getlist("brand"),
                               lokasi=request.POST.get("lokasi"), rak=request.POST.getlist("rak"))
            return redirect("hp:tugas_detail", tiket_id=t.id)
        except Tolak as e:
            galat = str(e)
    return _tampil(request, "hp/tugas_baru.html", {
        "tab": "tugas", "jenis": jenis, "jenis_tugas": JENIS_TUGAS,
        "karyawan": [k for k in _karyawan_tugas() if jenis in k["jenis"]], "galat": galat, "isi": request.POST,
        "izin": Tiket.IZIN_BARANG, "boleh_hitung": "opname" in hak,
        "daftar_kategori": sorted({k for k in Item.objects.values_list("category", flat=True).distinct() if k}, key=kunci_alami),
        "daftar_brand": sorted({b for b in Item.objects.values_list("brand", flat=True).distinct() if b}, key=kunci_alami),
        "lokasi": svc_lokasi.pilihan(), "daftar_rak": list(Rak.objects.select_related("tempat").order_by("kode")),
    })


@perlu_hp("kelola_lokasi")
def tugas_detail(request, tiket_id):
    from master.layanan import lokasi as svc_lokasi
    from master.layanan import tiket as svc_tiket
    from master.layanan import tiket_barang as svc_barang
    from master.views.lokasi import _ringkasan_tugas
    hak = hak_pengguna(request.user)
    t = get_object_or_404(Tiket.objects.select_related("untuk"), pk=tiket_id)
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "terapkan" and t.jenis == "barang":
                ok, gagal, tolak = svc_barang.terapkan(request.user, t, request.POST, hak=hak)
                pesan = f"{ok} usulan diterapkan, {tolak} tidak di-ACC." + (f" {gagal} gagal." if gagal else "")
            elif aksi == "terapkan" and t.jenis == "lokasi":
                ok, gagal = svc_tiket.terapkan(request.user, t, request.POST, hak=hak)
                pesan = f"{ok} usulan diterapkan." + (f" {gagal} gagal, lihat keterangannya." if gagal else "")
            elif aksi == "teruskan" and t.jenis == "hitung":
                from master.layanan import opname as svc_opname
                svc_opname.teruskan_tugas(request.user, t, hak=hak)
                pesan = "Hitungan diteruskan ke Stok opname."
            elif aksi == "tolak":
                svc_tiket.tolak(request.user, t, request.POST.get("alasan") or "Ditolak dari HP", hak=hak)
                pesan = "Tugas ditolak."
            elif aksi == "batal":
                svc_tiket.batalkan(request.user, t, hak=hak)
                pesan = "Tugas dibatalkan."
            else:
                raise Tolak("Aksi tidak dikenal.")
            return _jawab(pesan=pesan)
        except Tolak as e:
            return _jawab(False, str(e), 400)
    isi = {"tab": "tugas", "t": t, "ringkas": _ringkasan_tugas(t), "lihat_modal": "lihat_modal" in hak, "baris": []}
    if t.jenis == "barang":
        isi["baris"] = svc_barang.rincian(t, lihat_modal="lihat_modal" in hak)
    elif t.jenis == "lokasi":
        baris = list(t.baris.select_related("varian__rak", "rak__tempat", "dari", "ke"))
        for b in baris:
            if b.jenis == "pindah":
                b.stok_asal = svc_lokasi.stok_di(b.varian, b.dari)
        isi.update(baris=baris, lokasi=svc_lokasi.pilihan())
    else:
        from master.models import Opname
        op = Opname.objects.filter(tiket=t).first()
        isi.update(op=op, jumlah_hitung=op.baris.count() if op else 0)
    return _tampil(request, "hp/tugas_detail.html", isi)


# ---------------------------------------------------------------------------
# Input: nota tulis tangan dan pembelian
# ---------------------------------------------------------------------------


@perlu_hp("pembelian")
def input_(request):
    from master.layanan import lokasi as svc_lokasi
    from master.layanan.faktur import MAKS_FOTO
    hak = hak_pengguna(request.user)
    return _tampil(request, "hp/input.html", {
        "tab": "", "hak_beli": "ubah_stok" in hak, "hari_ini": timezone.localdate(),
        "supplier": Supplier.objects.filter(aktif=True).order_by("nama"), "lokasi": svc_lokasi.pilihan(),
        "maks_foto": MAKS_FOTO, "mode_ppn": Pembelian.MODE_PPN,
    })


@require_POST
@perlu_hp("pembelian")
def input_beli(request):
    """Pembelian dari distributor (multipart, `baris` berupa JSON).

    mode=datang: nota/faktur sudah di tangan dan barangnya sudah datang. PO dibuat, dicatat datang,
    lalu di-ACC sekaligus: stok, modal, dan hutang berubah. mode=pesan: hanya PO (dipesan, ditulis);
    gudang mencatat datang nanti lalu pemilik ACC. Foto nota ikut disimpan di keduanya.
    """
    from django.db import transaction
    from master.layanan import pembelian as svc
    from master.layanan.toko import toko_aktif
    hak = hak_pengguna(request.user)
    if "ubah_stok" not in hak:
        return _jawab(False, "Akun ini tidak boleh mengubah stok.", 403)
    d = request.POST
    try:
        baris = json.loads(d.get("baris") or "[]")
    except ValueError:
        baris = None
    if not isinstance(baris, list) or not baris:
        return _jawab(False, "Tambahkan minimal satu barang.", 400)
    datang = d.get("mode") == "datang"
    # Dari HP tanggal selalu hari ini; tanggal lain diubah dari master.
    hari_ini = timezone.localdate().isoformat()
    try:
        with transaction.atomic():
            po = svc.buat(request.user, supplier_id=d.get("supplier"), tanggal_pesan=hari_ini,
                          catatan=d.get("catatan") or "", toko=toko_aktif(request))
            for b in baris:
                if not isinstance(b, dict):
                    raise Tolak("Daftar barang tidak terbaca. Muat ulang halaman lalu coba lagi.")
                svc.tambah_baris(po, varian_id=b.get("id"), jumlah=b.get("jumlah"), harga=b.get("harga"))
            svc.tambah_foto(request.user, po, request.FILES.getlist("foto"))
            if datang:
                isi = {"mode_ppn": d.get("mode_ppn") or po.mode_ppn, "tanggal_terima": hari_ini,
                       "tanggal_faktur": hari_ini, "nomor_faktur": d.get("nomor_faktur") or "",
                       "jatuh_tempo": d.get("jatuh_tempo") or "", "ongkir": d.get("ongkir") or "",
                       "lokasi_semua": d.get("lokasi") or ""}
                for pb in po.baris.all():
                    isi[f"terima_{pb.id}"] = pb.jumlah_pesan
                    isi[f"harga_{pb.id}"] = pb.harga
                svc.catat_datang(request.user, po, isi)
                svc.acc(request.user, po)
                pesan = f"{po.nomor} tersimpan: stok, modal, dan hutang sudah diperbarui."
            else:
                pesan = f"{po.nomor} tersimpan sebagai pesanan. Saat barang datang, gudang mencatatnya lalu kamu ACC."
    except Tolak as e:
        return _jawab(False, str(e), 400)
    return _jawab(pesan=pesan, nomor=po.nomor)


# ---------------------------------------------------------------------------
# Setelan dan notifikasi
# ---------------------------------------------------------------------------


@perlu_hp()
def setelan(request):
    from master.layanan import push as svc_push
    from master.layanan.toko import toko_aktif
    from master.models import PushLangganan
    hak = hak_pengguna(request.user)
    from master.akun.kunci_hp import KUNCI_DETIK
    from master.models import PasskeyHp
    return _tampil(request, "hp/setelan.html", {
        "tab": "", "passkey": PasskeyHp.objects.filter(user=request.user), "kunci_menit": KUNCI_DETIK // 60, "toko": toko_aktif(request), "kunci_push": svc_push.kunci().kunci_publik,
        "perangkat": PushLangganan.objects.filter(user=request.user),
        "dapat": [label for jenis, label in (("nota", "Nota kasir ditahan (mis. di bawah floor)"),
                                              ("tugas", "Tugas karyawan selesai dikerjakan"),
                                              ("dus", "Buka dus dari gudang"), ("po", "Barang datang, PO menunggu ACC"),
                                              ("piutang", "Invoice / titip barang menunggu ACC"),
                                              ("tempo", "Piutang pelanggan lewat jatuh tempo"),
                                              ("kas", "Tutup kas menunggu ACC"),
                                              ("biaya", "Uang keluar dari kasir menunggu ACC"),
                                              ("gaji", "Pengingat gajian jam 12.00"))
                  if svc_push.HAK_JENIS[jenis] in hak],
    })


@require_POST
@perlu_hp()
def push(request):
    """Notifikasi web: simpan/hapus langganan HP ini, atau kirim pesan percobaan (JSON)."""
    from master.layanan import push as svc_push
    from master.models import PushLangganan
    d = _json_badan(request)
    aksi = d.get("aksi")
    try:
        if aksi == "langganan":
            pertama = not PushLangganan.objects.exists()
            svc_push.simpan_langganan(request.user, d.get("langganan"), request.META.get("HTTP_USER_AGENT", ""))
            if pertama:
                svc_push.tandai_semua()     # yang sudah menunggu sebelum ini tidak dikirim sekaligus
            return _jawab(pesan="Notifikasi menyala di perangkat ini.")
        if aksi == "berhenti":
            PushLangganan.objects.filter(user=request.user, endpoint=d.get("endpoint") or "").delete()
            return _jawab(pesan="Notifikasi dimatikan di perangkat ini.")
        if aksi == "tes":
            qs = PushLangganan.objects.filter(user=request.user, endpoint=d.get("endpoint") or "")
            if not qs.exists():
                raise Tolak("Perangkat ini belum menyalakan notifikasi.")
            ok = svc_push.kirim_ke(qs, {"title": "DB Toko", "body": "Tes notifikasi berhasil. 👍", "url": "/hp/setelan/",
                                        "tag": "tes", "badge": _jumlah_acc(request)})
            if not ok:
                raise Tolak("Pesan tidak sampai ke layanan notifikasi. Periksa internet server lalu coba lagi.")
            return _jawab(pesan="Tes terkirim. Notifikasi muncul dalam beberapa detik.")
    except Tolak as e:
        return _jawab(False, str(e), 400)
    return _jawab(False, "Permintaan tidak dikenali.", 400)


SERVICE_WORKER = """// Service worker aplikasi HP: hanya notifikasi (tidak menyimpan halaman untuk offline).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {body: e.data && e.data.text()}; }
  const tugas = [self.registration.showNotification(d.title || 'DB Toko', {
    body: d.body || '', tag: d.tag || undefined, data: {url: d.url || '/hp/'}, icon: '/hp/ikon-192.png', badge: '/hp/ikon-192.png'})];
  if (self.navigator && 'setAppBadge' in self.navigator && typeof d.badge === 'number')
    tugas.push(d.badge ? self.navigator.setAppBadge(d.badge) : self.navigator.clearAppBadge());
  e.waitUntil(Promise.all(tugas).catch(() => {}));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/hp/';
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    for (const c of list) { if ('focus' in c) { c.navigate(url).catch(() => {}); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
"""


def service_worker(request):
    resp = HttpResponse(SERVICE_WORKER, content_type="application/javascript")
    resp["Cache-Control"] = "no-cache"
    resp["Service-Worker-Allowed"] = "/hp/"
    return resp


# ---------------------------------------------------------------------------
# Kunci aplikasi dan passkey (Face ID / sidik jari)
# ---------------------------------------------------------------------------


def _ke_aman(alamat, bawaan="/hp/"):
    return alamat if alamat and alamat.startswith("/") and not alamat.startswith("//") else bawaan


def _perangkat(request):
    ua = request.META.get("HTTP_USER_AGENT", "")
    for kata, nama in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"), ("Windows", "Windows")):
        if kata in ua:
            return nama
    return "Perangkat"


def kunci(request):
    """Layar kunci: buka dengan passkey atau kata sandi. Tidak lewat perlu_hp (middleware membebaskannya)."""
    from master.akun import masuk as svc_masuk
    from master.akun.kunci_hp import tandai_terbuka, terkunci
    from master.models import PasskeyHp
    tujuan = _ke_aman(request.POST.get("next") or request.GET.get("next"))
    if not request.user.is_authenticated:
        from urllib.parse import quote
        from django.urls import reverse
        return redirect(f"{reverse('hp:masuk')}?next={quote(tujuan)}")
    galat = ""
    if request.method == "POST":
        nama = request.user.username
        if svc_masuk.gagal(nama) >= svc_masuk.BATAS:
            galat = f"Terlalu banyak percobaan salah. Coba lagi {svc_masuk.KUNCI // 60} menit lagi."
        elif request.user.check_password(request.POST.get("password") or ""):
            from django.core.cache import cache
            cache.delete(svc_masuk._kunci(nama))
            tandai_terbuka(request)
            return redirect(tujuan)
        else:
            from django.core.cache import cache
            k = svc_masuk._kunci(nama)
            cache.set(k, cache.get(k, 0) + 1, svc_masuk.KUNCI)
            galat = "Kata sandi salah."
    elif not terkunci(request):
        return redirect(tujuan)
    return render(request, "hp/kunci.html", {
        "next": tujuan, "galat": galat, "punya_passkey": PasskeyHp.objects.filter(user=request.user).exists(),
    })


def kunci_opsi(request):
    from master.layanan import passkey
    if not request.user.is_authenticated:
        return _jawab(False, "Belum masuk.", 401)
    return JsonResponse(passkey.opsi_masuk(request, request.user))


@require_POST
def kunci_buka(request):
    from master.akun.kunci_hp import tandai_terbuka
    from master.layanan import passkey
    if not request.user.is_authenticated:
        return _jawab(False, "Belum masuk.", 401)
    d = _json_badan(request)
    try:
        passkey.verifikasi(request, d.get("kredensial") or {}, user=request.user)
    except Tolak as e:
        return _jawab(False, str(e), 400)
    tandai_terbuka(request)
    return _jawab(pesan="Terbuka.", next=_ke_aman(d.get("next")))


def masuk_opsi(request):
    from master.layanan import passkey
    return JsonResponse(passkey.opsi_masuk(request))


@require_POST
def masuk_passkey(request):
    from django.contrib.auth import login
    from master.akun.kunci_hp import tandai_terbuka
    from master.layanan import passkey
    d = _json_badan(request)
    try:
        p = passkey.verifikasi(request, d.get("kredensial") or {})
    except Tolak as e:
        return _jawab(False, str(e), 400)
    login(request, p.user, backend="django.contrib.auth.backends.ModelBackend")
    tandai_terbuka(request)
    return _jawab(pesan="Masuk.", next=_ke_aman(d.get("next")))


@perlu_hp()
def passkey_opsi_daftar(request):
    from master.layanan import passkey
    return JsonResponse(passkey.opsi_daftar(request, request.user))


@require_POST
@perlu_hp()
def passkey_daftar(request):
    from master.layanan import passkey
    d = _json_badan(request)
    try:
        p = passkey.daftar(request, request.user, d.get("kredensial") or {}, nama=_perangkat(request))
    except Tolak as e:
        return _jawab(False, str(e), 400)
    layanan.catat(request.user, objek="akun", kolom="passkey", baru=f"Passkey didaftarkan: {p.nama}")
    return _jawab(pesan=f"Terdaftar. Berikutnya bisa masuk dan membuka kunci dengan {p.nama}.")


@require_POST
@perlu_hp()
def passkey_hapus(request):
    from master.models import PasskeyHp
    d = _json_badan(request)
    n, _ = PasskeyHp.objects.filter(user=request.user, pk=d.get("id") or 0).delete()
    if n:
        layanan.catat(request.user, objek="akun", kolom="passkey", baru="Passkey dihapus")
    return _jawab(pesan="Perangkat dihapus." if n else "Tidak ditemukan.")


@perlu_hp()
def detak(request):
    """Dipanggil saat aplikasi sedang dipakai (tanpa pindah halaman) supaya tidak terkunci di tengah jalan."""
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------
# PWA: manifest dan ikon (server tidak melayani berkas statis)
# ---------------------------------------------------------------------------


def manifest(request):
    from django.urls import reverse
    data = {
        "name": "DB Toko", "short_name": "DB Toko", "start_url": reverse("hp:beranda"), "scope": reverse("hp:beranda"),
        "display": "standalone", "background_color": "#F2F2F7", "theme_color": "#F2F2F7", "id": reverse("hp:beranda"),
        "icons": [{"src": reverse("hp:ikon", args=[n]), "sizes": f"{n}x{n}", "type": "image/png"} for n in (192, 512)],
    }
    return HttpResponse(json.dumps(data), content_type="application/manifest+json")


def ikon(request, ukuran):
    from pathlib import Path
    from django.http import Http404
    if ukuran not in (180, 192, 512):
        raise Http404
    berkas = Path(__file__).resolve().parent.parent / "hp_aset" / f"ikon-{ukuran}.png"
    resp = HttpResponse(berkas.read_bytes(), content_type="image/png")
    resp["Cache-Control"] = "public, max-age=604800"
    return resp


# ---------------------------------------------------------------------------
# Piutang & pesanan pelanggan
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PO: siapa yang menerima (11 Nov 2026). PO belum ditugaskan tidak tampil di gudang siapa pun.
# ---------------------------------------------------------------------------


@perlu_hp("pembelian")
def po(request):
    from master.models import Pembelian
    toko, _ = _toko_hp(request)
    qs = Pembelian.objects.filter(status="dibuat").select_related("supplier", "penerima").order_by("tanggal_pesan", "id")
    if toko is not None:
        qs = qs.filter(toko=toko)
    daftar = list(qs.annotate(n=Count("baris")))
    return _tampil(request, "hp/po.html", {"tab": "", "belum": [p for p in daftar if not p.penerima_id],
                                           "sudah": [p for p in daftar if p.penerima_id]})


@perlu_hp("pembelian")
def po_detail(request, po_id):
    from master.layanan import pembelian as svc
    from master.models import Pembelian
    p = get_object_or_404(Pembelian.objects.select_related("supplier", "penerima"), pk=po_id)
    if request.method == "POST":
        d = _json_badan(request)
        try:
            if d.get("aksi") == "penerima":
                svc.atur_penerima(request.user, p, penerima=str(d.get("penerima") or ""))
                return _jawab(pesan="Penerima tersimpan." if p.penerima else "PO belum ditugaskan.")
            if d.get("aksi") == "pecah":
                baru = svc.pecah_per_pic(request.user, p)
                return _jawab(pesan="Dipecah jadi " + ", ".join(x.nomor for x in baru) + ".")
        except Tolak as e:
            return _jawab(False, str(e), 400)
        return _jawab(False, "Permintaan tidak dikenali.", 400)
    boleh = {u.pk: u for u in svc.penerima_boleh()}
    baris = []
    for b, pic, _ in svc._pic_baris(p):
        b.pic = boleh.get(pic)
        baris.append(b)
    return _tampil(request, "hp/po_detail.html", {
        "tab": "", "p": p, "baris": baris, "penerima_boleh": list(boleh.values()),
        "usulan": svc.usulan_penerima(p)[0] if p.status == "dibuat" else None,
        "rincian": svc.rincian_pic(p) if p.status == "dibuat" else []})


@perlu_hp("piutang")
def piutang(request):
    from master.layanan import piutang as svc
    svc.sinkron_kasir()
    tab = request.GET.get("tab") or "belum_lunas"
    if tab not in dict(svc.TAB):
        tab = "belum_lunas"
    qs = svc.daftar(tab)
    cari = (request.GET.get("q") or "").strip()
    if cari:
        from django.db.models import Q
        qs = qs.filter(Q(nama__icontains=cari) | Q(telepon__icontains=cari) | Q(nomor__icontains=cari))
    baris = list(qs[:150])
    hari_ini = timezone.localdate()
    for p in baris:
        p.lewat = bool(p.jatuh_tempo and p.jatuh_tempo < hari_ini and not p.lunas)
    return _tampil(request, "hp/piutang.html", {
        "tab": "", "pilih": tab, "tabs": svc.TAB, "hitung": svc.hitungan(), "baris": baris, "cari": cari,
        "ringkas": svc.ringkas()})


@perlu_hp("piutang")
def piutang_detail(request, piutang_id):
    from master.layanan import piutang as svc
    from master.models import Piutang
    p = get_object_or_404(Piutang.objects.select_related("toko", "nota_manual"), pk=piutang_id)
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        d = _json_badan(request)
        aksi = d.get("aksi")
        try:
            if aksi == "bayar":
                svc.bayar(request.user, p, jumlah=d.get("jumlah"), cara=d.get("cara") or "cash",
                          catatan=d.get("catatan") or "", sumber="hp")
                p.refresh_from_db()
                return _jawab(pesan="Pembayaran tercatat." + (" Lunas." if p.lunas else f" Sisa {svc.rp(p.sisa)}."))
            if aksi == "serahkan":
                svc.serahkan(request.user, p)
                return _jawab(pesan="Barang ditandai sudah diambil.")
            if aksi in ("setuju", "tolak"):
                if "acc_piutang" not in hak:
                    raise Tolak("Akun ini tidak boleh memberi ACC.")
                svc.acc(request.user, p, setuju=aksi == "setuju", catatan=d.get("catatan") or "")
                return _jawab(pesan="Disetujui." if aksi == "setuju" else "Ditolak.")
        except Tolak as e:
            return _jawab(False, str(e), 400)
        return _jawab(False, "Permintaan tidak dikenali.", 400)
    return _tampil(request, "hp/piutang_detail.html", {
        "tab": "", "p": p, "pembayaran": p.pembayaran.all(), "cara": [(k, v) for k, v in svc.CARA_BAYAR.items() if k != "campur"],
        "boleh_acc": "acc_piutang" in hak, "wa": svc.wa_url(p) if p.telepon else "",
        "barang": svc.barang(p),
        "lewat": bool(p.jatuh_tempo and p.jatuh_tempo < timezone.localdate() and not p.lunas)})


# ---------------------------------------------------------------------------
# Kas laci (rancangan 3 Nov 2026)
# ---------------------------------------------------------------------------


@perlu_hp("kas")
def kas(request):
    import uuid as _uuid
    from master.layanan import kas as svc
    from master.layanan.toko import toko_aktif
    toko = toko_aktif(request)
    if request.method == "POST":
        d = _json_badan(request)
        aksi = d.get("aksi")
        try:
            if aksi == "buka":
                svc.buka(request.user, toko, d, dari="hp")
                return _jawab(pesan="Kas dibuka.")
            if aksi == "tutup":
                s = svc.tutup(request.user, d.get("sesi_id"), d, dari="hp")
                return _jawab(pesan="Kas ditutup. Selisih " + svc.rp(s.selisih) + ".", sesi_id=str(s.pk))
            if aksi == "gerak":
                g = svc.catat_gerak(request.user, toko, d, dari="hp")
                return _jawab(pesan=f"{g.get_jenis_display()} {svc.rp(g.jumlah)} tercatat.")
            if aksi == "batal":
                svc.batal_gerak(request.user, d.get("gerak_id"), d.get("alasan"))
                return _jawab(pesan="Catatan dibatalkan.")
        except Tolak as e:
            return _jawab(False, str(e), 400)
        return _jawab(False, "Permintaan tidak dikenali.", 400)
    return _tampil(request, "hp/kas.html", {
        "tab": "", "toko": toko, "k": svc.ringkas(toko), "riwayat": svc.riwayat(toko, 14),
        "kategori": svc.kategori_keluar(), "gerak_baru": _uuid.uuid4()})


@perlu_hp("biaya")
def biaya(request):
    """Catat pengeluaran toko dari HP + daftar bulan ini (rancangan pembukuan 7 Nov 2026)."""
    from master.layanan import biaya as svc
    from master.layanan import kas as svc_kas
    from master.layanan.toko import toko_aktif
    toko = toko_aktif(request)
    if request.method == "POST":
        try:
            if request.POST.get("aksi") == "batal":
                svc.batal(request.user, request.POST.get("id"), request.POST.get("alasan"))
                return _jawab(pesan="Catatan dibatalkan.")
            b = svc.catat(request.user, toko, request.POST, request.FILES.get("foto"), dari="hp")
            return _jawab(pesan=f"{b.kategori} {svc.rp(b.jumlah)} tercatat.")
        except Tolak as e:
            return _jawab(False, str(e), 400)
    svc.tarik_dari_kas()
    awal, akhir = svc.rentang_bulan(request.GET.get("bulan") or "")
    return _tampil(request, "hp/biaya.html", {
        "tab": "", "toko": toko, "awal": awal, "r": svc.ringkas(toko, awal, akhir),
        "daftar": svc.daftar(toko, awal, akhir).exclude(status="batal")[:100],
        "kategori": svc.kategori_aktif(), "boleh_laci": svc_kas.punya_kas(toko), "hari_ini": timezone.localdate(),
    })


@perlu_hp("kas")
def kas_detail(request, sesi_id):
    from master.layanan import kas as svc
    d = svc.detail(sesi_id)
    if d is None:
        from django.http import Http404
        raise Http404
    if request.method == "POST":
        b = _json_badan(request)
        try:
            svc.acc(request.user, sesi_id, b.get("aksi") == "terima", b.get("catatan") or "")
        except Tolak as e:
            return _jawab(False, str(e), 400)
        return _jawab(pesan="Tutup kas diterima." if b.get("aksi") == "terima" else "Tutup kas ditolak.")
    return _tampil(request, "hp/kas_detail.html", {"tab": "", **d})


# ---------------------------------------------------------------------------
# Periksa opname (satu tugas hitung = satu ACC; rinciannya per barang)
# ---------------------------------------------------------------------------


@perlu_hp("opname")
def opname(request, opname_id):
    from master.layanan import opname as svc
    from master.models import Opname
    op = get_object_or_404(Opname.objects.exclude(status="draf").select_related("toko", "lokasi"), pk=opname_id)
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        d = _json_badan(request)
        try:
            if d.get("aksi") == "acc":
                pilih = d.get("pilih") or []
                svc.acc(request.user, op, pilih, hak=hak)
                return _jawab(pesan=f"Opname diterima: {len(pilih)} barang diterapkan.")
            if d.get("aksi") == "tolak":
                svc.tolak(request.user, op, d.get("alasan"), hak=hak)
                return _jawab(pesan="Opname ditolak.")
        except Tolak as e:
            return _jawab(False, str(e), 400)
        return _jawab(False, "Permintaan tidak dikenali.", 400)
    baris = svc.rincian(op)
    return _tampil(request, "hp/opname.html", {
        "tab": "", "op": op, "beda": [b for b in baris if b.selisih], "cocok": [b for b in baris if not b.selisih],
        "lihat_modal": "lihat_modal" in hak, "punya_stok": op.toko is None or op.toko.stok_di_kasir,
    })


@perlu_hp("opname")
def opname_ulang(request):
    """Hitung ulang opname dari HP (pemilik/Mama). Hitungan awal disembunyikan sampai pemeriksa mengisi."""
    from master.layanan import sesi_opname as svc_sesi
    from master.templatetags.master_tags import qty
    sesi = svc_sesi.sesi_buka()
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        if sesi is None:
            return _jawab(False, "Tidak ada sesi opname yang berjalan.", 400)
        d = _json_badan(request)
        try:
            p = svc_sesi.isi_ulang(request.user, sesi, d.get("varian"), d.get("lokasi"), d.get("jumlah"), hak=hak)
        except Tolak as e:
            return _jawab(False, str(e), 400)
        beda = p.ulang - (p.awal or 0)
        return _jawab(pesan="Tersimpan.", awal=qty(p.awal, p.varian), ulang=qty(p.ulang, p.varian), beda=beda,
                      beda_teks=qty(abs(beda), p.varian))
    grup = []
    selesai = 0
    if sesi is not None:
        r = svc_sesi.rekap(sesi)
        per = {}
        for b in r["menunggu_ulang"]:
            raks = sorted({h.rak_id or "" for h in b["tempat"]}) or [b["v"].rak_id or ""]
            kunci = (b["lokasi"].urutan, b["lokasi"].nama, raks[0])
            per.setdefault(kunci, []).append({**b, "raks": [x or "tanpa rak" for x in raks]})
        grup = [{"lokasi": k[1], "rak": k[2] or "tanpa rak", "baris": v} for k, v in sorted(per.items())]
        selesai = len(r["diperiksa"])
    return _tampil(request, "hp/opname_ulang.html", {"tab": "", "sesi": sesi, "grup": grup, "selesai": selesai})


@perlu_hp()
def menu(request):
    """Tab Menu: kumpulan fitur di luar tab bawah. Isi tergantung hak akun dan mode tampilan."""
    hak = hak_pengguna(request.user)
    simpel = mode_hp(request) == "simpel"
    opname_ulang = 0
    if "opname" in hak:
        from master.layanan import sesi_opname as svc_sesi
        sesi = svc_sesi.sesi_buka()
        opname_ulang = svc_sesi.jumlah_menunggu(sesi) if sesi else 0
    return _tampil(request, "hp/menu.html", {"tab": "menu", "simpel": simpel, "opname_ulang": opname_ulang})


@require_POST
@perlu_hp()
def mode(request):
    from master.models import SetelanHp
    pilih = _json_badan(request).get("mode")
    if pilih not in dict(SetelanHp.MODE):
        return _jawab(False, "Pilihan tidak dikenal.", 400)
    SetelanHp.objects.update_or_create(user=request.user, defaults={"mode": pilih})
    return _jawab(pesan="Tampilan simpel." if pilih == "simpel" else "Tampilan lanjutan.")


@perlu_hp("gaji")
def gaji(request):
    """Gaji di HP: yang jatuh tempo (tandai sudah dibayar) dan aturan gaji tiap orang. Aturan diubah di master."""
    from master.layanan import gaji as svc
    from master.layanan.toko import toko_aktif
    hak = hak_pengguna(request.user)
    if request.method == "POST":
        d = _json_badan(request)
        try:
            b = svc.bayar(request.user, d.get("gaji"), d.get("tanggal"), hak=hak, sumber=d.get("sumber") or "luar_laci",
                          hari_masuk=d.get("hari_masuk"), toko=toko_aktif(request))
        except Tolak as e:
            return _jawab(False, str(e), 400)
        return _jawab(pesan=f"Gaji {b.gaji.nama} {svc.rp(b.jumlah)} dicatat dibayar.")
    orang = [k for k in svc.daftar() if k["gaji"] and k["gaji"].aktif]
    for k in orang:
        k["aturan"] = svc.ringkas_aturan(k["gaji"])
    return _tampil(request, "hp/gaji.html", {"tab": "menu", "tempo": svc.jatuh_tempo(), "orang": orang,
                                             "riwayat": svc.riwayat(10)})
