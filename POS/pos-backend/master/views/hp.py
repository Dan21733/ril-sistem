"""Aplikasi pemilik di HP (/hp/): ringkas, besar, untuk iPhone dan Android (PWA, bukan kiosk).

Semua aturan dan hak sama dengan master: halaman ini hanya tampilan lain di atas layanan yang
sama (master/layanan/*). Tidak ada aturan bisnis baru di sini.
"""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
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


def _jumlah_acc(request):
    """Angka di tab Beranda: yang menunggu keputusan pemilik (sesuai hak akun)."""
    hak = hak_pengguna(request.user)
    n = 0
    if "kelola_kasir" in hak:
        from pos.models import NotaTinjau
        n += min(NotaTinjau.objects.filter(status="menunggu").count(), 30)
    if "kelola_lokasi" in hak:
        n += Tiket.objects.filter(status="dikirim").count()
    if "ubah_stok" in hak:
        n += BukaDus.objects.filter(status="menunggu").count()
    if "acc_piutang" in hak:
        from master.models import Piutang
        n += Piutang.objects.filter(batal=False, acc_status="menunggu").count()
    return n


def _tampil(request, nama, isi):
    isi.setdefault("jumlah_acc", _jumlah_acc(request))
    return render(request, nama, isi)


def _jawab(ok=True, pesan="", status=200, **lain):
    return JsonResponse({"ok": ok, "pesan": pesan, **lain}, status=status)


# ---------------------------------------------------------------------------
# Beranda
# ---------------------------------------------------------------------------


@perlu_hp()
def beranda(request):
    from master.layanan import dasbor
    from master.layanan.toko import toko_aktif
    hak = hak_pengguna(request.user)
    toko = toko_aktif(request)
    kode, awal, akhir, judul = _periode(request)
    hari_ini = timezone.localdate()
    jual = dasbor.periode(toko, hak, awal, akhir) if "laporan" in hak else None
    lain = dasbor.lain(request, hak, toko)
    nota = []
    if "kelola_kasir" in hak:
        from master.layanan import nota_tinjau
        nota = nota_tinjau.daftar()[:30]
    tugas = list(Tiket.objects.filter(status="dikirim").select_related("untuk")[:30]) if "kelola_lokasi" in hak else []
    dus = list(BukaDus.objects.filter(status="menunggu").select_related("konversi__dari", "konversi__ke")[:30]) \
        if "ubah_stok" in hak else []
    piutang_acc, piutang_ringkas = [], None
    if "acc_piutang" in hak:
        from master.models import Piutang
        piutang_acc = list(Piutang.objects.filter(batal=False, acc_status="menunggu").select_related("pelanggan")[:30])
    if "piutang" in hak:
        from master.layanan import piutang as svc_piutang
        piutang_ringkas = svc_piutang.ringkas()
    jam = timezone.localtime().hour
    sapa = "Selamat pagi" if jam < 11 else "Selamat siang" if jam < 15 else "Selamat sore" if jam < 18 else "Selamat malam"
    return render(request, "hp/beranda.html", {
        "tab": "beranda", "jual": jual, "lain": lain, "periode": PERIODE, "p": kode, "judul_periode": judul,
        "awal": awal, "akhir": akhir, "hari_ini": hari_ini,
        "pembanding": _pembanding(kode), "nota": nota, "tugas": tugas, "dus": dus,
        "piutang_acc": piutang_acc, "piutang_ringkas": piutang_ringkas,
        "jumlah_acc": len(nota) + len(tugas) + len(dus) + len(piutang_acc), "sapa": sapa,
        "pekerja": list(Worker.objects.order_by("name").values_list("id", "name")),
    })


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
        "tab": "transaksi", "periode": PERIODE, "p": kode, "judul_periode": judul, "awal": awal, "akhir": akhir,
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
        for d in daftar:
            d["id"] = ids.get(d["item_name"])
            r = dekat.get(d["item_name"])
            if r:
                ket = svc_cari.keterangan_harga(harga, r["max_price"], r["min_price"])
                d["ket_harga"] = f"{r['variant']}: {ket}" if r["variant"] != "N/A" else ket
    return _tampil(request, "hp/barang.html", {"tab": "barang", "q": q, "daftar": daftar, "harga_cari": harga})


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
        b["margin"] = (round((b["max_price"] - b["cost_amount"]) / b["max_price"] * 100, 1)
                       if lihat_modal and b["max_price"] and b["cost_amount"] else None)
    aktif = [b for b in baris if b["aktif"]]
    nonaktif = [b for b in baris if not b["aktif"]]
    return _tampil(request, "hp/barang_detail.html", {
        "tab": "barang", "item": info_item, "aktif": aktif, "nonaktif": nonaktif,
        "kategori": baris[0]["category"] if baris else "", "brand": baris[0]["brand"] if baris else "",
        "lihat_modal": lihat_modal, "jenis_stok": list(layanan.JENIS_MUTASI.items()),
        "kembali": _kembali(request, "hp:barang"),
    })


@require_POST
@perlu_hp("ubah_stok")
def stok(request):
    """Sesuaikan stok satu varian dari HP (JSON)."""
    hak = hak_pengguna(request.user)
    d = _json_badan(request)
    info = VarianInfo.objects.filter(pk=d.get("id") or 0, sampah__isnull=True).first()
    if info is None:
        return _jawab(False, "Barang tidak ditemukan.", 404)
    try:
        baru, selisih = layanan.sesuaikan_stok(request.user, info, cara=d.get("cara") or "tetapkan", angka=d.get("angka"),
                                               jenis=d.get("jenis") or "hitung", catatan=d.get("catatan") or "", hak=hak)
    except Tolak as e:
        return _jawab(False, str(e), 400)
    tanda = f"+{selisih}" if selisih > 0 else str(selisih)
    return _jawab(pesan=f"Stok jadi {baru} ({tanda})." if selisih else "Stok tidak berubah.", stok=baru)


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
        "daftar_kategori": sorted({k for k in Item.objects.values_list("category", flat=True) if k}, key=kunci_alami),
        "daftar_brand": sorted({b for b in Item.objects.values_list("brand", flat=True) if b}, key=kunci_alami),
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
        "tab": "input", "hak_beli": "ubah_stok" in hak, "hari_ini": timezone.localdate(),
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
    try:
        with transaction.atomic():
            po = svc.buat(request.user, supplier_id=d.get("supplier"), tanggal_pesan=d.get("tanggal"),
                          catatan=d.get("catatan") or "", toko=toko_aktif(request))
            for b in baris:
                if not isinstance(b, dict):
                    raise Tolak("Daftar barang tidak terbaca. Muat ulang halaman lalu coba lagi.")
                svc.tambah_baris(po, varian_id=b.get("id"), jumlah=b.get("jumlah"), harga=b.get("harga"))
            svc.tambah_foto(request.user, po, request.FILES.getlist("foto"))
            if datang:
                isi = {"mode_ppn": d.get("mode_ppn") or po.mode_ppn, "tanggal_terima": d.get("tanggal"),
                       "tanggal_faktur": d.get("tanggal"), "nomor_faktur": d.get("nomor_faktur") or "",
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
                                              ("tempo", "Piutang pelanggan lewat jatuh tempo"))
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


@perlu_hp("piutang")
def piutang(request):
    from master.layanan import piutang as svc
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
        "tab": "", "p": p, "pembayaran": p.pembayaran.all(), "cara": svc.CARA_BAYAR.items(),
        "boleh_acc": "acc_piutang" in hak, "wa": svc.wa_url(p) if p.telepon else "",
        "barang": p.nota_manual.baris.all() if p.nota_manual_id else [],
        "lewat": bool(p.jatuh_tempo and p.jatuh_tempo < timezone.localdate() and not p.lunas)})
