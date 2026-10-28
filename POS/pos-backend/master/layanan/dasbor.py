"""Beranda master: widget yang bisa diatur per akun (urutan, lebar, tampil/sembunyi).

Angka penjualan memakai layanan laporan yang sama dengan menu Laporan, jadi hasilnya cocok:
- omzet = penjualan barang dikurangi uang yang dikembalikan (refund/retur/void) di hari itu;
- laba kotor = omzet - modal barang terjual (modal rata pada waktu nota);
- margin = laba kotor / omzet.
Laba bersih belum bisa dihitung: biaya operasional (gaji, listrik, dll.) belum dicatat di sistem.
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from master.layanan import laporan

# kunci: (judul, hak yang dibutuhkan atau None, lebar bawaan 1|2)
WIDGET = {
    "omzet_hari": ("Penjualan hari ini", "laporan", 1),
    "laba_hari": ("Laba kotor hari ini", "lihat_modal", 1),
    "bulan": ("Bulan ini", "laporan", 1),
    "perlu_acc": ("Perlu kamu periksa", None, 1),
    "minggu": ("Penjualan 7 hari terakhir", "laporan", 2),
    "jam": ("Penjualan per jam hari ini", "laporan", 2),
    "terlaris": ("Barang terlaris 7 hari", "laporan", 1),
    "kategori": ("Kategori terlaris 7 hari", "laporan", 1),
    "stok": ("Stok", "lihat_stok", 1),
    "hutang": ("Hutang ke supplier", "pembelian", 1),
    "data_barang": ("Data barang perlu diperbaiki", None, 2),
    "riwayat": ("Perubahan terakhir", "lihat_log", 2),
}
BAWAAN = ["omzet_hari", "laba_hari", "bulan", "perlu_acc", "minggu", "jam", "terlaris", "kategori",
          "stok", "hutang", "data_barang"]
HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]


def boleh(kunci, hak):
    syarat = WIDGET[kunci][1]
    return syarat is None or syarat in hak


def susunan(pref, hak):
    """[{"k", "lebar", "judul"}] yang tampil, sesuai pilihan akun (atau bawaan), hanya yang boleh dilihat."""
    simpan = pref.dasbor if pref and isinstance(pref.dasbor, list) and pref.dasbor else \
        [{"k": k, "lebar": WIDGET[k][2]} for k in BAWAAN]
    hasil, sudah = [], set()
    for w in simpan:
        k = w.get("k") if isinstance(w, dict) else None
        if k in WIDGET and k not in sudah and boleh(k, hak):
            sudah.add(k)
            hasil.append({"k": k, "lebar": 2 if w.get("lebar") == 2 else 1, "judul": WIDGET[k][0]})
    return hasil


def tersembunyi(tampil, hak):
    ada = {w["k"] for w in tampil}
    return [{"k": k, "judul": v[0]} for k, v in WIDGET.items() if k not in ada and boleh(k, hak)]


def bersihkan(data, hak):
    """Susunan kiriman browser -> yang disimpan."""
    hasil, sudah = [], set()
    for w in data if isinstance(data, list) else []:
        k = w.get("k") if isinstance(w, dict) else None
        if k in WIDGET and k not in sudah and boleh(k, hak):
            sudah.add(k)
            hasil.append({"k": k, "lebar": 2 if w.get("lebar") == 2 else 1})
    return hasil


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def _bersih_per_hari(nota, kembali):
    zona = timezone.get_current_timezone()
    omzet, laba, jumlah = defaultdict(int), defaultdict(int), defaultdict(int)
    for n in nota:
        d = n["transaction_time"].astimezone(zona).date()
        omzet[d] += n["item_amount"]
        laba[d] += n["item_amount"] - n["modal"]
        jumlah[d] += 1
    for k in kembali:
        d = k["waktu"].astimezone(zona).date()
        omzet[d] -= k["uang"]
        laba[d] -= k["uang"] - k["modal_kembali"]
    return omzet, laba, jumlah


def _batang(daftar):
    """Tambahkan 'tinggi' (0-100) untuk grafik batang."""
    puncak = max((max(d["nilai"], 0) for d in daftar), default=0)
    for d in daftar:
        d["tinggi"] = round(max(d["nilai"], 0) / puncak * 100) if puncak else 0
    return daftar


def penjualan(toko, hak):
    """Semua angka penjualan untuk widget, dari satu kali baca (awal bulan / 7 hari lalu s.d. hari ini)."""
    hari_ini = timezone.localdate()
    awal = min(hari_ini.replace(day=1), hari_ini - timedelta(days=7))
    mulai, selesai = laporan.rentang(awal, hari_ini)
    nota, baris = laporan.ambil(mulai, selesai, toko=toko)
    kembali = laporan.ambil_kembali(mulai, selesai, toko=toko)
    omzet, laba, jumlah = _bersih_per_hari(nota, kembali)
    zona = timezone.get_current_timezone()
    lihat_modal = "lihat_modal" in hak
    tanpa_modal = any(not n["modal_lengkap"] for n in nota)

    def ringkas(dari, sampai):
        hari = [dari + timedelta(days=i) for i in range((sampai - dari).days + 1)]
        o = sum(omzet[d] for d in hari)
        l = sum(laba[d] for d in hari)
        n = sum(jumlah[d] for d in hari)
        return {"omzet": o, "laba": l, "nota": n, "rata": round(o / n) if n else 0,
                "margin": round(l / o * 100, 1) if o else None}

    kemarin = hari_ini - timedelta(days=1)
    hasil = {
        "hari": ringkas(hari_ini, hari_ini),
        "kemarin": ringkas(kemarin, kemarin),
        "bulan": ringkas(hari_ini.replace(day=1), hari_ini),
        "minggu_total": ringkas(hari_ini - timedelta(days=6), hari_ini),
        "lihat_modal": lihat_modal,
        "tanpa_modal": tanpa_modal,
    }
    h, k = hasil["hari"]["omzet"], hasil["kemarin"]["omzet"]
    hasil["banding"] = round((h - k) / k * 100) if k > 0 else None

    hasil["minggu"] = _batang([
        {"label": HARI[d.weekday()], "sub": f"{d.day}/{d.month}", "nilai": omzet[d], "laba": laba[d],
         "nota": jumlah[d], "hari_ini": d == hari_ini}
        for d in (hari_ini - timedelta(days=6 - i) for i in range(7))])

    per_jam = defaultdict(int)
    jam_nota = defaultdict(int)
    for n in nota:
        waktu = n["transaction_time"].astimezone(zona)
        if waktu.date() == hari_ini and not n.get("manual"):
            per_jam[waktu.hour] += n["item_amount"]
            jam_nota[waktu.hour] += 1
    jam_ada = [j for j in per_jam if per_jam[j]]
    dari = min([7] + jam_ada)
    sampai = max([17] + jam_ada)
    hasil["jam"] = _batang([{"label": f"{j:02d}", "sub": "", "nilai": per_jam[j], "nota": jam_nota[j]}
                            for j in range(dari, sampai + 1)])
    hasil["jam_ramai"] = max(jam_ada, key=lambda j: per_jam[j]) if jam_ada else None

    tujuh = laporan.rentang(hari_ini - timedelta(days=6), hari_ini)[0]
    baris7 = [b for b in baris if b["nota"]["transaction_time"] >= tujuh]
    hasil["terlaris"] = laporan.kelompok_baris(baris7, "barang", "omzet")[:5]
    hasil["kategori"] = laporan.kelompok_baris(baris7, "kategori", "omzet")[:6]
    return hasil


def lain(request, hak, toko):
    """Angka non-penjualan: yang perlu diperiksa, stok, hutang."""
    from pos.models import Item
    from master.layanan import pembelian as svc_beli
    from master.layanan.toko import saring as saring_toko
    from master.models import Opname, Pembelian, PermintaanAmbil, Tiket

    perlu = []

    def tambah(label, n, url):
        if n:
            perlu.append({"label": label, "jumlah": n, "url": url})

    if "pembelian" in hak:
        tambah("Barang datang menunggu ACC", saring_toko(Pembelian.objects.filter(status="menunggu"), request).count(),
               "master:pembelian")
    if "opname" in hak:
        tambah("Opname menunggu ACC", saring_toko(Opname.objects.filter(status="diajukan"), request).count(),
               "master:opname")
    if "ubah_stok" in hak:
        from master.layanan.bukadus import jumlah_menunggu as dus_menunggu
        tambah("Buka dus menunggu ACC", dus_menunggu(), "master:buka_dus")
    if "kelola_lokasi" in hak:
        tambah("Tugas karyawan menunggu ACC", Tiket.objects.filter(status="dikirim").count(), "master:tiket")
    if "kelola_kasir" in hak:
        from master.layanan.nota_tinjau import jumlah_menunggu
        tambah("Nota perlu ACC", jumlah_menunggu(), "master:nota_tinjau")
    if "laporan" in hak or "ubah_stok" in hak:
        from master.layanan.retur_jual import jumlah_menunggu as retur_menunggu
        tambah("Retur belum diterima", retur_menunggu(), "master:retur")
    if "lihat_stok" in hak:
        tambah("Barang menunggu diambil dari gudang", PermintaanAmbil.objects.filter(status="menunggu").count(),
               "master:ambil")
    semua = Item.objects.all()
    return {
        "perlu": perlu,
        "stok_habis": semua.filter(in_stock__lte=0).count() if "lihat_stok" in hak else 0,
        "stok_menipis": semua.filter(stock_alert__isnull=False, in_stock__lte=F("stock_alert"), in_stock__gt=0).count()
        if "lihat_stok" in hak else 0,
        "hutang": svc_beli.ringkasan_hutang(toko=toko) if "pembelian" in hak else None,
    }


# ---------------------------------------------------------------------------
# Ringkasan satu periode (aplikasi HP: Hari ini / Kemarin / 7 hari / Bulan ini)
# ---------------------------------------------------------------------------

BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def periode(toko, hak, awal, akhir):
    """Angka penjualan untuk awal..akhir (tanggal lokal, inklusif), dibandingkan periode sebelumnya yang sama panjang."""
    panjang = (akhir - awal).days + 1
    sebelum_awal = awal - timedelta(days=panjang)
    mulai, selesai = laporan.rentang(sebelum_awal, akhir)
    nota, baris = laporan.ambil(mulai, selesai, toko=toko)
    kembali = laporan.ambil_kembali(mulai, selesai, toko=toko)
    omzet, laba, jumlah = _bersih_per_hari(nota, kembali)
    hari = [awal + timedelta(days=i) for i in range(panjang)]
    lalu = [sebelum_awal + timedelta(days=i) for i in range(panjang)]
    o, l, n = sum(omzet[d] for d in hari), sum(laba[d] for d in hari), sum(jumlah[d] for d in hari)
    o_lalu = sum(omzet[d] for d in lalu)
    lihat_modal = "lihat_modal" in hak
    hasil = {
        "omzet": o, "laba": l if lihat_modal else None, "nota": n, "rata": round(o / n) if n else 0,
        "margin": round(l / o * 100, 1) if lihat_modal and o else None, "lihat_modal": lihat_modal,
        "omzet_lalu": o_lalu, "banding": round((o - o_lalu) / o_lalu * 100) if o_lalu > 0 else None,
    }
    zona = timezone.get_current_timezone()
    mulai_ini = laporan.rentang(awal, akhir)[0]
    nota_ini = [x for x in nota if x["transaction_time"] >= mulai_ini]
    if panjang == 1:
        per_jam = defaultdict(int)
        for x in nota_ini:
            per_jam[x["transaction_time"].astimezone(zona).hour] += x["item_amount"]
        ada = [j for j in per_jam if per_jam[j]]
        hasil["grafik_judul"] = "Per jam"
        hasil["grafik"] = _batang([{"label": f"{j:02d}", "nilai": per_jam[j], "kini": False}
                                   for j in range(min([7] + ada), max([17] + ada) + 1)])
    else:
        hari_ini = timezone.localdate()
        hasil["grafik_judul"] = "Per hari"
        # Lebih dari seminggu: label tanggal hanya tiap 5 hari (dan hari terakhir) supaya tidak berdempet.
        hasil["grafik"] = _batang([{"label": HARI[d.weekday()] if panjang <= 7 else
                                    (str(d.day) if d.day == 1 or d.day % 5 == 0 or d == akhir else ""),
                                    "nilai": omzet[d], "kini": d == hari_ini} for d in hari])
    baris_ini = [b for b in baris if b["nota"]["transaction_time"] >= mulai_ini]
    hasil["terlaris"] = laporan.kelompok_baris(baris_ini, "barang", "omzet")[:5]
    return hasil
